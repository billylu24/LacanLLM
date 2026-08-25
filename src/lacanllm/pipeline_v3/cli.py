from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from .config import load_config
from .stages import (
    apply_hard_filter,
    audit,
    clean,
    deduplicate,
    generate,
    judge,
    preflight,
    queue,
    run,
    seal,
    select,
    smoke,
    split,
    status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lacanllm-pipeline-v3", description="LacanLLM resumable data Pipeline v3")
    parser.add_argument("--config", required=True, help="Pipeline v3 JSON configuration")
    parser.add_argument(
        "--backend", choices=("fake", "transformers"), help="Explicit backend override (fake is tests only)"
    )
    parser.add_argument("--no-remote-preflight", action="store_true", help="Skip Hugging Face revision resolution")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "preflight",
        "clean",
        "split",
        "queue",
        "generate",
        "hard-filter",
        "deduplicate",
        "judge",
        "select",
        "seal",
        "audit",
        "status",
        "smoke",
        "run",
    ):
        subparsers.add_parser(name)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    simple: dict[str, Callable[..., dict[str, Any]]] = {
        "clean": clean,
        "split": split,
        "queue": queue,
        "hard-filter": apply_hard_filter,
        "deduplicate": deduplicate,
        "select": select,
        "seal": seal,
        "audit": audit,
        "status": status,
    }
    if args.command in simple:
        result = simple[args.command](config)
    elif args.command == "preflight":
        result = preflight(config, check_remote=not args.no_remote_preflight)
    elif args.command == "generate":
        result = generate(config, backend=args.backend)
    elif args.command == "judge":
        result = judge(config, backend=args.backend)
    elif args.command == "smoke":
        result = smoke(config, backend=args.backend, remote_preflight=not args.no_remote_preflight)
    elif args.command == "run":
        result = run(config, backend=args.backend, remote_preflight=not args.no_remote_preflight)
    else:  # pragma: no cover
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
