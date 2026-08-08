# LacanLLM

一个面向 Jacques Lacan 文本的实验性 SFT/QLoRA 项目。当前路线是：

```text
原始 TXT -> 规则清洗 -> 模型生成问题 -> 自动质检 -> QLoRA -> 评估/推理
```

项目固定使用 `google/gemma-4-E2B-it` 作为问题生成、训练和推理的基础模型，避免不同模型之间的格式和能力差异。Gemma 4 需要使用 `AutoProcessor` 和 `AutoModelForMultimodalLM`。

## 环境

项目按 Windows + Python 3.11 + NVIDIA GPU 配置。建议使用已有虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

运行 gated Hugging Face 模型前，必须在当前 PowerShell 会话设置 token：

```powershell
$env:HF_TOKEN = "hf_..."
```

不要把 token 写入代码、`.env` 或 Git。

## 数据处理

1. 清洗 46 个原始文本文件：

```powershell
python scripts\clean_lacan.py
```

输出：

- `data\lacan_full_corpus.txt`
- `data\lacan_dataset.jsonl`

清洗会保留 `source_file`、`paragraph_index`、`char_count` 和 `mojibake_score` 元数据，并过滤明显版权页、目录、脚注碎片和编码损坏段落。

2. 生成 synthetic SFT 问答：

```powershell
python scripts\generate_sft_gemma.py --model-id google/gemma-4-E2B-it
```

默认先写入 `data\lacan_sft_pairs_raw.jsonl`，支持中断后继续。

3. 质检并统一 schema。正式运行至少检查 300 条样本：

```powershell
python scripts\quality_check.py --sample-size 300
```

最终训练文件为 `data\lacan_sft_pairs.jsonl`，每行格式为：

```json
{"schema_version":1,"instruction":"...","input":"","output":"...","source_file":"...","paragraph_index":0,"char_count":500}
```

质检报告写入 `data\quality_report.json`。当前过滤规则会去掉乱码、出版信息、拒答文本、过短/过长答案和重复问答。由于问题由模型生成，它们仍然属于 synthetic data，建议人工复核抽样。

## 小规模 QLoRA 训练

先只准备数据，不加载模型：

```powershell
python scripts\run_pipeline.py --prepare-only --target-count 1000
```

然后用 20 个 step 做训练冒烟测试：

```powershell
python scripts\run_pipeline.py `
  --skip-prepare `
  --model-id google/gemma-4-E2B-it `
  --max-seq-length 1024 `
  --max-steps 20 `
  --target-count 1000 `
  --logging-steps 1 `
  --eval-steps 10 `
  --save-steps 10 `
  --output-dir adapters\lacan_lora_smoke `
  --checkpoint-dir outputs\smoke
```

正式训练前建议先确认显存、模型许可和 token 可用。Gemma 4 E2B 比 E4B 更适合 12GB 显存，但训练仍需从 `--max-seq-length 1024 --max-steps 10` 的小实验开始。训练完成后，LoRA adapter 会保存到 `adapters\lacan_lora`，checkpoint 保存到 `outputs`。

要运行量化/epoch 对照实验：

```powershell
python scripts\run_experiments.py
python scripts\summarize_experiments.py
```

每组实验的日志在 `experiments\logs`，汇总在 `experiments\summary.json`。当前已验证 4-bit NF4 和 8-bit；RTX 5070 12GB 上的小样本结果中，4-bit NF4 + 1 epoch 的 eval loss 最低。

## 评估与推理

单条推理：

```powershell
python scripts\infer.py "Explain Lacan's account of desire." --adapter-dir adapters\lacan_lora_smoke
```

在验证集上生成并保存结果：

```powershell
python scripts\evaluate.py `
  --adapter-dir adapters\lacan_lora_smoke `
  --data-file data\lacan_validation_data.jsonl `
  --limit 50
```

评估脚本当前输出预测文本和简单的 reference token overlap。这个指标只能做回归检查，不能替代 Lacanian 专业人工评审。

## 当前状态与限制

- 规则清洗和数据 schema 已统一；数据文件属于本地生成产物，不纳入 Git。
- 完整 SFT 生成和 QLoRA 训练依赖 `HF_TOKEN` 以及 Hugging Face 模型许可。
- 当前没有网页/API 部署。
- 训练数据来自模型反向生成问题，不能宣称为人工标注问答。
- 评估仍需加入独立人工标注集，以判断理论准确性、引用忠实度和幻觉率。

## 连续 1.5 epoch 实验

`scripts/run_experiments.py` 现在只启动两个连续实验：4-bit NF4 和 8-bit。每个实验都从基础模型的 0 epoch 开始，训练到 1.5 epoch，并在 0.25、0.5、0.75、1.0、1.25、1.5 epoch 保存 checkpoint、验证 loss 和记录文件。

```powershell
python scripts\run_experiments.py
python scripts\summarize_experiments.py
python scripts\plot_experiments.py
```

曲线数据保存在 `experiments\*_metrics.jsonl`，图表保存在 `experiments\quantization_epoch_comparison.png`。每个量化设置只加载一次基础模型；不同 epoch 点不是独立重启的实验。
