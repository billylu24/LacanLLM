# Pipeline v3 Gemma 4 12B smoke

Run date: 2026-08-25  
Profile: smoke-only self-judge; not eligible for formal data production

- Model: `google/gemma-4-12B-it`
- Revision: `707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7`
- Runtime: Transformers 5.15.1, Torch 2.13.0, NF4 4-bit with double
  quantization and BF16 compute, batch size 1
- Hardware: NVIDIA GeForce RTX 5070, 12,227 MiB VRAM
- Candidates: six Train records: four single-context and two nearby
  paired-context records
- Result: 6/6 generation JSON parsed, 6/6 exact evidence maps passed, 6/6
  deterministic hard filter passed, 6/6 Judge JSON parsed
- Structured repair retries: generator 0, Judge 0
- Informational Judge quality pass: 6/6 (100%)
- Peak allocated VRAM: 8,171,325,952 bytes (7.61 GiB)
- Peak process RSS: 16,946,152 KiB (16.16 GiB); model CPU offload was not
  used because the quantized model fit within the GPU allocation
- Model inference: 2,127 output tokens in 90.51 seconds, aggregate 23.50
  output tokens/second
- Resume verification: the identical second pass appended zero generation
  rows and zero judgment rows, loaded neither backend, and left both stage
  JSONL hashes unchanged

The first long-ID trial also exposed an evidence `context_id` copy failure on
one paired record after its single repair. The hard gate rejected it. Pipeline
v3.2 now presents deterministic short IDs (`context_1`, `context_2`) to the
model while retaining paragraph hashes separately as provenance. The clean
post-fix run above passed without repair.
