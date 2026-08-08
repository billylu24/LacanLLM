"""Run continuous 1.5-epoch Gemma 4 E2B QLoRA experiments.

Each quantization setting starts once from the base model. Evaluation and
checkpointing happen every quarter epoch, so the recorded curve is cumulative
within one run rather than a collection of independent restarts.
"""

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = PROJECT_ROOT / "experiments"
LOG_ROOT = EXPERIMENT_ROOT / "logs"
DATA_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs_checked.jsonl"
TRAIN_FILE = PROJECT_ROOT / "data" / "gemma4_e2b_continuous_5000_train.jsonl"
VALIDATION_FILE = PROJECT_ROOT / "data" / "gemma4_e2b_continuous_5000_validation.jsonl"

EXPERIMENTS = [
    {
        "name": "gemma4_e2b_4bit_nf4_continuous_1p5ep_5000",
        "bits": 4,
        "quant_type": "nf4",
    },
    {
        "name": "gemma4_e2b_8bit_continuous_1p5ep_5000",
        "bits": 8,
        "quant_type": "nf4",
    },
]


def run_prepare() -> None:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
        "--prepare-only",
        "--model-id",
        "google/gemma-4-E2B-it",
        "--raw-data-file",
        str(DATA_FILE),
        "--training-file",
        str(TRAIN_FILE),
        "--validation-file",
        str(VALIDATION_FILE),
        "--target-count",
        "5000",
        "--max-seq-length",
        "1024",
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as input_file:
        return sum(1 for _ in input_file)


def main() -> None:
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not available in this process.")

    run_prepare()
    train_rows = line_count(TRAIN_FILE)
    gradient_accumulation_steps = 4
    steps_per_epoch = math.ceil(train_rows / gradient_accumulation_steps)
    quarter_epoch_steps = max(1, round(steps_per_epoch * 0.25))

    env = os.environ.copy()
    env["HF_TOKEN"] = token
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = EXPERIMENT_ROOT / "continuous_manifest.jsonl"

    for config in EXPERIMENTS:
        name = config["name"]
        output_dir = PROJECT_ROOT / "adapters" / name
        checkpoint_dir = PROJECT_ROOT / "outputs" / name
        log_path = LOG_ROOT / f"{name}.log"
        metrics_path = EXPERIMENT_ROOT / f"{name}_metrics.jsonl"
        metadata_path = output_dir / "training_metadata.json"
        if metadata_path.exists():
            try:
                previous = json.loads(metadata_path.read_text(encoding="utf-8"))
                if previous.get("eval_metrics", {}).get("eval_loss") is not None:
                    print(f"SKIP {name}: existing successful metadata", flush=True)
                    continue
            except (OSError, json.JSONDecodeError):
                pass

        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
            "--skip-prepare",
            "--auto-resume",
            "--model-id",
            "google/gemma-4-E2B-it",
            "--raw-data-file",
            str(DATA_FILE),
            "--training-file",
            str(TRAIN_FILE),
            "--validation-file",
            str(VALIDATION_FILE),
            "--output-dir",
            str(output_dir),
            "--checkpoint-dir",
            str(checkpoint_dir),
            "--target-count",
            "5000",
            "--max-seq-length",
            "1024",
            "--num-train-epochs",
            "1.5",
            "--quantization-bits",
            str(config["bits"]),
            "--quantization-type",
            str(config["quant_type"]),
            "--experiment-name",
            name,
            "--run-name",
            name,
            "--gradient-accumulation-steps",
            str(gradient_accumulation_steps),
            "--logging-steps",
            "1",
            "--eval-steps",
            str(quarter_epoch_steps),
            "--save-steps",
            str(quarter_epoch_steps),
            "--save-total-limit",
            "10",
            "--metrics-file",
            str(metrics_path),
        ]
        started = time.time()
        record = {
            "experiment_name": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "quantization_bits": config["bits"],
            "quantization_type": config["quant_type"] if config["bits"] == 4 else None,
            "epochs": 1.5,
            "record_interval_epochs": 0.25,
            "target_count": 5000,
            "train_rows": train_rows,
            "steps_per_epoch": steps_per_epoch,
            "record_interval_steps": quarter_epoch_steps,
            "metrics_file": str(metrics_path),
            "log_file": str(log_path),
            "adapter_dir": str(output_dir),
        }
        print(f"START {name}: 0 -> 1.5 epochs, every 0.25 epoch", flush=True)
        has_checkpoint = any(checkpoint_dir.glob("checkpoint-*/trainer_state.json"))
        if not has_checkpoint:
            metrics_path.unlink(missing_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        record.update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.time() - started, 2),
            "returncode": result.returncode,
            "status": "success" if result.returncode == 0 else "failed",
        })
        with manifest_path.open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
