# LacanLLM

LacanLLM is an auditable data, training, and evaluation project. The active
implementation is the fully automated Pipeline v2. Previous code, generated
data, adapters, and experiment artifacts remain recoverable under `legacy/`;
the immediately preceding rule-only dataset is under
`legacy/pipeline_v1_20260819/`

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

## Reproducibility and documentation

The canonical rules and append-only execution ledger are in
[`data/PIPELINE_V2_RECORD.md`](data/PIPELINE_V2_RECORD.md). The detailed data
runbook is in [`data/README.md`](data/README.md), and all numerical settings are
versioned in `configs/data/pipeline_v2.json`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```
