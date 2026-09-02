# QLoRA Phase 5 — Rank selection

Status: completed on 2026-09-02. The Test split remains sealed and was not used.

## Controlled experiment

Three QLoRA ranks (`r=8`, `r=16`, and `r=32`) were trained serially from the
same Qwen/Qwen3.8-27B revision (`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`).
All runs used the same seed (3407), 2,000 Train rows, 250 Validation rows,
three epochs / 750 optimizer steps, NF4 4-bit quantization, BF16 computation,
completion-only loss, and learning rate `1e-4`.

Each final adapter was evaluated on all 250 Validation rows for
completion-only loss and on a fixed 16-row generation subset for token F1.
The underlying metadata, complete logged training-loss history, evaluation
configuration, and generated samples are versioned under
`artifacts/experiments/`.

## Results

| Rank | Trainable parameters | Final training loss | Validation loss | Mean token-F1 | Peak VRAM |
|---:|---:|---:|---:|---:|---:|
| 8 | 58,363,904 | 0.09384 | **0.14189** | **0.77090** | 27.14 GiB |
| 16 | 116,727,808 | 0.08474 | 0.14526 | 0.76317 | 27.68 GiB |
| 32 | 233,455,616 | 0.08435 | 0.15145 | 0.75138 | 28.63 GiB |

The r=8 adapter is the selection winner: it has the lowest full-Validation
loss and highest fixed-sample token-F1, while using the fewest trainable
parameters and least VRAM. Increasing rank improved training loss but did not
improve held-out Validation metrics, so the higher-rank adapters show no
selection benefit under this controlled comparison.

## Decision and retained artifacts

Select **r=8 / alpha=16 / lr=1e-4** for the next locked-configuration stage.
Do not run Test until that next-stage protocol is approved.

The final adapters and resumable checkpoints are retained on the persistent
training volume but are deliberately not committed: adapter files are
approximately 233 MB (r=8), 467 MB (r=16), and 934 MB (r=32), while GitHub
rejects individual files above 100 MB. Publishing binary adapters requires an
explicit Git LFS or model-registry release decision.
