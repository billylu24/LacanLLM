import argparse
import json
import os
from pathlib import Path

import torch
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_FILE = PROJECT_ROOT / "data" / "lacan_dataset.jsonl"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs.jsonl"

DEFAULT_MODEL_ID = "google/gemma-3-4b-it"
MIN_TEXT_CHARS = 160
MAX_INPUT_TOKENS = 2048
MAX_NEW_TOKENS = 120


def require_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in the environment before loading gated HF models.")
    return token


def load_model(model_id: str, token: str):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for this generation script.")

    tokenizer = AutoTokenizer.from_pretrained(model_id, token=token)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        device_map="auto",
        token=token,
        attn_implementation="sdpa",
    )
    model.eval()
    return model, tokenizer


def generate_question(model, tokenizer, lacan_text: str) -> str | None:
    prompt = (
        "You are preparing supervised fine-tuning data for a scholarly assistant "
        "specialized in Jacques Lacan.\n\n"
        "Given the Lacan passage below, write one concise English question that the "
        "passage could answer. Only output the question.\n\n"
        f"Passage:\n{lacan_text}"
    )
    messages = [{"role": "user", "content": prompt}]
    text_input = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer(
        text_input,
        return_tensors="pt",
        max_length=MAX_INPUT_TOKENS,
        truncation=True,
    ).to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=0.3,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated_ids = outputs[0][inputs["input_ids"].shape[-1] :]
    question = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    question = question.strip('"').strip()
    for prefix in ("Question:", "The question is:", "Here is the question:"):
        if question.lower().startswith(prefix.lower()):
            question = question[len(prefix) :].strip()
    if len(question) < 8 or "?" not in question:
        return None
    return question


def iter_input_rows(path: Path):
    with path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = row.get("text", "").strip()
            if len(text) >= MIN_TEXT_CHARS:
                yield row, text


def count_existing_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as output_file:
        return sum(1 for _ in output_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate instruction/output SFT pairs.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT_FILE)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not args.input_file.exists():
        raise FileNotFoundError(args.input_file)

    rows = list(iter_input_rows(args.input_file))
    processed = count_existing_rows(args.output_file)
    remaining = rows[processed:]
    if args.limit is not None:
        remaining = remaining[: args.limit]

    print(f"Valid passages: {len(rows)}")
    print(f"Already processed: {processed}")
    print(f"This run: {len(remaining)}")

    token = require_hf_token()
    model, tokenizer = load_model(args.model_id, token)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with args.output_file.open("a", encoding="utf-8") as output:
        for row, text in tqdm(remaining, desc="Generating SFT pairs"):
            question = generate_question(model, tokenizer, text)
            if not question:
                continue
            entry = {
                "instruction": question,
                "input": "",
                "output": text,
                "source_file": row.get("source_file"),
                "paragraph_index": row.get("paragraph_index"),
            }
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
            output.flush()


if __name__ == "__main__":
    main()
