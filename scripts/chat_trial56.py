#!/usr/bin/env python3
"""Run the Trial 56 QLoRA adapter in one-shot or interactive chat mode."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_ADAPTER = PROJECT_ROOT / "artifacts" / "training" / "qlora_v1" / "trials" / "trial-056" / "adapter"
DEFAULT_MODEL = "google/gemma-4-E2B-it"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", "-q", help="Ask one question and exit; omit for interactive mode")
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=192)
    return parser.parse_args()


def ask(generator: object, question: str, max_new_tokens: int) -> str:
    row = {"id": "interactive", "question_type": "interactive", "question": question}
    result = generator.generate([row], max_new_tokens=max_new_tokens)[0]
    return str(result["prediction"])


def main() -> None:
    args = parse_args()
    adapter = args.adapter.expanduser().resolve()
    weights = adapter / "adapter_model.safetensors"
    if not weights.is_file():
        raise SystemExit(f"Trial 56 adapter weights were not found: {weights}")
    if args.max_new_tokens < 1:
        raise SystemExit("--max-new-tokens must be at least 1")

    # Import lazily so --help works even outside the GPU environment.
    from lacanllm.training.runtime import LocalGenerator

    print(f"Loading {args.model} with adapter {adapter} ...", file=sys.stderr)
    generator = LocalGenerator(args.model, adapter)
    try:
        if args.question:
            print(ask(generator, args.question.strip(), args.max_new_tokens))
            return

        print("Trial 56 is ready. Enter a question; use Ctrl-D or /quit to exit.")
        while True:
            try:
                question = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if question.lower() in {"/quit", "/exit", "quit", "exit"}:
                break
            if not question:
                continue
            print(f"Trial56> {ask(generator, question, args.max_new_tokens)}")
    finally:
        generator.close()


if __name__ == "__main__":
    main()
