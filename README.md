# LacanLLM v3

This branch is the clean starting point for Pipeline v3. Previous data
pipelines, generated QA datasets, adapters, training code, and experiment
artifacts remain available from the `main` branch and its history; they are not
part of the v3 working tree.

## Preserved source

The only retained data artifact is the conservatively cleaned source corpus:

- path: `data/source/cleaned_corpus/paragraphs.jsonl`
- rows: 32,028
- SHA-256: `721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da`

This file is an immutable input snapshot. Pipeline v3 must write all new
queues, generations, judgments, datasets, manifests, and reports to new
versioned paths and must never edit this source file in place.

## Pipeline v3 CLI

The implementation is exposed as `lacanllm-pipeline-v3` and writes only below
`data/pipeline_v3/`. Production uses the pinned Gemma 4 12B revision for both
generation and review under an explicit `allow_self_judge` override. Audit
reports therefore identify this run as self-judged rather than independently
cross-model reviewed.

```bash
conda run -n lacanllm lacanllm-pipeline-v3 \
  --config configs/pipeline_v3/smoke_gemma4_12b.json smoke
```

Each generation and judgment is flushed as one JSONL record. Re-running a
stage resumes by deterministic candidate ID, rejects stale configuration
hashes, and writes no duplicate rows. The generator returns only `question`
and `answer`; the pipeline joins those fields to context and provenance, and
the Judge evaluates that three-part record. Malformed model output is logged as
a rejection and skipped without repair or batch termination. The complete decision record is
[`docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md`](docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md).
