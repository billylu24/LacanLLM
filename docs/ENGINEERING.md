# Engineering design

## Design goal

The repository optimizes for two readers: an interviewer who needs a five-minute architectural overview, and a learner who needs to revisit individual concepts months later. Human-facing commands live in `scripts/`; deterministic and testable rules live in `src/lacanllm/`.

## Boundaries

| Layer | Responsibility | Must not do |
|---|---|---|
| `configs/` | Declare experiment choices | Execute training |
| `scripts/` | Parse CLI arguments and orchestrate I/O | Hide reusable rules in large nested functions |
| `src/lacanllm/` | Implement deterministic domain logic | Download models at import time |
| `tests/` | Prove invariants on tiny CPU fixtures | Require CUDA or gated assets |
| `data`, `adapters`, `outputs` | Hold artifacts | Act as source code dependencies |

## Enforced invariants

- Duplicate normalized answers are removed before train/validation splitting.
- Exact normalized instructions and outputs may not cross the split boundary.
- Whole sources are isolated when provenance is complete and at least two sources exist.
- Missing provenance is reported explicitly instead of being presented as a grouped split.
- User/prompt tokens and padding tokens use label `-100` during SFT.
- New v2 experiments never overwrite historical adapters or datasets.
- CI is CPU-only and must finish without downloading Gemma.

## Reproducibility contract

An experiment should be reconstructable from its Git commit, TOML config, data hashes, base-model identifier/revision, dependency versions, seed, GPU/CUDA metadata, metrics JSONL, and final adapter. The current metadata captures most runtime settings; model revision and published artifact checksums remain roadmap items.

## Artifact policy

Source code belongs in Git. Large adapters should move to Hugging Face Hub or GitHub Releases and be referenced by immutable revision plus SHA-256. Checkpoints and logs are local/ephemeral. Data may only be redistributed when its provenance and rights are documented in `DATA_CARD.md`.

## Historical-result policy

Existing 2026 adapters are retained as historical artifacts. Their data split had exact answer overlap, so they cannot pass the v2 leakage gate. They should not be silently relabeled as v2 results. Any table comparing v2 models must be regenerated from v2 metadata.

