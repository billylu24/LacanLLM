"""Config-driven Base/adapter evaluation on the unsealed Validation split only."""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from train_qlora import (
    CompletionOnlyCollator,
    TokenizedQADataset,
    build_user_prompt,
    file_sha256,
    json_sha256,
    read_jsonl,
)


def normalized_tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def token_f1(prediction: str, reference: str) -> float:
    predicted = normalized_tokens(prediction)
    expected = normalized_tokens(reference)
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = sum((Counter(predicted) & Counter(expected)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    return 2 * precision * recall / (precision + recall)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    process_started = time.monotonic()
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    model_config = config["model"]
    dataset_config = config["dataset"]
    evaluation_config = config["evaluation"]
    output_dir = Path(config["output_dir"])
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"{output_dir} is not empty; evaluation outputs are immutable")

    import torch
    import transformers
    from transformers import AutoConfig, AutoModelForImageTextToText, AutoTokenizer, BitsAndBytesConfig, set_seed

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for the matched 4-bit evaluation")
    set_seed(int(config["seed"]))
    remote_config = AutoConfig.from_pretrained(model_config["model_id"], revision=model_config["revision"])
    if remote_config.model_type != model_config["expected_model_type"]:
        raise RuntimeError(f"unexpected model type: {remote_config.model_type}")
    if model_config["expected_architecture"] not in list(remote_config.architectures or []):
        raise RuntimeError(f"unexpected model architecture: {remote_config.architectures}")

    validation_path = Path(dataset_config["validation_file"])
    validation_sha256 = file_sha256(validation_path)
    if validation_sha256 != dataset_config["expected_validation_sha256"]:
        raise RuntimeError(f"validation dataset hash mismatch: {validation_sha256}")
    rows = read_jsonl(validation_path)
    if len(rows) != int(dataset_config["expected_validation_rows"]):
        raise RuntimeError(f"expected {dataset_config['expected_validation_rows']} Validation rows, found {len(rows)}")
    generation_indices = [int(index) for index in evaluation_config["generation_indices"]]
    indices_are_unique = len(generation_indices) == len(set(generation_indices))
    indices_are_valid = all(0 <= index < len(rows) for index in generation_indices)
    if not indices_are_unique or not indices_are_valid:
        raise ValueError("generation_indices must be unique valid Validation row indexes")

    tokenizer = AutoTokenizer.from_pretrained(model_config["model_id"], revision=model_config["revision"])
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    dataset = TokenizedQADataset(rows, tokenizer, int(evaluation_config["max_seq_length"]))
    collator = CompletionOnlyCollator(tokenizer.pad_token_id)
    dtype = getattr(torch, model_config["dtype"])
    quantization = config["quantization"]
    base = AutoModelForImageTextToText.from_pretrained(
        model_config["model_id"],
        revision=model_config["revision"],
        device_map={"": torch.cuda.current_device()},
        dtype=dtype,
        low_cpu_mem_usage=True,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=bool(quantization["load_in_4bit"]),
            bnb_4bit_quant_type=quantization["quant_type"],
            bnb_4bit_use_double_quant=bool(quantization["double_quant"]),
            bnb_4bit_compute_dtype=dtype,
        ),
    )
    adapter_path = config.get("adapter_path")
    if adapter_path:
        from peft import PeftModel

        model = PeftModel.from_pretrained(base, adapter_path)
    else:
        model = base
    model.eval()

    output_dir.mkdir(parents=True, exist_ok=False)
    resolved_config = {**config, "config_sha256": json_sha256(config)}
    (output_dir / "evaluation_config.json").write_text(
        json.dumps(resolved_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    torch.cuda.reset_peak_memory_stats()
    evaluation_started = time.monotonic()
    losses: list[float] = []
    supervised_tokens = 0
    with torch.inference_mode():
        for example in dataset.examples:
            batch = collator([example])
            batch = {key: value.to(torch.cuda.current_device()) for key, value in batch.items()}
            loss = float(model(**batch).loss)
            if not math.isfinite(loss):
                raise RuntimeError("non-finite Validation loss")
            losses.append(loss)
            supervised_tokens += sum(label != -100 for label in example["labels"])

    generation_rows: list[dict[str, Any]] = []
    for index in generation_indices:
        row = rows[index]
        messages = [{"role": "user", "content": build_user_prompt(row)}]
        rendered = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
            preserve_thinking=False,
        )
        inputs = tokenizer(rendered, return_tensors="pt")
        inputs = {key: value.to(torch.cuda.current_device()) for key, value in inputs.items()}
        input_length = inputs["input_ids"].shape[-1]
        generation_started = time.monotonic()
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=int(evaluation_config["max_new_tokens"]),
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
                use_cache=True,
            )
        generated_ids = output[0, input_length:]
        prediction = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        generation_rows.append(
            {
                "validation_index": index,
                "candidate_id": row["candidate_id"],
                "question": row["question"],
                "reference_answer": row["answer"],
                "prediction": prediction,
                "token_f1": token_f1(prediction, row["answer"]),
                "input_tokens": int(input_length),
                "output_tokens": int(generated_ids.numel()),
                "generation_seconds": time.monotonic() - generation_started,
            }
        )

    (output_dir / "generations.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in generation_rows),
        encoding="utf-8",
    )
    summary = {
        "evaluation_name": config["evaluation_name"],
        "model_id": model_config["model_id"],
        "revision": model_config["revision"],
        "adapter_path": adapter_path,
        "seed": int(config["seed"]),
        "validation_rows": len(rows),
        "validation_sha256": validation_sha256,
        "validation_loss": sum(losses) / len(losses),
        "supervised_assistant_tokens": supervised_tokens,
        "generation_rows": len(generation_rows),
        "mean_token_f1": sum(row["token_f1"] for row in generation_rows) / len(generation_rows),
        "peak_gpu_vram_bytes": torch.cuda.max_memory_allocated(),
        "evaluation_seconds": time.monotonic() - evaluation_started,
        "end_to_end_seconds": time.monotonic() - process_started,
        "gpu": torch.cuda.get_device_name(torch.cuda.current_device()),
        "software": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        },
        "config_sha256": resolved_config["config_sha256"],
    }
    (output_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
