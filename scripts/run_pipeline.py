import argparse
import json
import os
import random
from pathlib import Path

import numpy as np
import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs.jsonl"
TRAINING_DATA_FILE = PROJECT_ROOT / "data" / "lacan_training_data.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "adapters" / "lacan_lora"

DEFAULT_MODEL_ID = "google/gemma-3-4b-it"
MAX_SEQ_LENGTH = 2048
TARGET_DATA_COUNT = 30000
NUM_EPOCHS = 1
SEED = 3407

BAD_PHRASES = (
    "i cannot answer",
    "i am an ai",
    "text provided does not",
    "context is missing",
    "sorry",
    "cannot provide",
)


def require_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in the environment before loading gated HF models.")
    return token


def prepare_training_data(raw_file: Path, output_file: Path, target_count: int) -> None:
    if not raw_file.exists():
        raise FileNotFoundError(raw_file)

    rows = []
    lengths = []
    with raw_file.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            instruction = entry.get("instruction", "").strip()
            output = entry.get("output", "").strip()
            if len(instruction) < 10 or len(output) < 80:
                continue
            if any(phrase in output.lower() for phrase in BAD_PHRASES):
                continue

            entry["instruction"] = instruction
            entry["output"] = output
            entry["_score"] = min(len(output), 6000) + 20 * int("?" in instruction)
            rows.append(entry)

    if not rows:
        raise RuntimeError("No usable SFT rows found after filtering.")

    rows.sort(key=lambda item: item["_score"], reverse=True)
    selected = rows[:target_count]
    random.Random(SEED).shuffle(selected)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as output:
        for row in selected:
            row.pop("_score", None)
            lengths.append(len(row["instruction"]) + len(row["output"]))
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    avg_chars = float(np.mean(lengths))
    max_chars = float(np.max(lengths))
    print(f"Prepared rows: {len(selected)}")
    print(f"Average chars: {avg_chars:.0f}")
    print(f"Max chars: {max_chars:.0f}")
    print(f"Estimated avg tokens: {avg_chars / 3.5:.0f}")
    print(f"Estimated max tokens: {max_chars / 3.5:.0f}")


def format_example(example, tokenizer):
    messages = [
        {"role": "user", "content": example["instruction"]},
        {"role": "assistant", "content": example["output"]},
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )


def tokenize_example(example, tokenizer):
    tokenized = tokenizer(
        example["text"],
        truncation=True,
        max_length=MAX_SEQ_LENGTH,
        padding=False,
    )
    return tokenized


def train(model_id: str, training_file: Path, output_dir: Path) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for LoRA training.")

    token = require_hf_token()
    tokenizer = AutoTokenizer.from_pretrained(model_id, token=token)
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        token=token,
        device_map="auto",
        quantization_config=quantization_config,
        attn_implementation="sdpa",
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    model = get_peft_model(model, lora_config)

    dataset = load_dataset("json", data_files=str(training_file), split="train")
    dataset = dataset.map(
        lambda example: {"text": format_example(example, tokenizer)},
        remove_columns=dataset.column_names,
    )
    dataset = dataset.map(
        lambda example: tokenize_example(example, tokenizer),
        remove_columns=["text"],
    )

    args = TrainingArguments(
        output_dir=str(PROJECT_ROOT / "outputs"),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        num_train_epochs=NUM_EPOCHS,
        learning_rate=2e-4,
        warmup_ratio=0.03,
        logging_steps=10,
        save_strategy="steps",
        save_steps=500,
        save_total_limit=2,
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        optim="paged_adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=SEED,
        report_to="none",
    )

    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=dataset,
        data_collator=data_collator,
    )
    trainer.train()

    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"Saved LoRA adapter to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare SFT data and train a PEFT LoRA adapter.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--target-count", type=int, default=TARGET_DATA_COUNT)
    args = parser.parse_args()

    if not args.skip_prepare:
        prepare_training_data(RAW_DATA_FILE, TRAINING_DATA_FILE, args.target_count)
    if not args.prepare_only:
        train(args.model_id, TRAINING_DATA_FILE, OUTPUT_DIR)


if __name__ == "__main__":
    main()
