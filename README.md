# LacanLLM

LacanLLM is an auditable data, training, and evaluation project. The active
implementation is the fully automated Pipeline v2. Previous code, generated
data, adapters, and experiment artifacts remain recoverable under `legacy/`;
the immediately preceding rule-only dataset is under
`legacy/pipeline_v1_20260819/`.

## Active data pipeline

Pipeline v2 creates four source-grounded datasets from 51,427 legacy corpus
paragraphs:

- `sft_v3`: 500 training QA pairs;
- `benchmark_v2/validation`: 250 silver examples;
- `benchmark_v2/test`: 250 sealed silver examples;
- `benchmark_v2/challenge`: 100 automated challenge examples.

The active reduced final release is selected only from existing dual-judge
consensus rows. It contains 500 Train, 200 Validation, 200 sealed Test, and 70
Challenge examples. The `other` and `ambiguous` question types are intentionally
excluded. The original Pipeline v2 production configuration and provenance hash
remain unchanged; `configs/data/final_release.json` records the separate final
selection policy.

The pipeline performs conservative corpus cleaning, anonymous source-file
isolation, type-conditioned Gemma 4 E2B generation, deterministic hard filters,
exact and SimHash deduplication, two blind Gemma 4 E4B judge passes, fixed type
quotas, global benchmark-first deduplication, and final artifact auditing.

Because no expert performs final review, all benchmark artifacts are explicitly
labelled **silver**, not gold. Anonymous filenames also prevent verification
that two files do not originate from the same underlying work.

## Commands

Create and activate the Miniconda environment from the repository root:

```bash
conda env create -f environment.yml
conda activate lacanllm
python -m pytest -q
python -m lacanllm.data.pipeline_v2 status
```

If the environment already exists, synchronize it after dependency changes with
`conda env update -f environment.yml --prune`.

On machines where Conda blocks configured Anaconda default channels pending
Terms-of-Service acceptance, use the conda-forge-only equivalent:

```bash
conda create --name lacanllm --override-channels --channel conda-forge python=3.12 'pip>=25' -y
conda run --name lacanllm python -m pip install -e '.[dev,models]'
conda activate lacanllm
```

```powershell
# Deterministic preparation
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 clean
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 split

# Resumable per-stage model work
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 generate --split train
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 hard-filter --split train
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 deduplicate
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 judge --split train --pass rubric
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 judge --split train --pass adversarial
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 select
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 audit

# Or run the complete refill-aware workflow
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 run

# Read-only progress snapshot
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 status

# Build the reduced, fully audited final release without loading a model
.\.venv\Scripts\python.exe -m lacanllm.data.release
```

Generation and judging use local CUDA. Set `HF_TOKEN` if Hugging Face requires
authentication for model retrieval. E4B uses 4-bit NF4 and `device_map=auto`,
which permits CPU offload rather than silently replacing the configured judge.

## QLoRA experiments

Training uses an isolated, pinned Unsloth environment so it does not replace
the newer Torch and Transformers versions used by the data pipeline:

```bash
conda env create -f environment-unsloth.yml
conda activate lacanllm-unsloth
lacanllm-experiments preflight
lacanllm-experiments smoke
```

If configured Anaconda channels are blocked by Terms-of-Service prompts, create
the same environment through conda-forge and then install the locked packages:

```bash
conda create --name lacanllm-unsloth --override-channels --channel conda-forge python=3.12 'pip>=25' -y
conda run --name lacanllm-unsloth python -m pip install -e '.[dev,models,training]' \
  'unsloth==2026.8.19' 'torch==2.11.0' 'torchvision==0.26.0' \
  'transformers==5.5.0' 'trl==0.24.0' 'peft==0.20.0' 'datasets==4.3.0'
```

Run backend measurements in separate processes, then launch the resumable
16-trial search. The search covers LoRA rank/alpha/dropout, effective batch,
warmup, scheduler, weight decay, and learning rate:

```bash
lacanllm-experiments benchmark-backend --backend native
lacanllm-experiments benchmark-backend --backend unsloth
lacanllm-experiments search --trials 16
lacanllm-experiments status
lacanllm-experiments evaluate-top --count 3
lacanllm-experiments evaluate-test
```

`evaluate-top` locks the winner using Validation and Challenge. Only then can
`evaluate-test` verify and open the sealed Test artifact, and a completion
marker prevents a second Test run. The full protocol and selection rules are in
[`docs/QLORA_UNSLOTH_EXPERIMENT_PLAN.md`](docs/QLORA_UNSLOTH_EXPERIMENT_PLAN.md).

## Reproducibility and documentation

The canonical rules and append-only execution ledger are in
[`data/PIPELINE_V2_RECORD.md`](data/PIPELINE_V2_RECORD.md). The detailed data
runbook is in [`data/README.md`](data/README.md), and all numerical settings are
versioned in `configs/data/pipeline_v2.json`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```
