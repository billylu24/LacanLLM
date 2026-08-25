"""Isolated GPU workers for training, prediction, and E4B judging."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lacanllm.training.config import DEFAULT_CONFIG, ExperimentConfig, TrialParams
from lacanllm.training.data import read_rows


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def train(config: ExperimentConfig, args: argparse.Namespace) -> None:
    from lacanllm.training.runtime import train_unsloth_trial

    params = TrialParams.from_dict(json.loads(args.params_json))
    train_unsloth_trial(config, args.trial, params)


def predict(config: ExperimentConfig, args: argparse.Namespace) -> None:
    from lacanllm.training.runtime import LocalGenerator

    rows = read_rows(args.rows_file)
    existing = {str(row["id"]) for row in read_rows(args.output)} if args.output.is_file() else set()
    missing = [row for row in rows if str(row["id"]) not in existing]
    if not missing:
        return
    adapter_dir = Path(args.adapter_dir) if args.adapter_dir else None
    generator = LocalGenerator(str(config.raw["model_id"]), adapter_dir)
    try:
        for row in missing:
            record = generator.generate(
                [row],
                max_new_tokens=int(config.raw["evaluation"]["max_new_tokens"]),
            )[0]
            _append_jsonl(args.output, record)
    finally:
        generator.close()


def judge(config: ExperimentConfig, args: argparse.Namespace) -> None:
    from lacanllm.training.runtime import PairJudge

    rows = read_rows(args.rows_file)
    candidate = {str(row["id"]): str(row["prediction"]) for row in read_rows(args.candidate)}
    baseline = {str(row["id"]): str(row["prediction"]) for row in read_rows(args.baseline)}
    backend = PairJudge(str(config.raw["judge_model_id"]))
    try:
        backend.evaluate(
            rows,
            candidate,
            baseline,
            output_path=args.output,
            max_new_tokens=int(config.raw["evaluation"]["judge_max_new_tokens"]),
        )
    finally:
        backend.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("train", "predict", "judge"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--trial", type=int)
    parser.add_argument("--params-json")
    parser.add_argument("--rows-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--adapter-dir", default="")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--baseline", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ExperimentConfig.load(args.config)
    {"train": train, "predict": predict, "judge": judge}[args.command](config, args)


if __name__ == "__main__":
    main()
