# QLoRA Phase 1–2 audit and execution plan

Status: Phase 1 implemented and verified; Phase 2 completed successfully on 2026-09-01.

## Audit result

The pinned repository is technically trainable with QLoRA on the current 32 GiB GPU, but the previous training entry point was not safe to run as written.

| Check | Finding before Phase 1 | Resolution |
|---|---|---|
| Base model | `Qwen/Qwen3.8-27B` is a 27B native vision-language model, not a conventional Qwen causal-LM checkpoint. Its config reports `Qwen3_5ForConditionalGeneration`. | Keep the pinned base for the requested experiment scope, load it with `AutoModelForImageTextToText`, and assert model type and architecture before downloading weights. |
| Model loading | `AutoModelForCausalLM` did not match the checkpoint architecture. | Use the image-text auto class with text-only token inputs and a single-GPU device map. |
| QLoRA | NF4, double quantization, and BF16 were appropriate, but k-bit training preparation was implicit/missing. | Explicitly call `prepare_model_for_kbit_training`, disable cache, use non-reentrant gradient checkpointing, and retain paged 8-bit AdamW. |
| LoRA targets | The old suffix list covered full-attention and MLP projections but missed the 48 linear-attention layers. A suffix-only match also lacked an explicit vision exclusion. | Resolve exact paths only below `model.language_model.layers.`. The checked architecture has 496 targets: 64 full-attention projections, 240 linear-attention projections, and 192 MLP projections; zero vision modules match. |
| Chat template | The records have the expected `contexts`, `question`, and `answer` fields, but thinking mode was left at the template default. | Apply the pinned tokenizer template with `enable_thinking=false` and `preserve_thinking=false` for both training and later generation. |
| Loss mask | The old `text` dataset caused loss on user instructions, source passages, and assistant answers. | Pre-tokenize prompt and completed chat separately, verify prefix identity, and assign `-100` to every prompt/padding label. Only assistant answer and termination tokens contribute to loss. |
| Sequence length | `2048` was valid but wasteful. Across 2,250 Train/Validation records, full-chat lengths are 128–703 tokens, P95 463 and P99 558; none exceed 1,024. | Use `max_seq_length=1024` and fail rather than silently truncate if the dataset changes. |
| Checkpoint/evaluation | Checkpoints existed in principle, but there was no resume CLI, experiment overwrite guard, guaranteed final validation, or complete resource metadata. A one-step run could evaluate all 250 validation rows. | Add explicit/automatic resume, independent output directories, limited smoke data, step evaluation/saving, final validation, adapter/state/log history, hashes, trainable parameters, peak VRAM and wall-clock metadata. |
| Dependencies | `transformers>=5.15` required Hub `>=1.5`, while the project required Hub `<1`; installation was unsatisfiable and also tried to replace the CUDA/PyTorch stack. | Pin the checkpoint-compatible Transformers 5.8 line, Hub 1.x, PyTorch 2.8, bitsandbytes 0.50, Accelerate 1.12–1.14 and PEFT 0.20. Remove unused TRL/Datasets dependencies. |

The immutable inputs were verified without opening Test: Train has 2,000 rows and SHA-256 `d8ba14de...a3442`; Validation has 250 rows and SHA-256 `d12be947...0a13`; the sealed Test file has 250 rows and hash `9dae4007...6a14`. The full hashes are enforced in each training config.

One project limitation should stay visible in later reporting: the same pinned base model generated and self-judged the target answers. Fine-tuning can improve source-grounded response behavior and validation loss, but an evaluation using that same judge is not independent evidence. This does not require adding another base-model experiment; it requires honest wording and eventually a separately configured judge or small human review for the QA score.

## Phase 1 — correct and verify the training implementation

### Why

Phase 1 prevents an expensive run from training the wrong architecture, the wrong tokens, or the vision tower, and makes every later rank comparison reproducible and auditable.

### Files changed

- `scripts/train_qlora.py`: correct loader, completion-only labels, QLoRA preparation, architecture-gated LoRA targets, output protection, resume, final validation, and metadata.
- `configs/training/qwen3_8_27b_qlora.json`: config-driven r=16/lr=1e-4 reference experiment.
- `configs/training/smoke_r8_1step.json`: isolated 8-train/4-validation, one-optimizer-step smoke.
- `pyproject.toml` and `requirements-training.txt`: compatible dependency windows.
- `tests/test_training_qlora.py`: loss-mask, no-truncation, target-scope, and latest-checkpoint tests.
- `tests/test_pipeline_v3.py`: update one stale smoke-config filename to the current Qwen config.

### Commands

Run from the repository root:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-training.txt
python -m pip install -e '.[dev]'

python -m json.tool configs/training/qwen3_8_27b_qlora.json >/dev/null
python -m json.tool configs/training/smoke_r8_1step.json >/dev/null
pytest -q
ruff check .
```

Optional no-weight model/config preflight:

```bash
python - <<'PY'
from transformers import AutoConfig

c = AutoConfig.from_pretrained(
    "Qwen/Qwen3.8-27B",
    revision="1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0",
)
print(c.model_type, c.architectures)
assert c.model_type == "qwen3_5"
assert c.architectures == ["Qwen3_5ForConditionalGeneration"]
PY
```

### Success criteria

- Dependency installation resolves without replacing PyTorch 2.8/CUDA 12.8.
- All tests and Ruff pass. Current local result: 16 tests passed.
- Preflight prints the pinned `qwen3_5` conditional-generation architecture.
- Meta-model inspection finds exactly 496 language-model targets and zero visual targets.
- No command reads `test.jsonl` contents.

### If it fails

- Dependency resolution: confirm Python 3.11/3.12 and use the committed version windows; do not mix an old environment with partial upgrades.
- Hub errors: run `hf auth login` or inject `HF_TOKEN`, then verify the pinned revision still exists.
- Architecture/count mismatch: stop. Inspect `config.json` and `model.named_modules()`; do not weaken the assertions just to make training start.
- BF16 failure: verify the NVIDIA driver, CUDA-visible GPU, and `torch.cuda.is_bf16_supported()`.
- Dataset hash/count failure: restore the committed v3 split; do not train a mixture under the same experiment name.

## Phase 2 — one real optimizer-step smoke

### Why

Static checks cannot prove that 4-bit kernels, backward propagation through all hybrid attention targets, optimizer state, validation, checkpoint serialization, and adapter reload artifacts work together on the actual GPU.

### Files involved

No new code should be needed. Use `configs/training/smoke_r8_1step.json`; outputs go only to `artifacts/smoke/smoke_r8_1step/`.

### Commands

First verify the GPU is idle and the environment is correct:

```bash
nvidia-smi
python - <<'PY'
import bitsandbytes, peft, torch, transformers

print("torch", torch.__version__, "cuda", torch.version.cuda)
print("transformers", transformers.__version__)
print("peft", peft.__version__, "bitsandbytes", bitsandbytes.__version__)
print("gpu", torch.cuda.get_device_name(0))
print("bf16", torch.cuda.is_bf16_supported())
assert torch.cuda.is_available() and torch.cuda.is_bf16_supported()
PY
```

Then run exactly one optimizer step:

```bash
python scripts/train_qlora.py \
  --config configs/training/smoke_r8_1step.json \
  2>&1 | tee smoke-r8-1step.log
```

Inspect outputs:

```bash
find artifacts/smoke/smoke_r8_1step -maxdepth 2 -type f | sort
python -m json.tool artifacts/smoke/smoke_r8_1step/training_metadata.json
python -m json.tool artifacts/smoke/smoke_r8_1step/log_history.json
```

To verify resume behavior after an interrupted or completed smoke:

```bash
python scripts/train_qlora.py \
  --config configs/training/smoke_r8_1step.json \
  --resume-from-checkpoint
```

### Success criteria

- The model loads with NF4/double-quant/BF16 and reports 496 matched target modules.
- Step 1 produces a finite positive training loss; gradients and optimizer complete without OOM or NaN.
- Four validation rows produce a finite `validation_loss`.
- `checkpoint-1/`, `adapter/`, `training_config.json`, `training_metadata.json`, `log_history.json`, and Trainer state exist.
- Metadata contains rank=8 through its config, seed 3407, exact model revision/dataset hashes, trainable parameter count, runtime, and nonzero peak VRAM.
- The adapter contains `adapter_config.json` and adapter weights; the vision tower is absent from LoRA target names.
- Resume detects that `checkpoint-1` already reached configured `max_steps=1` and exits with
  `status=already_complete`, without starting another optimizer step or overwriting another experiment.

### If it fails

- OOM during loading: confirm no other GPU process exists. Do not enable CPU offload for the controlled experiments; first assess a smaller target set or a longer-context memory mistake.
- OOM during backward: verify sequence length is 1,024, batch size and accumulation are 1 for smoke, gradient checkpointing is enabled, and cache is disabled.
- Slow linear attention: Transformers may report that optional Flash Linear Attention/causal-conv1d kernels are absent and use its PyTorch fallback. Record speed/VRAM first; only add those architecture-specific kernels if the fallback makes Phase 3 infeasible, because they add compilation and compatibility risk.
- Target-count mismatch: inspect the exact checkpoint revision and stop before training.
- All labels are `-100` or prefix mismatch: inspect chat-template output and tokenizer version; do not fall back to full-sequence loss.
- Checkpoint missing: inspect disk space and `save_steps=1`; confirm the output directory was empty before the first run.
- Validation OOM: keep eval batch size 1 and check that only four smoke validation samples were selected.

Do not begin the 20–50 step Phase 3 run until every Phase 2 criterion is met and its metadata is retained.

## Recorded Phase 2 result

The run used an NVIDIA RTX PRO 4500 Blackwell Server Edition (32,623 MiB), PyTorch 2.8.0+cu128,
Transformers 5.8.1, rank 8 / alpha 16, and seed 3407. The result was:

| Item | Result |
|---|---:|
| Optimizer steps | 1 |
| Smoke Train / Validation rows | 8 / 4 |
| Training loss | 0.6661036 |
| Validation loss | 0.6404160 |
| Trainable parameters | 58,363,904 |
| LoRA module instances | 496 |
| Vision or MTP LoRA modules | 0 |
| Peak allocated VRAM | 26,791,468,544 bytes (24.95 GiB) |
| Trainer runtime | about 7.3 seconds, excluding model import/load |
| Adapter size | about 604 MiB including checkpoint and final adapter copies |

`checkpoint-1` contains adapter weights, optimizer, scheduler, RNG and trainer state. A real resume invocation
returned `status=already_complete` at global step 1 and performed no additional optimizer step. The optional
Flash Linear Attention/causal-conv1d fast path was not installed; the supported PyTorch fallback was fast enough
for this smoke, so Phase 1 does not add those compilation-heavy dependencies.

## Locked execution order after this handoff

Do not combine these gates or inspect Test early:

1. **Phase 3 — 20–50 step smoke training:** copy the smoke config to a new `artifacts/smoke/` run, use roughly
   32 optimizer steps and enough training rows for the configured effective batch. Confirm a decreasing but finite
   loss curve, multiple checkpoints, resume from a genuinely incomplete checkpoint, stable peak VRAM, and adapter
   reload. This is an engineering run, not a model-selection result.
2. **Phase 4 — Base validation baseline:** implement the shared evaluation/generation entry point, freeze a small
   fixed Validation QA subset and generation settings, and record base completion-only Validation loss and answers.
   The base row must use the same prompt/template and decoding settings as every adapter.
3. **Phase 5 — rank experiments:** create independent configs/output directories for r=8/alpha=16,
   r=16/alpha=32 and r=32/alpha=64, all at lr=1e-4 and otherwise identical. Run one at a time on the same GPU.
4. **Phase 6 — select rank on Validation:** compare Validation loss, frozen Validation QA score, trainable parameters,
   peak VRAM and runtime. Prefer the smallest rank whose quality improvement is material; do not read Test.
5. **Phase 7 — one learning-rate comparison:** compare the winning rank at lr=1e-4 versus 5e-5 only. Reuse the
   1e-4 result; do not rerun it unless invalid.
6. **Phase 8 — final model:** lock the winning config and adapter/checkpoint. If the winning completed experiment
   already used the full Train set and budget, promote that artifact instead of retraining it and introducing another
   random run.
7. **Phase 9 — one-time Test evaluation:** first write `winner_lock.json` with model/config/checkpoint/generation hashes,
   then verify the existing Test seal and open Test once for Base versus Final evaluation. Never use the outcome to
   revise rank or learning rate.
8. **Phase 10 — reporting:** generate the requested comparison table, representative fixed QA outputs, limitations,
   rank/VRAM/quality and LR conclusions, README commands, and concise resume bullets backed by saved artifacts.
