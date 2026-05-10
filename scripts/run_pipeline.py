import argparse
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from datasets import Dataset, load_dataset
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoProcessor,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs.jsonl"
TRAINING_DATA_FILE = PROJECT_ROOT / "data" / "lacan_training_data.jsonl"
VALIDATION_DATA_FILE = PROJECT_ROOT / "data" / "lacan_validation_data.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "adapters" / "lacan_lora"
DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "outputs"

DEFAULT_MODEL_ID = "google/gemma-4-E4B-it"
DEFAULT_MAX_SEQ_LENGTH = 2048
DEFAULT_TARGET_DATA_COUNT = 30000
DEFAULT_VAL_RATIO = 0.05
DEFAULT_SEED = 3407
DEFAULT_MIN_OUTPUT_CHARS = 120
DEFAULT_MAX_OUTPUT_CHARS = 6000
DEFAULT_PREFERRED_OUTPUT_CHARS = 1200

BAD_PHRASES = (
    "i cannot answer",
    "i am an ai",
    "as an ai",
    "text provided does not",
    "context is missing",
    "sorry",
    "cannot provide",
)

DEFAULT_LORA_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


def require_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in the environment before loading gated HF models.")
    return token


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for row in rows:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_quality_score(entry: dict[str, Any], preferred_output_chars: int) -> int:
    instruction = entry["instruction"]
    output = entry["output"]
    output_len = len(output)
    score = 1000 - abs(output_len - preferred_output_chars)
    score += 50 if "?" in instruction else 0
    score += 25 if entry.get("source_file") else 0
    score -= 250 if entry.get("mojibake_score", 0) else 0
    return score


def prepare_training_data(
    raw_file: Path,
    train_file: Path,
    validation_file: Path,
    target_count: int,
    val_ratio: float,
    seed: int,
    min_output_chars: int,
    max_output_chars: int,
    preferred_output_chars: int,
) -> None:
    if not raw_file.exists():
        raise FileNotFoundError(raw_file)

    rows = []
    for entry in read_jsonl(raw_file):
        instruction = entry.get("instruction", "").strip()
        output = entry.get("output", "").strip()
        if len(instruction) < 10 or len(output) < min_output_chars:
            continue
        if len(output) > max_output_chars:
            continue
        if any(phrase in output.lower() for phrase in BAD_PHRASES):
            continue

        entry["instruction"] = instruction
        entry["output"] = output
        entry["_score"] = row_quality_score(entry, preferred_output_chars)
        rows.append(entry)

    if not rows:
        raise RuntimeError("No usable SFT rows found after filtering.")

    rows.sort(key=lambda item: item["_score"], reverse=True)
    selected = rows[:target_count]

    rng = random.Random(seed)
    source_files = sorted({row.get("source_file") for row in selected if row.get("source_file")})
    validation_sources = set(rng.sample(source_files, max(1, round(len(source_files) * val_ratio)))) if source_files else set()

    train_rows = []
    validation_rows = []
    for row in selected:
        row.pop("_score", None)
        if row.get("source_file") in validation_sources:
            validation_rows.append(row)
        else:
            train_rows.append(row)

    if not validation_rows:
        rng.shuffle(selected)
        split_at = max(1, round(len(selected) * val_ratio))
        validation_rows = selected[:split_at]
        train_rows = selected[split_at:]

    rng.shuffle(train_rows)
    rng.shuffle(validation_rows)

    write_jsonl(train_file, train_rows)
    write_jsonl(validation_file, validation_rows)
    print_data_summary(train_rows, validation_rows, train_file, validation_file)


def print_data_summary(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    train_file: Path,
    validation_file: Path,
) -> None:
    lengths = [len(row["instruction"]) + len(row["output"]) for row in train_rows + validation_rows]
    print(f"Train rows: {len(train_rows)} -> {train_file}")
    print(f"Validation rows: {len(validation_rows)} -> {validation_file}")
    print(f"Average chars: {float(np.mean(lengths)):.0f}")
    print(f"Max chars: {float(np.max(lengths)):.0f}")
    print(f"Estimated avg tokens: {float(np.mean(lengths)) / 3.5:.0f}")
    print(f"Estimated max tokens: {float(np.max(lengths)) / 3.5:.0f}")


def load_text_processor(model_id: str, token: str):
    try:
        processor = AutoProcessor.from_pretrained(model_id, token=token)
        tokenizer = getattr(processor, "tokenizer", processor)
        return processor, tokenizer
    except Exception:
        tokenizer = AutoTokenizer.from_pretrained(model_id, token=token)
        return tokenizer, tokenizer


def apply_chat_template(processor, tokenizer, instruction: str, output: str) -> str:
    messages = [
        {"role": "user", "content": instruction},
        {"role": "assistant", "content": output},
    ]
    template_kwargs = {
        "tokenize": False,
        "add_generation_prompt": False,
    }
    try:
        return processor.apply_chat_template(messages, enable_thinking=False, **template_kwargs)
    except TypeError:
        return tokenizer.apply_chat_template(messages, **template_kwargs)


def tokenize_dataset(dataset: Dataset, processor, tokenizer, max_seq_length: int) -> Dataset:
    def format_and_tokenize(example):
        text = apply_chat_template(processor, tokenizer, example["instruction"], example["output"])
        return tokenizer(
            text,
            truncation=True,
            max_length=max_seq_length,
            padding=False,
        )

    return dataset.map(format_and_tokenize, remove_columns=dataset.column_names)


def find_latest_checkpoint(checkpoint_dir: Path) -> str | None:
    if not checkpoint_dir.exists():
        return None
    checkpoints = [path for path in checkpoint_dir.glob("checkpoint-*") if path.is_dir()]
    if not checkpoints:
        return None
    return str(max(checkpoints, key=lambda path: int(path.name.split("-")[-1])))


def save_training_metadata(args: argparse.Namespace, output_dir: Path, train_count: int, validation_count: int) -> None:
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_id": args.model_id,
        "max_seq_length": args.max_seq_length,
        "train_rows": train_count,
        "validation_rows": validation_count,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.num_train_epochs,
        "per_device_train_batch_size": args.per_device_train_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def train(args: argparse.Namespace) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for LoRA training.")

    token = require_hf_token()
    processor, tokenizer = load_text_processor(args.model_id, token)
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
        args.model_id,
        token=token,
        device_map="auto",
        quantization_config=quantization_config,
        attn_implementation=args.attn_implementation,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        target_modules=list(DEFAULT_LORA_TARGET_MODULES),
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_dataset = load_dataset("json", data_files=str(args.training_file), split="train")
    eval_dataset = load_dataset("json", data_files=str(args.validation_file), split="train")
    train_dataset = tokenize_dataset(train_dataset, processor, tokenizer, args.max_seq_length)
    eval_dataset = tokenize_dataset(eval_dataset, processor, tokenizer, args.max_seq_length)

    training_args = TrainingArguments(
        output_dir=str(args.checkpoint_dir),
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        num_train_epochs=args.num_train_epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        warmup_ratio=args.warmup_ratio,
        logging_steps=args.logging_steps,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        gradient_checkpointing=True,
        optim=args.optim,
        weight_decay=args.weight_decay,
        lr_scheduler_type=args.lr_scheduler_type,
        seed=args.seed,
        data_seed=args.seed,
        report_to=args.report_to,
        run_name=args.run_name,
        remove_unused_columns=False,
    )

    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
    )

    resume_checkpoint = args.resume_from_checkpoint
    if args.auto_resume and resume_checkpoint is None:
        resume_checkpoint = find_latest_checkpoint(args.checkpoint_dir)
        if resume_checkpoint:
            print(f"Auto-resuming from {resume_checkpoint}")

    trainer.train(resume_from_checkpoint=resume_checkpoint)
    metrics = trainer.evaluate()
    print(metrics)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    save_training_metadata(args, args.output_dir, len(train_dataset), len(eval_dataset))
    print(f"Saved LoRA adapter to {args.output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Lacan SFT data and train a PEFT QLoRA adapter.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--raw-data-file", type=Path, default=RAW_DATA_FILE)
    parser.add_argument("--training-file", type=Path, default=TRAINING_DATA_FILE)
    parser.add_argument("--validation-file", type=Path, default=VALIDATION_DATA_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_DATA_COUNT)
    parser.add_argument("--val-ratio", type=float, default=DEFAULT_VAL_RATIO)
    parser.add_argument("--min-output-chars", type=int, default=DEFAULT_MIN_OUTPUT_CHARS)
    parser.add_argument("--max-output-chars", type=int, default=DEFAULT_MAX_OUTPUT_CHARS)
    parser.add_argument("--preferred-output-chars", type=int, default=DEFAULT_PREFERRED_OUTPUT_CHARS)
    parser.add_argument("--max-seq-length", type=int, default=DEFAULT_MAX_SEQ_LENGTH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--num-train-epochs", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--lr-scheduler-type", default="cosine")
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--per-device-eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--optim", default="paged_adamw_8bit")
    parser.add_argument("--attn-implementation", default="sdpa")
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--save-total-limit", type=int, default=2)
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--resume-from-checkpoint", default=None)
    parser.add_argument("--report-to", default="none")
    parser.add_argument("--run-name", default="lacan-lora")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.skip_prepare:
        prepare_training_data(
            raw_file=args.raw_data_file,
            train_file=args.training_file,
            validation_file=args.validation_file,
            target_count=args.target_count,
            val_ratio=args.val_ratio,
            seed=args.seed,
            min_output_chars=args.min_output_chars,
            max_output_chars=args.max_output_chars,
            preferred_output_chars=args.preferred_output_chars,
        )
    if not args.prepare_only:
        train(args)


if __name__ == "__main__":
    main()
