# Pipeline v3.1 Gemma 4 12B QA-only smoke

Run date: 2026-08-25  
Profile: smoke-only self-judge; not part of formal production artifacts

- Model: `google/gemma-4-12B-it`
- Revision: `707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7`
- Prompts: generator `generate-v3.3`, Judge `judge-v3.2`
- Schema: generator returns exactly `question` and `answer`; the pipeline owns
  context and source provenance
- Runtime: NF4 4-bit with double quantization and BF16 compute, batch size 1
- Result: 6/6 generation JSON parsed, 6/6 hard filter passed, 6/6 Judge JSON
  parsed, and 6/6 quality passed
- Rejections: generator 0, Judge 0; no repair path exists
- Peak allocated VRAM: 8,088,893,952 bytes (7.53 GiB)
- Model inference: 1,435 output tokens in 63.48 seconds, aggregate 22.61
  output tokens/second
- Generator inference: 23.93 seconds total, 3.99 seconds/record
- Judge inference: 39.55 seconds total, 6.59 seconds/record
- Resume verification: the identical second pass appended zero generation and
  judgment rows, loaded neither backend, and left both stage hashes unchanged

Compared with the prior evidence-extraction smoke (90.51 seconds), the QA-only
workflow reduced total model inference time by approximately 30% while
eliminating exact-quote copy failures. Invalid structured output is now logged
once and skipped without repair or batch termination.
