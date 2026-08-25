"""Command-line interface for reproducible QLoRA search and evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lacanllm.training.config import DEFAULT_CONFIG, ExperimentConfig
from lacanllm.training.preflight import preflight
from lacanllm.training.search import evaluate_test, evaluate_top, run_search


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "smoke",
            "benchmark-backend",
            "search",
            "status",
            "evaluate-top",
            "evaluate-test",
            "summarize",
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--trials", type=int)
    parser.add_argument("--count", type=int)
    parser.add_argument("--backend", choices=("native", "unsloth"), default="unsloth")
    parser.add_argument("--no-tokenize", action="store_true")
    return parser.parse_args(argv)


def execute(args: argparse.Namespace, config: ExperimentConfig) -> dict[str, Any]:
    if args.command == "preflight":
        return preflight(config, tokenize=not args.no_tokenize)
    if args.command == "smoke":
        return smoke(config)
    if args.command == "search":
        return run_search(config, trials=args.trials)
    if args.command == "status":
        return status(config)
    if args.command == "evaluate-top":
        return evaluate_top(config, count=args.count)
    if args.command == "evaluate-test":
        return evaluate_test(config)
    if args.command == "summarize":
        return summarize(config)
    if args.command == "benchmark-backend":
        return benchmark_backend(config, args.backend)
    raise ValueError(f"Unsupported command: {args.command}")


def status(config: ExperimentConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "config_hash": config.config_hash,
        "artifact_root": str(config.artifact_root),
        "preflight": (config.artifact_root / "preflight.json").is_file(),
        "winner_locked": (config.artifact_root / "winner.json").is_file(),
        "test_completed": (config.artifact_root / "test_evaluation_complete.json").is_file(),
    }
    database = config.artifact_root / "study.sqlite3"
    if database.is_file():
        import optuna

        study = optuna.load_study(
            study_name=str(config.raw["experiment_version"]),
            storage=f"sqlite:///{database}",
        )
        result["trials"] = {
            state.name: sum(trial.state == state for trial in study.trials)
            for state in optuna.trial.TrialState
        }
        has_values = any(trial.value is not None for trial in study.trials)
        result["best_trial"] = study.best_trial.number if has_values else None
    else:
        result["trials"] = {}
        result["best_trial"] = None
    return result


def summarize(config: ExperimentConfig) -> dict[str, Any]:
    result = status(config)
    for name in ("backend_benchmark", "search_summary", "top_evaluation", "winner", "test_evaluation_complete"):
        path = config.artifact_root / f"{name}.json"
        if path.is_file():
            result[name] = json.loads(path.read_text(encoding="utf-8"))
    return result


def benchmark_backend(config: ExperimentConfig, backend: str) -> dict[str, Any]:
    from lacanllm.training.benchmark import run_benchmark

    return run_benchmark(config, backend=backend)


def smoke(config: ExperimentConfig) -> dict[str, Any]:
    """Train one real step, reload its adapter, and create a prediction artifact."""
    from argparse import Namespace

    from lacanllm.training.config import TrialParams
    from lacanllm.training.data import read_rows
    from lacanllm.training.runtime import train_unsloth_trial
    from lacanllm.training.search import _materialize_rows
    from lacanllm.training.worker import predict

    params = TrialParams.from_dict(config.raw["search"]["anchors"][0])
    training = train_unsloth_trial(config, 999, params, max_steps=1, row_limit=2)
    smoke_dir = config.artifact_root / "evaluations" / "smoke"
    rows_path = smoke_dir / "rows.jsonl"
    output_path = smoke_dir / "prediction.jsonl"
    rows = read_rows(config.path("validation"))[:1]
    _materialize_rows(rows_path, rows)
    predict(
        config,
        Namespace(
            rows_file=rows_path,
            output=output_path,
            adapter_dir=str(training["adapter_dir"]),
        ),
    )
    predictions = read_rows(output_path)
    expected_id = str(rows[0]["id"])
    if len(predictions) != 1 or str(predictions[0].get("id")) != expected_id:
        raise RuntimeError("Smoke prediction artifact does not contain the expected row")
    if not str(predictions[0].get("prediction", "")).strip():
        raise RuntimeError("Smoke prediction is empty")
    return {
        **training,
        "prediction_smoke": {
            "status": "completed",
            "rows": len(predictions),
            "output": str(output_path),
        },
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = ExperimentConfig.load(args.config)
    print(json.dumps(execute(args, config), indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
