"""Config-driven, reproducible QLoRA training for Pipeline v3 QA records."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IGNORE_INDEX = -100


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_user_prompt(row: dict[str, Any]) -> str:
    context = "\n\n".join(item["text"] for item in row["contexts"])
    return (
        "Answer the question using only the supplied passage.\n\n"
        f"Passage:\n{context}\n\nQuestion:\n{row['question']}"
    )


def _input_ids(encoded: Any) -> list[int]:
    if isinstance(encoded, dict) or hasattr(encoded, "keys"):
        encoded = encoded["input_ids"]
    if encoded and isinstance(encoded[0], list):
        encoded = encoded[0]
    return list(encoded)


def encode_completion_only(tokenizer: Any, row: dict[str, Any], max_length: int) -> dict[str, list[int]]:
    """Apply the pinned chat template and mask every token before the answer."""
    prompt_messages = [{"role": "user", "content": build_user_prompt(row)}]
    full_messages = [*prompt_messages, {"role": "assistant", "content": row["answer"]}]
    template_kwargs = {"tokenize": True, "enable_thinking": False, "preserve_thinking": False}
    prompt_ids = _input_ids(
        tokenizer.apply_chat_template(prompt_messages, add_generation_prompt=True, **template_kwargs)
    )
    input_ids = _input_ids(
        tokenizer.apply_chat_template(full_messages, add_generation_prompt=False, **template_kwargs)
    )
    if input_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError(f"chat-template prefix mismatch for candidate {row.get('candidate_id', '<unknown>')}")
    if len(input_ids) > max_length:
        raise ValueError(
            f"candidate {row.get('candidate_id', '<unknown>')} has {len(input_ids)} tokens, "
            f"exceeding max_seq_length={max_length}; raise the configured limit instead of silently truncating"
        )
    labels = [IGNORE_INDEX] * len(prompt_ids) + input_ids[len(prompt_ids) :]
    if not labels or all(label == IGNORE_INDEX for label in labels):
        raise ValueError(f"candidate {row.get('candidate_id', '<unknown>')} has no assistant loss tokens")
    return {"input_ids": input_ids, "attention_mask": [1] * len(input_ids), "labels": labels}


class TokenizedQADataset:
    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, max_length: int) -> None:
        self.examples = [encode_completion_only(tokenizer, row, max_length) for row in rows]

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.examples[index]


@dataclass
class CompletionOnlyCollator:
    pad_token_id: int
    label_pad_token_id: int = IGNORE_INDEX

    def __call__(self, features: list[dict[str, list[int]]]) -> dict[str, Any]:
        import torch

        max_length = max(len(feature["input_ids"]) for feature in features)
        batch = {"input_ids": [], "attention_mask": [], "labels": []}
        for feature in features:
            padding = max_length - len(feature["input_ids"])
            batch["input_ids"].append(feature["input_ids"] + [self.pad_token_id] * padding)
            batch["attention_mask"].append(feature["attention_mask"] + [0] * padding)
            batch["labels"].append(feature["labels"] + [self.label_pad_token_id] * padding)
        return {key: torch.tensor(value, dtype=torch.long) for key, value in batch.items()}


def find_lora_targets(model: Any, scope: str, suffixes: list[str]) -> tuple[list[str], dict[str, int]]:
    """Resolve exact language-model module paths so the vision tower cannot be adapted accidentally."""
    targets: list[str] = []
    counts = {suffix: 0 for suffix in suffixes}
    for name, _module in model.named_modules():
        if not name.startswith(scope):
            continue
        suffix = name.rsplit(".", 1)[-1]
        if suffix in counts:
            targets.append(name)
            counts[suffix] += 1
    return targets, counts


def validate_target_counts(actual: dict[str, int], expected: dict[str, int]) -> None:
    if actual != expected:
        raise RuntimeError(f"LoRA target architecture mismatch: expected {expected}, found {actual}")


def latest_checkpoint(output_dir: Path) -> Path:
    checkpoints = []
    for path in output_dir.glob("checkpoint-*"):
        match = re.fullmatch(r"checkpoint-(\d+)", path.name)
        if match and path.is_dir():
            checkpoints.append((int(match.group(1)), path))
    if not checkpoints:
        raise FileNotFoundError(f"no checkpoint-* directory exists under {output_dir}")
    return max(checkpoints)[1]


def checkpoint_global_step(checkpoint: Path) -> int:
    state_path = checkpoint / "trainer_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return int(state["global_step"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/training/qwen3_8_27b_qlora.json"))
    parser.add_argument(
        "--resume-from-checkpoint",
        nargs="?",
        const="auto",
        help="Resume from the latest output checkpoint, or from the supplied checkpoint path",
    )
    return parser.parse_args()


def main() -> None:
    process_started = time.monotonic()
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    model_config = config["model"]
    quant_config = config["quantization"]
    lora_config = config["lora"]
    training_config = config["training"]
    dataset_config = config["dataset"]
    output_dir = Path(config["output_dir"])

    resume_checkpoint: str | None = args.resume_from_checkpoint
    if resume_checkpoint == "auto":
        resume_checkpoint = str(latest_checkpoint(output_dir))
    if resume_checkpoint is not None and int(training_config["max_steps"]) > 0:
        completed_steps = checkpoint_global_step(Path(resume_checkpoint))
        if completed_steps >= int(training_config["max_steps"]):
            print(
                json.dumps(
                    {
                        "status": "already_complete",
                        "checkpoint": resume_checkpoint,
                        "global_step": completed_steps,
                        "configured_max_steps": int(training_config["max_steps"]),
                    },
                    sort_keys=True,
                )
            )
            return
    if output_dir.exists() and any(output_dir.iterdir()) and resume_checkpoint is None:
        raise FileExistsError(
            f"{output_dir} is not empty; choose a new experiment output_dir or pass --resume-from-checkpoint"
        )

    import torch
    import transformers
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoConfig,
        AutoModelForImageTextToText,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for 4-bit QLoRA training")
    if model_config["dtype"] == "bfloat16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("the selected GPU does not support BF16 compute")
    set_seed(int(config["seed"]), deterministic=bool(training_config["full_determinism"]))

    remote_config = AutoConfig.from_pretrained(model_config["model_id"], revision=model_config["revision"])
    actual_architectures = list(remote_config.architectures or [])
    if remote_config.model_type != model_config["expected_model_type"]:
        raise RuntimeError(
            f"expected model_type={model_config['expected_model_type']}, found {remote_config.model_type}"
        )
    if model_config["expected_architecture"] not in actual_architectures:
        raise RuntimeError(
            f"expected architecture={model_config['expected_architecture']}, found {actual_architectures}"
        )

    train_path = Path(dataset_config["train_file"])
    validation_path = Path(dataset_config["validation_file"])
    actual_train_sha256 = file_sha256(train_path)
    actual_validation_sha256 = file_sha256(validation_path)
    if actual_train_sha256 != dataset_config["expected_train_sha256"]:
        raise RuntimeError(f"train dataset hash mismatch: {actual_train_sha256}")
    if actual_validation_sha256 != dataset_config["expected_validation_sha256"]:
        raise RuntimeError(f"validation dataset hash mismatch: {actual_validation_sha256}")
    train_rows = read_jsonl(train_path)
    validation_rows = read_jsonl(validation_path)
    if len(train_rows) != int(dataset_config["expected_train_rows"]):
        raise RuntimeError(f"expected {dataset_config['expected_train_rows']} train rows, found {len(train_rows)}")
    if len(validation_rows) != int(dataset_config["expected_validation_rows"]):
        raise RuntimeError(
            f"expected {dataset_config['expected_validation_rows']} validation rows, found {len(validation_rows)}"
        )
    if training_config["max_train_samples"] is not None:
        train_rows = train_rows[: int(training_config["max_train_samples"])]
    if training_config["max_eval_samples"] is not None:
        validation_rows = validation_rows[: int(training_config["max_eval_samples"])]

    tokenizer = AutoTokenizer.from_pretrained(model_config["model_id"], revision=model_config["revision"])
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_dataset = TokenizedQADataset(train_rows, tokenizer, int(training_config["max_seq_length"]))
    validation_dataset = TokenizedQADataset(validation_rows, tokenizer, int(training_config["max_seq_length"]))

    dtype = getattr(torch, model_config["dtype"])
    torch.cuda.empty_cache()
    model = AutoModelForImageTextToText.from_pretrained(
        model_config["model_id"],
        revision=model_config["revision"],
        device_map={"": torch.cuda.current_device()},
        dtype=dtype,
        low_cpu_mem_usage=True,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=bool(quant_config["load_in_4bit"]),
            bnb_4bit_quant_type=quant_config["quant_type"],
            bnb_4bit_use_double_quant=bool(quant_config["double_quant"]),
            bnb_4bit_compute_dtype=dtype,
        ),
    )
    model.config.use_cache = False
    if hasattr(model.config, "text_config"):
        model.config.text_config.use_cache = False
    model = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing=bool(training_config["gradient_checkpointing"]),
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )
    target_modules, target_counts = find_lora_targets(
        model, lora_config["target_scope"], lora_config["target_modules"]
    )
    validate_target_counts(target_counts, lora_config["expected_target_counts"])
    model = get_peft_model(
        model,
        LoraConfig(
            r=int(lora_config["rank"]),
            lora_alpha=int(lora_config["alpha"]),
            lora_dropout=float(lora_config["dropout"]),
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        ),
    )
    trainable_parameters = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    loaded_parameter_elements = sum(parameter.numel() for parameter in model.parameters())

    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_config = {**config, "config_sha256": json_sha256(config)}
    (output_dir / "training_config.json").write_text(
        json.dumps(resolved_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    trainer = Trainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        data_collator=CompletionOnlyCollator(tokenizer.pad_token_id),
        args=TrainingArguments(
            output_dir=str(output_dir),
            run_name=config["experiment_name"],
            num_train_epochs=float(training_config["epochs"]),
            max_steps=int(training_config["max_steps"]),
            learning_rate=float(training_config["learning_rate"]),
            lr_scheduler_type=training_config["lr_scheduler_type"],
            warmup_steps=int(training_config["warmup_steps"]),
            weight_decay=float(training_config["weight_decay"]),
            max_grad_norm=float(training_config["max_grad_norm"]),
            per_device_train_batch_size=int(training_config["per_device_train_batch_size"]),
            per_device_eval_batch_size=int(training_config["per_device_eval_batch_size"]),
            gradient_accumulation_steps=int(training_config["gradient_accumulation_steps"]),
            gradient_checkpointing=bool(training_config["gradient_checkpointing"]),
            gradient_checkpointing_kwargs={"use_reentrant": False},
            use_cache=False,
            bf16=model_config["dtype"] == "bfloat16",
            tf32=bool(training_config["tf32"]),
            optim=training_config["optimizer"],
            eval_strategy="steps",
            eval_steps=int(training_config["eval_steps"]),
            save_strategy="steps",
            save_steps=int(training_config["save_steps"]),
            save_total_limit=int(training_config["save_total_limit"]),
            logging_strategy="steps",
            logging_steps=int(training_config["logging_steps"]),
            logging_first_step=True,
            report_to=training_config["report_to"],
            seed=int(config["seed"]),
            data_seed=int(config["seed"]),
            full_determinism=bool(training_config["full_determinism"]),
            remove_unused_columns=False,
            prediction_loss_only=True,
            load_best_model_at_end=bool(training_config.get("load_best_model_at_end", False)),
            metric_for_best_model="eval_loss",
            greater_is_better=False,
        ),
    )
    train_result = trainer.train(resume_from_checkpoint=resume_checkpoint)
    eval_metrics = trainer.evaluate(metric_key_prefix="validation")
    elapsed = time.monotonic() - started
    adapter_dir = output_dir / "adapter"
    trainer.save_model(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    trainer.save_state()
    (output_dir / "log_history.json").write_text(
        json.dumps(trainer.state.log_history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    metadata = {
        "experiment_name": config["experiment_name"],
        "model_id": model_config["model_id"],
        "revision": model_config["revision"],
        "architecture": actual_architectures,
        "seed": int(config["seed"]),
        "dataset_version": dataset_config["version"],
        "config_sha256": resolved_config["config_sha256"],
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "train_sha256": actual_train_sha256,
        "validation_sha256": actual_validation_sha256,
        "trainable_parameters": trainable_parameters,
        "base_checkpoint_parameters": int(model_config["parameter_count"]),
        "loaded_parameter_elements": loaded_parameter_elements,
        "trainable_parameter_percent": 100
        * trainable_parameters
        / (int(model_config["parameter_count"]) + trainable_parameters),
        "lora_target_counts": target_counts,
        "peak_gpu_vram_bytes": torch.cuda.max_memory_allocated(),
        "wall_clock_seconds": elapsed,
        "end_to_end_seconds": time.monotonic() - process_started,
        "train_metrics": train_result.metrics,
        "validation_metrics": eval_metrics,
        "resume_from_checkpoint": resume_checkpoint,
        "software": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        },
        "gpu": torch.cuda.get_device_name(torch.cuda.current_device()),
    }
    (output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
