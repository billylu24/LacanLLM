"""Run a small Gemma 4 E2B quantization/epoch matrix and record every result."""

import json
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

EXPERIMENTS = [
    {"name": "gemma4_e2b_4bit_nf4_025ep_5000", "bits": 4, "quant_type": "nf4", "epochs": 0.25, "target_count": 5000, "save_steps": 297},
    {"name": "gemma4_e2b_4bit_nf4_050ep_5000", "bits": 4, "quant_type": "nf4", "epochs": 0.50, "target_count": 5000, "save_steps": 594},
    {"name": "gemma4_e2b_4bit_nf4_075ep_5000", "bits": 4, "quant_type": "nf4", "epochs": 0.75, "target_count": 5000, "save_steps": 891},
    {"name": "gemma4_e2b_4bit_nf4_100ep_5000", "bits": 4, "quant_type": "nf4", "epochs": 1.00, "target_count": 5000, "save_steps": 1188},
    {"name": "gemma4_e2b_8bit_025ep_5000", "bits": 8, "quant_type": "nf4", "epochs": 0.25, "target_count": 5000, "save_steps": 297},
    {"name": "gemma4_e2b_8bit_050ep_5000", "bits": 8, "quant_type": "nf4", "epochs": 0.50, "target_count": 5000, "save_steps": 594},
    {"name": "gemma4_e2b_8bit_075ep_5000", "bits": 8, "quant_type": "nf4", "epochs": 0.75, "target_count": 5000, "save_steps": 891},
    {"name": "gemma4_e2b_8bit_100ep_5000", "bits": 8, "quant_type": "nf4", "epochs": 1.00, "target_count": 5000, "save_steps": 1188},
]


def main() -> None:
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HF_TOKEN_USER")
    if not token:
        token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not available in this process.")
    env = os.environ.copy()
    env["HF_TOKEN"] = token
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = EXPERIMENT_ROOT / "experiment_manifest_v2.jsonl"

    for config in EXPERIMENTS:
        name = config["name"]
        train_file = PROJECT_ROOT / "data" / f"{name}_train.jsonl"
        val_file = PROJECT_ROOT / "data" / f"{name}_validation.jsonl"
        output_dir = PROJECT_ROOT / "adapters" / name
        checkpoint_dir = PROJECT_ROOT / "outputs" / name
        log_path = LOG_ROOT / f"{name}.log"
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
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
            "--model-id", "google/gemma-4-E2B-it",
            "--raw-data-file", str(DATA_FILE),
            "--training-file", str(train_file),
            "--validation-file", str(val_file),
            "--output-dir", str(output_dir),
            "--checkpoint-dir", str(checkpoint_dir),
            "--target-count", str(config["target_count"]),
            "--max-seq-length", "1024",
            "--num-train-epochs", str(config["epochs"]),
            "--quantization-bits", str(config["bits"]),
            "--quantization-type", str(config["quant_type"]),
            "--experiment-name", name,
            "--run-name", name,
            "--gradient-accumulation-steps", "4",
            "--logging-steps", "1",
            "--eval-steps", "16",
            "--save-steps", str(config["save_steps"]),
            "--save-total-limit", "1",
        ]
        started = time.time()
        record = {
            "experiment_name": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "quantization_bits": config["bits"],
            "quantization_type": config["quant_type"] if config["bits"] == 4 else None,
            "epochs": config["epochs"],
            "target_count": config["target_count"],
            "max_seq_length": 1024,
            "log_file": str(log_path),
            "adapter_dir": str(output_dir),
        }
        print(f"START {name}", flush=True)
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
