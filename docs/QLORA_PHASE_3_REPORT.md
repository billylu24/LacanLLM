# QLoRA Phase 3 — 32-step engineering smoke

Status: passed on 2026-09-01. This run is engineering evidence only and is not a rank-selection result.

## Why this run exists

The one-step Phase 2 run proved that a single forward/backward/save path worked. Phase 3 verifies sustained
gradient accumulation, a useful loss curve, repeated Validation, multiple checkpoints, recovery from a genuinely
incomplete run, resource stability, and loading the saved adapter into a fresh quantized base model.

## Configuration and command

The run used `configs/training/smoke_r8_32step.json` and wrote only to
`artifacts/smoke/smoke_r8_32step/`. It used rank 8 / alpha 16, lr 1e-4, 256 Train rows, 32 Validation rows,
micro-batch 1, gradient accumulation 8, and 32 optimizer steps. Evaluation and checkpoint saving occurred every
8 steps.

```bash
HF_HOME=/workspace/LacanLLM/.cache/huggingface \
python scripts/train_qlora.py \
  --config configs/training/smoke_r8_32step.json
```

After `checkpoint-8` was fully written, the process was deliberately interrupted. It was resumed with:

```bash
HF_HOME=/workspace/LacanLLM/.cache/huggingface \
python scripts/train_qlora.py \
  --config configs/training/smoke_r8_32step.json \
  --resume-from-checkpoint
```

The restored progress began at global step 9/32 with the expected lr 8e-5 and reproduced the interrupted step-9
loss. The completed run records `checkpoint-8` as its resume source.

## Results

| Item | Result |
|---|---:|
| Optimizer steps | 32 |
| Train / Validation subset | 256 / 32 |
| Effective train batch | 8 |
| Trainable parameters | 58,363,904 |
| Peak allocated VRAM | 29,194,088,448 bytes (27.19 GiB) |
| Checkpoints | 8, 16, 24, 32 |
| Final adapter SHA-256 | `4d1981e14e641c5e5229d334d9303ff4cd93f170a44d836e9fa626515c87fc22` |
| Resume-segment Trainer runtime | 424.6 seconds |
| Resume invocation end-to-end | 692.2 seconds, including import/load/save/final eval |
| Complete 32-step mean train loss | 0.32507 |
| First 8-step mean train loss | 0.53638 |
| Last 8-step mean train loss | 0.21257 |
| Gradient norm range | 0.5855–1.2248 |

Validation loss decreased monotonically on the fixed 32-row smoke subset:

| Global step | Validation loss |
|---:|---:|
| 8 | 0.34702 |
| 16 | 0.24292 |
| 24 | 0.21837 |
| 32 | 0.19628 |

The raw Transformers `train_metrics.train_loss=0.19097` is retained for provenance but must not be presented as
the complete-run mean: after resume, that version of Trainer normalizes the resumed segment against total
`max_steps`. `training_loss_summary.mean=0.32507`, derived from all 32 per-step records in `log_history.json`, is
the authoritative full smoke mean.

## Adapter reload verification

A fresh `AutoModelForImageTextToText` base was loaded with the same NF4/double-quant/BF16 settings, then the final
adapter was attached with `PeftModel.from_pretrained`. Reload produced exactly 496 adapter modules, all below
`model.language_model.layers`; vision and MTP matches were zero. A completion-only forward pass on one Validation
row had 105 supervised assistant tokens and finite loss 0.241896.

## Success criteria and failure checks

Phase 3 passes because all 32 losses and gradient norms were finite, both train-window and Validation loss declined,
four complete checkpoints exist, incomplete-checkpoint resume was correct, peak VRAM stayed below the 32 GiB GPU
limit, and a fresh adapter reload completed a real forward pass.

If a future reproduction fails, check in this order: exact model/config/data hashes; GPU occupancy; completion-only
labels; `checkpoint-*/trainer_state.json`, optimizer, scheduler and RNG files; the 496-module architecture assertion;
then network-filesystem latency. This run observed highly variable checkpoint loading/saving time on the shared
filesystem, while steady-state backward remained roughly 13 seconds per optimizer step.

## Gate decision

Phase 3 is complete. Proceed to Phase 4 Base Model Validation baseline. Do not use this small subset's loss to select
rank, do not start r=8/r=16/r=32 formal runs yet, and do not open Test.
