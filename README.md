# LacanLLM Pipeline v3

用于从清洗后的拉康语料生成、审核并封装 QA 数据集的可恢复 GPU 流水线。当前
`v3` 分支包含完整代码、配置、测试以及不可变源语料，适合直接克隆到 Linux GPU
机器运行。

## 当前生产方案

- 模型：`google/gemma-4-12B-it`
- 固定 revision：`707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7`
- 推理：NF4 4-bit、double quantization、BF16、batch size 1
- 生成与审核：同一模型；产物会明确标记为 self-judged
- 输入：`data/source/cleaned_corpus/paragraphs.jsonl`，32,028 行
- 输入 SHA-256：`721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da`
- 所有派生产物：`data/pipeline_v3/`（已被 Git 忽略）

已有实机 smoke 记录为峰值分配显存 7.53 GiB。默认配置允许模型最多使用
10 GiB GPU 显存和 12 GiB CPU 内存，并允许自动 CPU offload。建议远程机器至少：

- NVIDIA GPU，12 GiB 显存；16 GiB 或以上更稳妥
- 20 GiB 系统内存；24 GiB 或以上更稳妥
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

## Hugging Face 模型授权

Gemma 是 gated model。运行前需要在 Hugging Face 页面接受模型许可，并让远程机
登录有访问权的账号：

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
  --config configs/pipeline_v3/smoke_gemma4_12b.json \
  smoke
```

查看结果：

```bash
lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/smoke_gemma4_12b.json \
  status

sed -n '1,240p' data/pipeline_v3/smoke/gemma4_12b_qa_only/reports/smoke.json
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
`data/pipeline_v3/` 路径。完整的数据决策与审计约束见
[`docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md`](docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md)。

流水线产物默认不提交到 Git。需要迁移运行中的任务时，应单独同步
`data/pipeline_v3/` 和日志到持久磁盘；代码更新继续通过 `v3` 分支完成。
