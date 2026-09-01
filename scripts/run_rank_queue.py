"""Run the controlled rank experiments serially on one GPU."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONFIGS = [
    Path("configs/training/r8_lr1e4.json"),
    Path("configs/training/r16_lr1e4.json"),
    Path("configs/training/r32_lr1e4.json"),
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_status(path: Path, status: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def checkpoint_exists(output_dir: Path) -> bool:
    return any(path.is_dir() for path in output_dir.glob("checkpoint-*"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", nargs="+", type=Path, default=DEFAULT_CONFIGS)
    parser.add_argument("--state-dir", type=Path, default=Path("artifacts/experiments/rank_queue"))
    parser.add_argument(
        "--evaluation-template",
        type=Path,
        default=Path("configs/evaluation/base_validation.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = (args.state_dir / "queue.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another rank experiment queue is already running") from error
    lock_handle.write(f"{os.getpid()}\n")
    lock_handle.flush()

    experiments = []
    for config_path in args.configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        experiments.append(
            {
                "name": config["experiment_name"],
                "config": str(config_path),
                "output_dir": config["output_dir"],
                "status": "pending",
            }
        )
    status: dict[str, Any] = {
        "queue_status": "running",
        "queue_pid": os.getpid(),
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "experiments": experiments,
    }
    status_path = args.state_dir / "status.json"
    write_status(status_path, status)

    for experiment in experiments:
        output_dir = Path(experiment["output_dir"])
        metadata_path = output_dir / "training_metadata.json"
        if not metadata_path.exists():
            command = [sys.executable, "-u", "scripts/train_qlora.py", "--config", experiment["config"]]
            if output_dir.exists() and any(output_dir.iterdir()):
                if not checkpoint_exists(output_dir):
                    experiment["status"] = "failed"
                    experiment["error"] = "non-empty output directory has no resumable checkpoint"
                    status["queue_status"] = "failed"
                    status["updated_at"] = utc_now()
                    write_status(status_path, status)
                    raise RuntimeError(experiment["error"])
                command.extend(["--resume-from-checkpoint", "auto"])

            experiment["status"] = "training"
            experiment["started_at"] = utc_now()
            experiment["training_command"] = command
            log_path = args.state_dir / f"{experiment['name']}.log"
            experiment["log"] = str(log_path)
            status["current_experiment"] = experiment["name"]
            status["updated_at"] = utc_now()
            with log_path.open("a", encoding="utf-8") as log_handle:
                process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
                experiment["training_pid"] = process.pid
                write_status(status_path, status)
                return_code = process.wait()
            experiment["training_return_code"] = return_code
            status["updated_at"] = utc_now()
            if return_code != 0 or not metadata_path.exists():
                experiment["status"] = "failed"
                experiment["error"] = "training command failed or did not produce training_metadata.json"
                status["queue_status"] = "failed"
                write_status(status_path, status)
                raise RuntimeError(experiment["error"])
        else:
            experiment["training_status"] = "completed_existing"

        evaluation_dir = output_dir / "validation_evaluation"
        evaluation_summary = evaluation_dir / "evaluation_summary.json"
        if not evaluation_summary.exists():
            evaluation_config = json.loads(args.evaluation_template.read_text(encoding="utf-8"))
            evaluation_config.update(
                {
                    "evaluation_name": f"{experiment['name']}_validation",
                    "output_dir": str(evaluation_dir),
                    "adapter_path": str(output_dir / "adapter"),
                }
            )
            evaluation_config_path = args.state_dir / f"{experiment['name']}_evaluation.json"
            evaluation_config_path.write_text(
                json.dumps(evaluation_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            evaluation_command = [
                sys.executable,
                "-u",
                "scripts/evaluate_validation.py",
                "--config",
                str(evaluation_config_path),
            ]
            experiment["status"] = "evaluating"
            experiment["evaluation_command"] = evaluation_command
            status["updated_at"] = utc_now()
            evaluation_log_path = args.state_dir / f"{experiment['name']}_evaluation.log"
            with evaluation_log_path.open("a", encoding="utf-8") as log_handle:
                process = subprocess.Popen(evaluation_command, stdout=log_handle, stderr=subprocess.STDOUT)
                experiment["evaluation_pid"] = process.pid
                write_status(status_path, status)
                return_code = process.wait()
            experiment["evaluation_return_code"] = return_code
            if return_code != 0 or not evaluation_summary.exists():
                experiment["status"] = "failed"
                experiment["error"] = "Validation evaluation failed or did not produce evaluation_summary.json"
                status["queue_status"] = "failed"
                status["updated_at"] = utc_now()
                write_status(status_path, status)
                raise RuntimeError(experiment["error"])

        experiment["status"] = "completed"
        experiment["finished_at"] = utc_now()
        status["updated_at"] = utc_now()
        write_status(status_path, status)

    status["queue_status"] = "completed"
    status.pop("current_experiment", None)
    status["finished_at"] = utc_now()
    status["updated_at"] = utc_now()
    write_status(status_path, status)


if __name__ == "__main__":
    main()
