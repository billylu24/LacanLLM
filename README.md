# LacanLLM Pipeline v3

用于从清洗后的拉康语料生成、审核并封装 QA 数据集的可恢复 GPU 流水线。当前
`v3` 分支包含完整代码、配置、测试以及不可变源语料，适合直接克隆到 Linux GPU
机器运行。

## 当前生产方案

- 模型：`Qwen/Qwen3.8-27B`
- 固定 revision：`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`
- 推理：NF4 4-bit、double quantization、BF16、batch size 1
- 生成与审核：同一模型；产物会明确标记为 self-judged
- 输入：`data/source/cleaned_corpus/paragraphs.jsonl`，32,028 行
- 输入 SHA-256：`721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da`
- 所有派生产物：`data/pipeline_v3/`（完整纳入 Git，便于跨设备恢复）

已有实机 smoke 记录为峰值分配显存约 17.14 GiB。默认配置允许模型最多使用
28 GiB GPU 显存和 64 GiB CPU 内存，并允许自动 CPU offload。建议远程机器至少：

- NVIDIA GPU，24 GiB 显存；32 GiB 或以上更稳妥
- 64 GiB 系统内存
- 50 GiB 可用磁盘（模型缓存、offload 和流水线产物）
- 较新的 NVIDIA 驱动，以及 Python 3.11 或 3.12

## 远程 GPU 快速部署

以下命令适用于 Ubuntu / Debian 类 Linux。系统只需预装 NVIDIA 驱动、Git 和
Python；PyTorch wheel 会带所需的 CUDA 用户态运行库，不需要单独安装完整 CUDA
Toolkit。

```bash
git clone --branch v3 --single-branch https://github.com/billylu24/LacanLLM.git
cd LacanLLM

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

先检查驱动、PyTorch 和 GPU 是否正常：

```bash
nvidia-smi
python - <<'PY'
import torch

print("torch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("CUDA runtime:", torch.version.cuda)
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY
```

如果 PyPI 为机器选到的 PyTorch wheel 与驱动不兼容，请按
[PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/)重装对应 CUDA wheel，
然后再次运行上面的自检。

## Hugging Face 登录

如模型下载需要认证，让远程机登录有访问权的 Hugging Face 账号：

```bash
hf auth login
```

无交互部署也可以通过机器的 secret 管理器注入 `HF_TOKEN`。不要把 token 写进
README、配置、shell history 或提交到 Git。

模型默认缓存在 `~/.cache/huggingface`。如果系统盘空间有限，可改到数据盘：

```bash
export HF_HOME=/path/to/large-disk/huggingface
mkdir -p "$HF_HOME"
```

## 先跑 smoke

smoke 会生成和审核 6 条记录，并再次执行相同阶段验证断点恢复与去重。首次运行会
下载模型：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/smoke_qwen3_8_27b.json \
  smoke
```

查看结果：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/smoke_qwen3_8_27b.json \
  status

sed -n '1,240p' data/pipeline_v3/smoke/qwen3_8_27b_qa_only/reports/smoke.json
```

已验证的 smoke 指标和行为见
[`docs/PIPELINE_V3_GEMMA4_SMOKE.md`](docs/PIPELINE_V3_GEMMA4_SMOKE.md)。

## 启动生产任务

建议在 `tmux` 中运行，避免 SSH 断开终止任务：

```bash
tmux new -s lacan-v3
source .venv/bin/activate

lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/production.json \
  run 2>&1 | tee pipeline-v3-production.log
```

`run` 会依次执行 preflight、clean、split、queue、generate、hard-filter、deduplicate、
judge、select、seal 和 audit。生成与审核记录逐行落盘；使用完全相同的配置重新执行
同一命令时，会按确定性 candidate ID 跳过已完成记录，不会重复写入。

查看生产进度：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/production.json \
  status
```

如果远程机显存或内存不同，复制生产配置后修改以下字段，再从一开始始终使用这份
配置；配置内容参与 hash，途中更换配置会被流水线拒绝，以防混合不同实验产物。

```json
"quantization": {
  "device_map": "auto",
  "max_gpu_memory": "10GiB",
  "max_cpu_memory": "12GiB"
}
```

示例：24 GiB GPU 可将 `max_gpu_memory` 调为 `22GiB`；不要占满显存，要给 CUDA
上下文和临时张量留出余量。

## 常用诊断

仅验证源语料、运行环境和 Hugging Face revision，不执行生成：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/production.json \
  preflight
```

无网络时只做本地预检：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/production.json \
  --no-remote-preflight \
  preflight
```

本地编排测试（不会加载模型）：

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check .
```

## 数据与版本纪律

`data/source/cleaned_corpus/paragraphs.jsonl` 是只读输入快照，不应原地修改。新生成的
队列、模型输出、判断、数据集、manifest 和报告必须继续写入版本化的
`data/pipeline_v3/` 路径。当前仓库有意完整版本化这些产物，包括各阶段中间记录，
因此在其他设备 clone 后可以审计或恢复任意阶段。完整的数据决策与审计约束见
[`docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md`](docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md)。

## QLoRA 训练

仓库已包含本次生产选出的 2,000 条 Train 和 250 条 Validation。训练入口使用固定
revision、NF4 4-bit、BF16 和 completion-only loss；Test 在最终配置锁定前保持封存。
新 GPU 设备安装训练依赖后，先执行独立的一步 smoke：

```bash
python -m pip install -r requirements-training.txt
python scripts/train_qlora.py --config configs/training/smoke_r8_1step.json
```

中断后从该实验的最新 checkpoint 恢复：

```bash
python scripts/train_qlora.py \
  --config configs/training/smoke_r8_1step.json \
  --resume-from-checkpoint
```

一步 smoke 验收后才能复制正式配置并启动 rank 实验。每组输出使用独立的
`artifacts/experiments/<experiment_name>/`；程序拒绝无意覆盖已有实验。Phase 1 审计结论、
Phase 2 命令与验收方法见
[`docs/QLORA_PHASE_1_2_PLAN.md`](docs/QLORA_PHASE_1_2_PLAN.md)。Test 封存哈希见
`data/pipeline_v3/production/09_seal/test_seal.json`。

32-step Phase 3 已验证持续训练、未完成 checkpoint 恢复、多次 Validation、显存记录和
adapter 重新加载；结果与限制见
[`docs/QLORA_PHASE_3_REPORT.md`](docs/QLORA_PHASE_3_REPORT.md)。Phase 4 Base baseline 使用完整
250 条 Validation，得到 completion-only loss `0.67864`；固定 16 条生成样本的 mean token-F1
为 `0.56849`。Phase 5 使用 `scripts/run_rank_queue.py` 串行运行 r=8/16/32，并在每组结束后
生成同口径 Validation 对比。Phase 5 已完成：r=8 在完整 Validation loss（`0.14189`）和固定
16 条生成样本 mean token-F1（`0.77090`）上均为最佳，故选为下一阶段配置。完整实验结果、
选择依据和二进制 adapter 发布限制见 [`docs/QLORA_PHASE_5_RANK_REPORT.md`](docs/QLORA_PHASE_5_RANK_REPORT.md)。
Test 仍保持封存。
