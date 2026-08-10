# LacanLLM Model Card

## Model description

LacanLLM is a research adapter for `google/gemma-4-E2B-it`, trained with PEFT/QLoRA on synthetic questions paired with Lacanian source passages. Adapters require the separately licensed Gemma base model.

## Intended use

- Education and research about domain adaptation, QLoRA, and evaluation;
- Exploratory question answering about Lacanian texts;
- Reproducible comparison of 4-bit and 8-bit training configurations.

## Out-of-scope use

- Mental-health diagnosis, treatment, crisis support, or clinical decisions;
- Presenting generated interpretations as authoritative scholarship;
- Generating citations without checking the original source.

## Training

The historical adapters used 4,750 training rows, 250 validation rows, seed 3407, maximum sequence length 1024, LoRA rank 16, alpha 32, and 1.5 epochs on an RTX 5070. See each adapter's `training_metadata.json` for recorded details.

The v2 4-bit NF4 adapter used 2,700 training rows, 300 validation rows, one epoch, assistant-only loss, and the same seed and LoRA settings. It completed 675 optimizer steps in 1,998 seconds of Trainer runtime. The final adapter SHA-256 is recorded in its training metadata.

## Evaluation status

Historical validation loss is reported for reproducibility, but the historical split contained 25 exact answer overlaps. The completed v2 run has zero exact cross-split instruction/output overlaps and reached validation loss 3.3556. Historical and v2 losses are not directly comparable because both the data split and label masking changed.

Before claiming model improvement, compare the base model and each adapter on the same leakage-free benchmark and complete a blinded domain review for theoretical accuracy, relevance, hallucination, and source faithfulness.

## Limitations

- Questions are synthetic and may encode generator-model biases;
- Source passages may contain OCR errors;
- Lexical overlap does not measure theoretical correctness;
- Results currently come from one seed and one consumer GPU;
- The model can hallucinate, flatten theoretical disagreements, and invent citations.

## Licenses

Repository code is MIT licensed. The base model, source texts, dataset, and adapter may have separate restrictions; consult their original licenses before use or redistribution.
