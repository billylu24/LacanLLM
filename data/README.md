# LacanLLM Pipeline v2 Data Runbook

## What is active

The old `evaluation_v1` and `sft_v2` data are archived at
`legacy/pipeline_v1_20260819/`. They are not read by Pipeline v2.

Pipeline v2 reads only `legacy/data/lacan_dataset.jsonl`, creates a traceable
`corpus_v2`, then generates every Train, Validation, Test, and Challenge item
from scratch. E2B generates candidates and E4B performs two blind semantic
judge passes.

The complete frozen thresholds and every execution event live together in
[`PIPELINE_V2_RECORD.md`](PIPELINE_V2_RECORD.md). That file is the authoritative
human-readable record; `configs/data/pipeline_v2.json` is the executable form.

## Data flow

```text
legacy raw corpus
  -> conservative clean corpus + complete rejection ledger
  -> immutable 35/5/5 anonymous-file split manifest
  -> type-conditioned generation queues
  -> E2B JSON QA generation
  -> hard filters and exact quote offsets
  -> split-local exact/SimHash duplicate clusters
  -> E4B rubric pass + E4B adversarial pass
  -> strict 2/2 automated consensus
  -> benchmark-first global deduplication
  -> source-aware fixed-quota selection
  -> SFT 500 + Validation 250 + sealed Test 250 + Challenge 100
  -> final hash and contract audit
```

## Artifact layout

```text
data/processed/corpus_v2/paragraphs.jsonl
data/interim/pipeline_v2/<stage>/<split>.jsonl
data/processed/sft_v3/train.jsonl
data/processed/benchmark_v2/{validation,test,challenge}.jsonl
data/manifests/pipeline_v2/{splits,test_seal}.json
data/reports/pipeline_v2/audit.json
data/PIPELINE_V2_RECORD.md
```

Intermediate rows retain raw model output, parser errors, hard-filter metrics,
all rejection reasons, duplicate cluster metadata, both raw Judge outputs,
structured Judge decisions, provenance, evidence offsets, prompt versions,
model IDs, timestamps, and configuration hashes.

## Safe execution order

```powershell
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 clean
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 split

foreach ($split in @('train','validation','test','challenge')) {
  .\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 generate --split $split
  .\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 hard-filter --split $split
}

.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 deduplicate

foreach ($split in @('train','validation','test','challenge')) {
  .\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 judge --split $split --pass rubric
  .\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 judge --split $split --pass adversarial
}

.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 select
.\.venv\Scripts\python.exe -m lacanllm.data.pipeline_v2 audit
```

The unified `run` command performs the same stages and automatically adds
type-specific refill batches when consensus-qualified candidates cannot fill a
quota. All generation and judging commands resume by candidate ID and reject
artifacts carrying a different configuration hash.

## Reading status

`status` reports, for every split, the number queued, generated, hard-filtered,
deduplicated, rubric-judged, adversarially judged, consensus-qualified, final,
and targeted. A nonzero queue is not equivalent to usable training data; only
the final audited counts are deliverables.

## Benchmark interpretation

Validation, Test, and Challenge are automated silver benchmarks. They are
suitable for reproducible engineering comparisons within this project, but not
for claims of expert-level Lacanian accuracy. The Test file is considered
sealed only when its manifest SHA-256 matches and the full final audit passes.
