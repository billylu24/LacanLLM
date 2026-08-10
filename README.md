# LacanLLM

> 用一张消费级显卡完成领域数据构建、Gemma 4 E2B QLoRA 微调、量化对比和可复现评估。

[English summary](#english-summary) · [技术学习指南](docs/TECHNICAL_GUIDE.md) · [工程设计](docs/ENGINEERING.md) · [Model Card](MODEL_CARD.md) · [Data Card](DATA_CARD.md)

## 30 秒看懂项目

LacanLLM 不是一个简单的模型调用 Demo，而是一条端到端的 ML 工程流水线：

```mermaid
flowchart LR
    A["原始文本"] --> B["清洗与段落重建"]
    B --> C["合成问答 SFT 数据"]
    C --> D["质检、去重与泄漏审计"]
    D --> E["4-bit / 8-bit QLoRA"]
    E --> F["断点续训与指标记录"]
    F --> G["基线、适配器与人工评估"]
```

核心工程点：

- **可复现数据**：固定 seed、答案指纹去重、优先按来源分组拆分；
- **训练正确性**：只在 assistant answer token 上计算 SFT loss；
- **长任务可靠性**：checkpoint、自动续训、失败日志和训练元数据；
- **实验可维护性**：实验矩阵集中在 [experiments.toml](configs/experiments.toml)，不再散落硬编码；
- **质量门禁**：pytest、Ruff、GitHub Actions 和独立 split audit。

## 当前状态

仓库中已有一轮历史实验：

| 历史实验 | 样本 | Epoch | Eval loss | 训练时间 | GPU |
|---|---:|---:|---:|---:|---|
| 4-bit NF4 | 5,000 | 1.5 | 3.0238 | 76.0 min | RTX 5070 |
| 8-bit | 5,000 | 1.5 | 3.0197 | 385.8 min | RTX 5070 |

在这台机器上，4-bit 的 eval loss 仅高约 **0.14%**，训练时间约为 8-bit 的 **1/5**。

当前泄漏门禁与 assistant-only loss 均已启用的 v2 实验：

| v2 实验 | 数据拆分 | Epoch | Eval loss | 训练时间 | 最终 step |
|---|---:|---:|---:|---:|---:|
| 4-bit NF4 | 2,700 / 300 | 1.0 | 3.3556 | 33.3 min | 675 |

v2 的验证 loss 从 0.25 epoch 的 3.4667 持续下降到 1 epoch 的 3.3556。由于 v2 更换了数据拆分并只计算 assistant token loss，**它的 loss 不能与历史实验直接横向比较**；模型效果仍需同一测试集上的 base-vs-adapter 生成评估。

> [!IMPORTANT]
> 历史数据缺少 `source_file`，并存在 25 条 train/validation 重复答案。因此这些结果适合展示工程过程和初步趋势，**不能作为最终模型效果结论**。`v2` 流程已修复精确去重、泄漏门禁和 assistant-only loss，但由于来源字段仍缺失，目前只能使用 seeded row split，不能排除语义或同源泄漏。

## 5 分钟运行工程检查

要求：Windows/Linux、Python 3.11、Git。运行单元测试不需要 GPU 或 Hugging Face token。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
pytest
ruff check src tests scripts
```

只生成 v2 数据拆分并运行泄漏审计，不启动训练：

```powershell
python scripts\run_experiments.py --prepare-only
python scripts\audit_data.py --strict
```

预期结果：`shared_instructions = 0`、`shared_outputs = 0`、`leakage_free = true`。

## 开始训练

Gemma 4 是 Hugging Face gated model。先在模型页面接受许可，再只在当前 shell 设置 token：

```powershell
$env:HF_TOKEN = "hf_..."
python scripts\run_experiments.py
```

训练配置见 [configs/experiments.toml](configs/experiments.toml)。训练会：

1. 生成约 2,700/300 的 train/validation split；
2. 跑 1 epoch 的 4-bit NF4 v2 实验；
3. 每 0.25 epoch 评估，每 50 optimizer steps 保存安全 checkpoint；
4. 中断后自动从最近的完整 checkpoint 恢复；
5. 保存 adapter、metrics、日志和环境元数据。

## 推理与对比评估

```powershell
python scripts\infer.py "Explain Lacan's account of desire." `
  --adapter-dir adapters\gemma4_e2b_4bit_nf4_v2_1ep_3000
```

评估脚本允许不传 adapter，因此可以建立 base model 基线：

```powershell
# Base model
python scripts\evaluate.py `
  --data-file data\gemma4_e2b_v2_3000_validation.jsonl `
  --run-name base `
  --output-file outputs\eval_base.jsonl

# 4-bit adapter
python scripts\evaluate.py `
  --data-file data\gemma4_e2b_v2_3000_validation.jsonl `
  --adapter-dir adapters\gemma4_e2b_4bit_nf4_v2_1ep_3000 `
  --run-name qlora_4bit `
  --output-file outputs\eval_4bit.jsonl
```

词汇 overlap 只用于快速诊断，不等于理论正确。正式结论还需领域人工盲评，详见 [技术学习指南](docs/TECHNICAL_GUIDE.md#8-如何正确评估领域-llm)。

## 仓库结构

```text
LacanLLM/
├── src/lacanllm/             # 可复用、可单测的核心逻辑
│   ├── data.py               # 去重、拆分、泄漏审计
│   ├── training.py           # assistant-only label masking
│   └── evaluation.py         # 透明的文本诊断指标
├── scripts/                  # 人可以直接运行的 CLI/编排层
├── configs/experiments.toml  # 实验单一配置源
├── tests/                    # CPU 单元测试
├── .github/workflows/ci.yml  # 自动质量门禁
├── data/                     # 数据与质量报告
├── experiments/              # 曲线、summary、manifest
├── adapters/                 # 历史 LoRA adapter
└── docs/                     # 学习与工程设计文档
```

建议的源码阅读顺序：

1. [configs/experiments.toml](configs/experiments.toml)
2. [scripts/run_experiments.py](scripts/run_experiments.py)
3. [src/lacanllm/data.py](src/lacanllm/data.py)
4. [scripts/run_pipeline.py](scripts/run_pipeline.py)
5. [src/lacanllm/training.py](src/lacanllm/training.py)
6. [scripts/evaluate.py](scripts/evaluate.py)

## 面试时怎么介绍

推荐用“问题—设计—证据—限制”四句话：

1. **问题**：在 12GB 消费级 GPU 上做拉康领域适配，并保证长时间训练可恢复；
2. **设计**：用 QLoRA、assistant-only loss、确定性数据管道和配置驱动实验；
3. **证据**：v2 在 675 steps 内将 held-out loss 从 3.4667 降至 3.3556，并保存数据/adapter 哈希；
4. **限制**：发现历史 split 泄漏后增加自动审计，模型能力结论仍需 base baseline 和人工盲评。

主动解释限制通常比只报一个 loss 更能体现工程判断力。

## Reproducibility and limitations

- v2 默认使用 seed 3407、最大长度 1024、梯度累积 4；
- SFT 问题由模型合成，回答来自源段落，不是专家人工标注 benchmark；
- MIT License 仅覆盖本仓库原创代码，不自动授予第三方文本、Gemma 基座模型或 adapter 的再许可；
- 发布或重新分发数据前，必须完成 [Data Card](DATA_CARD.md) 中的来源与授权核查；
- 本项目用于研究和教育，不应用于心理健康诊断或治疗建议。

## English summary

LacanLLM is a reproducible domain-adaptation project for Gemma 4 E2B. It covers text cleaning, synthetic SFT generation, deterministic deduplication and split auditing, 4/8-bit QLoRA, resumable training, metadata capture, and baseline-aware evaluation. The repository separates testable core modules under `src/lacanllm` from human-facing CLI orchestration under `scripts`.

Historical adapters are included for reproducibility, but their split contained 25 duplicated answers across train and validation. The completed 3,000-example v2 run reports zero exact cross-split instruction/output overlap, masks user-prompt labels, and reached validation loss 3.3556 after 675 steps. Because provenance remains missing, the split falls back to deterministic row-level isolation and still requires an independent base-vs-adapter evaluation.
