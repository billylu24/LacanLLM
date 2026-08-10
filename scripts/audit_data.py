"""Audit train/validation JSONL files for provenance and exact leakage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lacanllm.data import audit_split, read_jsonl

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-file",
        type=Path,
        default=PROJECT_ROOT / "data" / "gemma4_e2b_v2_3000_train.jsonl",
    )
    parser.add_argument(
        "--validation-file",
        type=Path,
        default=PROJECT_ROOT / "data" / "gemma4_e2b_v2_3000_validation.jsonl",
    )
    parser.add_argument("--strict", action="store_true", help="Exit with an error when leakage is found.")
    args = parser.parse_args()

    audit = audit_split(
        read_jsonl(args.train_file),
        read_jsonl(args.validation_file),
        split_strategy="existing_files",
    )
    print(json.dumps(audit.to_dict(), indent=2, ensure_ascii=False))
    if args.strict and not audit.leakage_free:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
