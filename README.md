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

## Next step

The current design record is
[`docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md`](docs/DATA_PIPELINE_V3_SIMPLIFIED_PLAN.md).
No v3 generation implementation or derived data exists yet.
