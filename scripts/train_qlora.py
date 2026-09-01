"""Reproducible QLoRA training for the selected Pipeline v3 QA records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/training/qwen3_8_27b_qlora.json"))
    parser.add_argument("--max-steps", type=int, default=-1, help="Use 1 for a real GPU smoke test")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for 4-bit QLoRA training")
    set_seed(int(config["seed"]))
    train_path = Path(config["train_file"])
    validation_path = Path(config["validation_file"])
    tokenizer = AutoTokenizer.from_pretrained(config["model_id"], revision=config["revision"])

    def render(row: dict) -> dict[str, str]:
        context = "\n\n".join(item["text"] for item in row["contexts"])
        user = f"Answer the question using the supplied passage.\n\nPassage:\n{context}\n\nQuestion:\n{row['question']}"
        messages = [{"role": "user", "content": user}, {"role": "assistant", "content": row["answer"]}]
        return {"text": tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)}

    train_rows = read_jsonl(train_path)
    validation_rows = read_jsonl(validation_path)
    train_dataset = Dataset.from_list([render(row) for row in train_rows])
    validation_dataset = Dataset.from_list([render(row) for row in validation_rows])
    model = AutoModelForCausalLM.from_pretrained(
        config["model_id"],
        revision=config["revision"],
        device_map="auto",
        torch_dtype=torch.bfloat16,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        ),
    )
    output_dir = Path(config["output_dir"])
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        peft_config=LoraConfig(
            r=int(config["lora_rank"]),
            lora_alpha=int(config["lora_alpha"]),
            lora_dropout=float(config["lora_dropout"]),
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
        args=SFTConfig(
            output_dir=str(output_dir),
            dataset_text_field="text",
            max_length=int(config["max_seq_length"]),
            num_train_epochs=float(config["epochs"]),
            max_steps=args.max_steps,
            learning_rate=float(config["learning_rate"]),
            per_device_train_batch_size=int(config["per_device_train_batch_size"]),
            per_device_eval_batch_size=int(config["per_device_eval_batch_size"]),
            gradient_accumulation_steps=int(config["gradient_accumulation_steps"]),
            gradient_checkpointing=True,
            bf16=True,
            optim="paged_adamw_8bit",
            eval_strategy="steps" if args.max_steps > 0 else "epoch",
            save_strategy="steps" if args.max_steps > 0 else "epoch",
            logging_steps=1,
            report_to="none",
            seed=int(config["seed"]),
        ),
    )
    result = trainer.train()
    adapter_dir = output_dir / "adapter"
    trainer.save_model(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    metadata = {
        "model_id": config["model_id"],
        "revision": config["revision"],
        "config": config,
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "train_sha256": file_sha256(train_path),
        "validation_sha256": file_sha256(validation_path),
        "metrics": result.metrics,
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
