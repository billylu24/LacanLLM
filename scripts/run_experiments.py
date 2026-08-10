"""Run the experiment matrix declared in ``configs/experiments.toml``.

The configuration file contains experiment choices; this script only handles
orchestration, logging, resume behavior, and failure reporting.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "experiments.toml"


def project_path(value: str) -> Path:
    """Resolve a repository-relative path without depending on the shell cwd."""

    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if "run" not in config or not config.get("experiments"):
        raise ValueError("Config must contain [run] and at least one [[experiments]] entry.")
    return config


def line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def run_with_live_log(command: list[str], *, environment: dict[str, str], log_path: Path) -> int:
    """Stream child output to both the visible terminal and a persistent UTF-8 log."""

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process.stdout is None:
            raise RuntimeError("Failed to capture the training process output.")
        while character := process.stdout.read(1):
            sys.stdout.write(character)
            log.write(character)
            if character in {"\n", "\r"}:
                sys.stdout.flush()
                log.flush()
        return process.wait()


def prepare_data(settings: dict[str, Any]) -> None:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
        "--prepare-only",
        "--model-id",
        settings["model_id"],
        "--raw-data-file",
        str(project_path(settings["data_file"])),
        "--training-file",
        str(project_path(settings["train_file"])),
        "--validation-file",
        str(project_path(settings["validation_file"])),
        "--split-audit-file",
        str(project_path(settings["split_audit_file"])),
        "--target-count",
        str(settings["target_count"]),
        "--val-ratio",
        str(settings["validation_ratio"]),
        "--max-seq-length",
        str(settings["max_seq_length"]),
        "--seed",
        str(settings["seed"]),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def build_training_command(
    settings: dict[str, Any],
    experiment: dict[str, Any],
    *,
    train_file: Path,
    validation_file: Path,
    output_dir: Path,
    checkpoint_dir: Path,
    metrics_path: Path,
    evaluation_steps: int,
) -> list[str]:
    name = experiment["name"]
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
        "--skip-prepare",
        "--auto-resume",
        "--model-id",
        settings["model_id"],
        "--training-file",
        str(train_file),
        "--validation-file",
        str(validation_file),
        "--split-audit-file",
        str(project_path(settings["split_audit_file"])),
        "--output-dir",
        str(output_dir),
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--max-seq-length",
        str(settings["max_seq_length"]),
        "--num-train-epochs",
        str(settings["epochs"]),
        "--quantization-bits",
        str(experiment["bits"]),
        "--quantization-type",
        experiment.get("quantization_type", "nf4"),
        "--experiment-name",
        name,
        "--run-name",
        name,
        "--seed",
        str(settings["seed"]),
        "--gradient-accumulation-steps",
        str(settings["gradient_accumulation_steps"]),
        "--per-device-train-batch-size",
        str(settings["per_device_train_batch_size"]),
        "--logging-steps",
        str(settings["logging_steps"]),
        "--eval-steps",
        str(evaluation_steps),
        "--save-steps",
        str(settings["checkpoint_steps"]),
        "--save-total-limit",
        str(settings["save_total_limit"]),
        "--metrics-file",
        str(metrics_path),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    settings = config["run"]
    experiments = config["experiments"]
    data_file = project_path(settings["data_file"])
    if not data_file.exists():
        raise FileNotFoundError(data_file)
    prepare_data(settings)
    if args.prepare_only:
        print("Data preparation and leakage audit completed; training was not started.")
        return

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not available in this process.")

    train_file = project_path(settings["train_file"])
    validation_file = project_path(settings["validation_file"])
    experiment_root = project_path(settings["experiment_root"])
    log_root = experiment_root / "logs"
    adapter_root = project_path(settings["adapter_root"])
    checkpoint_root = project_path(settings["checkpoint_root"])
    log_root.mkdir(parents=True, exist_ok=True)

    train_rows = line_count(train_file)
    effective_batch = settings["per_device_train_batch_size"] * settings["gradient_accumulation_steps"]
    steps_per_epoch = math.ceil(train_rows / effective_batch)
    evaluation_steps = max(1, round(steps_per_epoch * settings["evaluation_interval_epochs"]))
    manifest_path = experiment_root / "continuous_manifest.jsonl"
    environment = os.environ.copy()
    environment["HF_TOKEN"] = token
    environment["PYTHONUNBUFFERED"] = "1"

    for experiment in experiments:
        name = experiment["name"]
        output_dir = adapter_root / name
        checkpoint_dir = checkpoint_root / name
        log_path = log_root / f"{name}.log"
        metrics_path = experiment_root / f"{name}_metrics.jsonl"
        metadata_path = output_dir / "training_metadata.json"

        if metadata_path.exists():
            try:
                previous = json.loads(metadata_path.read_text(encoding="utf-8"))
                if previous.get("eval_metrics", {}).get("eval_loss") is not None:
                    print(f"SKIP {name}: existing successful metadata", flush=True)
                    continue
            except (OSError, json.JSONDecodeError):
                pass

        command = build_training_command(
            settings,
            experiment,
            train_file=train_file,
            validation_file=validation_file,
            output_dir=output_dir,
            checkpoint_dir=checkpoint_dir,
            metrics_path=metrics_path,
            evaluation_steps=evaluation_steps,
        )
        started = time.time()
        record = {
            "experiment_name": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "quantization_bits": experiment["bits"],
            "quantization_type": experiment.get("quantization_type") if experiment["bits"] == 4 else None,
            "epochs": settings["epochs"],
            "record_interval_epochs": settings["evaluation_interval_epochs"],
            "target_count": settings["target_count"],
            "train_rows": train_rows,
            "steps_per_epoch": steps_per_epoch,
            "record_interval_steps": evaluation_steps,
            "safety_checkpoint_steps": settings["checkpoint_steps"],
            "metrics_file": metrics_path.relative_to(PROJECT_ROOT).as_posix(),
            "log_file": log_path.relative_to(PROJECT_ROOT).as_posix(),
            "adapter_dir": output_dir.relative_to(PROJECT_ROOT).as_posix(),
        }
        total_steps = max(1, math.ceil(steps_per_epoch * settings["epochs"]))
        historical_steps_per_second = 0.391 if experiment["bits"] == 4 else 0.077
        estimated_minutes = total_steps / historical_steps_per_second / 60
        print(
            f"START {name}: {total_steps} optimizer steps, approximately "
            f"{estimated_minutes:.0f} minutes on the historical RTX 5070. "
            "The Trainer progress bar will update the ETA live.",
            flush=True,
        )
        has_checkpoint = any(checkpoint_dir.glob("checkpoint-*/trainer_state.json"))
        if not has_checkpoint:
            metrics_path.unlink(missing_ok=True)

        returncode = run_with_live_log(command, environment=environment, log_path=log_path)
        record.update(
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": round(time.time() - started, 2),
                "returncode": returncode,
                "status": "success" if returncode == 0 else "failed",
            }
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if returncode != 0:
            raise SystemExit(f"Experiment {name} failed; inspect {log_path}")


if __name__ == "__main__":
    main()
