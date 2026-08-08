"""Evaluate a trained adapter on held-out JSONL examples."""

import argparse
import json
import os
import re
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForMultimodalLM, AutoProcessor

DEFAULT_MODEL_ID = "google/gemma-4-E2B-it"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate adapter output against held-out reference passages.")
    parser.add_argument("--data-file", type=Path, default=PROJECT_ROOT / "data" / "lacan_validation_data.jsonl")
    parser.add_argument("--adapter-dir", type=Path, required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output-file", type=Path, default=PROJECT_ROOT / "outputs" / "evaluation.jsonl")
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN before loading the gated model.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for evaluation.")

    rows = []
    with args.data_file.open(encoding="utf-8") as handle:
        for line in handle:
            if len(rows) >= args.limit:
                break
            row = json.loads(line)
            rows.append(row)
    processor = AutoProcessor.from_pretrained(args.model_id, token=token)
    model = AutoModelForMultimodalLM.from_pretrained(args.model_id, token=token, torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16, device_map="auto", attn_implementation="sdpa")
    model = PeftModel.from_pretrained(model, args.adapter_dir)
    model.eval()
    results = []
    for row in rows:
        inputs = processor.apply_chat_template([{"role": "user", "content": row["instruction"]}], tokenize=True, return_dict=True, return_tensors="pt", add_generation_prompt=True, enable_thinking=False).to(model.device)
        with torch.no_grad():
            output = model.generate(**inputs, max_new_tokens=512, do_sample=False, pad_token_id=processor.tokenizer.eos_token_id)
        answer = processor.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
        reference_tokens = tokens(row["output"])
        overlap = len(tokens(answer) & reference_tokens) / max(1, len(tokens(answer)))
        results.append({"instruction": row["instruction"], "reference": row["output"], "prediction": answer, "prediction_reference_token_overlap": round(overlap, 4)})
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with args.output_file.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    mean_overlap = sum(item["prediction_reference_token_overlap"] for item in results) / max(1, len(results))
    print(json.dumps({"examples": len(results), "mean_prediction_reference_token_overlap": round(mean_overlap, 4), "output_file": str(args.output_file)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
