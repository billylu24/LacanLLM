"""Validate, filter, and audit synthetic Lacan SFT pairs."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from lacanllm.data import normalize_for_comparison

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "lacan_sft_pairs_raw.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lacan_sft_pairs.jsonl"
MIN_OUTPUT_CHARS = 120
MAX_OUTPUT_CHARS = 6000
BAD_MARKERS = ("\ufffd", "鈥", "禄", "漏", "脡", "锛", "绔", "骃")
BAD_PHRASES = ("i cannot", "as an ai", "text provided does not", "context is missing")
BOILERPLATE = ("isbn", "all rights reserved", "library of congress", "copyright", "www.", "contents")


def read_rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                yield line_number, None, "invalid_json"
                continue
            yield line_number, row, None


def validate(row: dict, seen: set[str]) -> str | None:
    question = str(row.get("instruction", "")).strip()
    answer = str(row.get("output", "")).strip()
    if len(question) < 12 or "?" not in question:
        return "invalid_question"
    if len(answer) < MIN_OUTPUT_CHARS or len(answer) > MAX_OUTPUT_CHARS:
        return "invalid_answer_length"
    lowered = answer.lower()
    if any(marker in answer for marker in BAD_MARKERS):
        return "mojibake"
    if any(phrase in lowered for phrase in BAD_PHRASES):
        return "refusal_or_placeholder"
    if any(term in lowered for term in BOILERPLATE):
        return "boilerplate"
    normalized = re.sub(r"\s+", " ", f"{question}\n{answer}").strip().lower()
    if normalized in seen:
        return "duplicate"
    seen.add(normalized)
    return None


def canonicalize(row: dict) -> dict:
    return {
        "schema_version": 1,
        "instruction": str(row.get("instruction", "")).strip(),
        "input": str(row.get("input", "")).strip(),
        "output": str(row.get("output", "")).strip(),
        "source_file": row.get("source_file"),
        "paragraph_index": row.get("paragraph_index"),
        "char_count": len(str(row.get("output", "")).strip()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter and audit SFT JSONL data.")
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample-size", type=int, default=300)
    args = parser.parse_args()
    if not args.input_file.exists():
        raise FileNotFoundError(args.input_file)

    kept = []
    rejected = Counter()
    seen = set()
    for _, row, parse_error in read_rows(args.input_file):
        if parse_error:
            rejected[parse_error] += 1
            continue
        reason = validate(row, seen)
        if reason:
            rejected[reason] += 1
        else:
            kept.append(canonicalize(row))

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with args.output_file.open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    sample = kept[: min(args.sample_size, len(kept))]
    unique_outputs = {normalize_for_comparison(row["output"]) for row in kept}
    report = {
        "input_file": str(args.input_file),
        "output_file": str(args.output_file),
        "input_rows": sum(rejected.values()) + len(kept),
        "kept_rows": len(kept),
        "rejected_rows": sum(rejected.values()),
        "rejection_reasons": dict(rejected),
        "missing_source_file": sum(not row.get("source_file") for row in kept),
        "missing_paragraph_index": sum(row.get("paragraph_index") is None for row in kept),
        "unique_outputs": len(unique_outputs),
        "duplicate_outputs": len(kept) - len(unique_outputs),
        "sample_size": len(sample),
        "sample_question_chars": [len(row["instruction"]) for row in sample],
        "sample_answer_chars": [len(row["output"]) for row in sample],
    }
    report_path = args.output_file.with_name("quality_report.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
