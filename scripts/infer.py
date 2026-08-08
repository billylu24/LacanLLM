"""Run one-off inference with the fixed base model and optional LoRA adapter."""

import argparse
import os
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForMultimodalLM, AutoProcessor

DEFAULT_MODEL_ID = "google/gemma-4-E2B-it"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an answer from the Lacan assistant.")
    parser.add_argument("prompt")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--adapter-dir", type=Path, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN before loading the gated model.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for the configured 4B model.")

    processor = AutoProcessor.from_pretrained(args.model_id, token=token)
    model = AutoModelForMultimodalLM.from_pretrained(
        args.model_id,
        token=token,
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        device_map="auto",
        attn_implementation="sdpa",
    )
    if args.adapter_dir:
        model = PeftModel.from_pretrained(model, args.adapter_dir)
    model.eval()
    messages = [{"role": "user", "content": args.prompt}]
    inputs = processor.apply_chat_template(messages, tokenize=True, return_dict=True, return_tensors="pt", add_generation_prompt=True, enable_thinking=False).to(model.device)
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False, pad_token_id=processor.tokenizer.eos_token_id)
    answer = processor.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
    print(answer)


if __name__ == "__main__":
    main()
