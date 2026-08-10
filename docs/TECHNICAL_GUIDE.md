# LacanLLM 技术学习指南

这份文档写给未来重新打开项目的你。目标不是背 API，而是理解每个技术选择解决了什么问题。

## 1. 项目在解决什么问题

基础模型懂通用语言，但不一定稳定掌握拉康术语、论证方式和文本语境。领域适配的目标是让模型在保留通用能力的同时，更常生成符合目标语料风格与知识分布的回答。

本项目使用监督微调（SFT）：输入一个问题，要求模型学习目标回答。这里的问题由模型根据段落反向生成，回答是源文本段落，所以它属于 synthetic instruction data，而不是专家标注问答。

## 2. 数据为什么使用 JSONL

JSONL 每一行是一个独立 JSON 对象：

```json
{"instruction": "What is desire?", "input": "", "output": "...", "source_file": "seminar-01.txt"}
```

优点：可以流式读取、断点追加、坏行定位和逐行 diff。关键字段：

- `instruction`：user 问题；
- `output`：assistant 应学习的回答；
- `source_file` / `paragraph_index`：数据血缘，用于追踪与防泄漏；
- `schema_version`：未来改变格式时保持兼容。

阅读入口：[clean_lacan.py](../scripts/clean_lacan.py) → [generate_sft_gemma.py](../scripts/generate_sft_gemma.py) → [quality_check.py](../scripts/quality_check.py)。

## 3. 为什么去重必须发生在拆分之前

如果同一个答案的两个副本先随机拆分，它们可能分别进入训练集和验证集。模型训练时已经见过验证答案，验证 loss 就不再代表泛化。

v2 的顺序是：

```text
质量排序 → normalized output hash 去重 → 选 Top N → 拆分 → overlap audit → 写文件
```

[data.py](../src/lacanllm/data.py) 使用 SHA-256 作为稳定指纹。SHA-256 在这里不是为了安全加密，而是为了低成本比较长文本。若有至少两个完整来源，拆分以整个 `source_file` 为组；旧数据没有来源时才退回固定 seed 的行级拆分。

最后的 `assert_no_content_leakage` 是 fail-fast 门禁：与其训练数小时后发现结果无效，不如在训练前立即失败。

## 4. Token、chat template 与 labels

模型不直接处理字符串，而是处理 token id。Chat model 还要求特殊模板，例如 user/assistant role token。项目调用模型自带的 `apply_chat_template`，避免手写与 Gemma 不兼容的特殊 token。

SFT 中：

```text
input_ids = [user role, question, assistant role, answer]
labels    = [-100,      -100,    -100,          answer token ids]
```

PyTorch/Hugging Face 的交叉熵会忽略 label `-100`。因此模型只因 assistant 回答预测错误而受罚，不会浪费容量学习复述用户问题。这段独立逻辑在 [training.py](../src/lacanllm/training.py)，对应单测在 [test_training.py](../tests/test_training.py)。

## 5. LoRA 与 QLoRA

完整微调会更新基础模型所有权重，显存和存储成本很高。LoRA 冻结原权重，在线性层旁增加两个低秩矩阵：

```text
W' = W + scale × B × A
```

若 rank `r` 远小于原矩阵维度，需要训练的参数就会大幅减少。`lora_r` 控制容量，`lora_alpha` 控制更新缩放，`lora_dropout` 用于正则化。

QLoRA 进一步把冻结的基础模型以 4-bit 或 8-bit 载入，LoRA 参数仍以较高精度训练。NF4 是针对近似正态分布权重设计的 4-bit 表示。项目使用 bitsandbytes 完成量化、PEFT 注入 LoRA、Transformers Trainer 执行训练。

## 6. 为什么需要梯度累积和 checkpoint

12GB GPU 一次通常只能容纳 batch size 1。梯度累积 4 次后再更新参数，得到有效 batch size 4，同时保持单步显存较低。

长训练可能遇到重启、显存错误或系统更新。项目每 50 optimizer steps 保存 checkpoint，并验证 `trainer_state.json` 存在后才将其视为完整 checkpoint。`--auto-resume` 会选择步数最大的完整目录。

注意区分：

- checkpoint：包含继续训练所需状态，体积大且是临时产物；
- adapter：最终部署所需 LoRA 权重，体积小；
- base model：运行时从 Hugging Face 获取，不应提交到本仓库。

## 7. 4-bit 与 8-bit 实验怎么解释

历史单次实验中：

- 4-bit：loss 3.0238，约 76 分钟；
- 8-bit：loss 3.0197，约 386 分钟。

不能因为 8-bit loss 数字略低就直接说它更好。差距约 0.0041，且只有一个 seed；同时 8-bit 慢约 5.1 倍。当前更合理的结论是：“在这次硬件和配置下，4-bit 位于更好的成本—效果 Pareto 点；是否稳定需要多 seed 验证。”

## 8. 如何正确评估领域 LLM

建议使用三层评估：

1. **训练诊断**：held-out loss，判断优化是否正常；
2. **自动诊断**：词汇 precision/recall/F1、回答长度、重复率；
3. **领域人工盲评**：理论准确性、问题相关性、幻觉、引用忠实度，且隐藏模型名称。

最关键的对照是 base model。没有 base baseline，只能证明训练 loss 变化，不能证明 adapter 带来净提升。

建议准备固定 100 题：70 题用于日常开发，30 题作为最终 held-out test。不要根据 test 题反复调参数，否则 test 也会被间接过拟合。

## 9. 各技术栈分别负责什么

| 技术 | 在项目中的责任 |
|---|---|
| PyTorch | tensor、CUDA、混合精度与反向传播基础 |
| Transformers | Gemma 加载、chat template、Trainer、checkpoint |
| PEFT | LoRA 配置、注入和 adapter 保存/加载 |
| bitsandbytes | 4-bit NF4、8-bit 量化和低内存优化器 |
| Datasets | JSONL 数据集加载与 map tokenization |
| pytest | 验证去重、拆分、masking 和指标逻辑 |
| Ruff | 静态错误和 import 规范检查 |
| GitHub Actions | 在每次 push/PR 自动执行质量门禁 |
| TOML | 将实验选择从代码中分离，便于审查与复现 |

## 10. 推荐复习路线

1. 先读 README 的架构图和状态说明；
2. 手工运行 `pytest -v`，逐个对应到 `src/lacanllm`；
3. 用 20 行虚拟 JSONL 运行 prepare-only，观察 split audit；
4. 阅读 `run_pipeline.py --help`，理解每组超参数；
5. 画出一次训练从文本到 loss 的 tensor 流程；
6. 最后再阅读 PEFT、bitsandbytes 和 Gemma 官方文档。

能不用源码说清楚“为什么去重在拆分前、为什么 label 是 -100、为什么 checkpoint 不等于 adapter”，就已经掌握了项目最重要的工程与 ML 核心。

