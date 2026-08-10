"""Collect continuous training metadata and quarter-epoch metrics."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "experiments" / "summary.json"


def main() -> None:
    rows = []
    for metadata_path in sorted((PROJECT_ROOT / "adapters").glob("gemma4_e2b_*/training_metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        experiment_name = metadata.get("experiment_name") or metadata_path.parent.name
        comparison_group = (
            "v2_assistant_only_leakage_free"
            if "_v2_" in experiment_name
            else "historical_prompt_inclusive_leaky_split"
        )
        metrics_path = PROJECT_ROOT / "experiments" / f"{metadata_path.parent.name}_metrics.jsonl"
        curve = []
        if metrics_path.exists():
            curve = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows.append({
            "experiment_name": experiment_name,
            "comparison_group": comparison_group,
            "model_id": metadata.get("model_id"),
            "quantization_bits": metadata.get("quantization_bits"),
            "quantization_type": metadata.get("quantization_type"),
            "epochs": metadata.get("num_train_epochs"),
            "train_rows": metadata.get("train_rows"),
            "validation_rows": metadata.get("validation_rows"),
            "train_loss": metadata.get("train_metrics", {}).get("train_loss"),
            "eval_loss": metadata.get("eval_metrics", {}).get("eval_loss"),
            "train_runtime_seconds": metadata.get("train_metrics", {}).get("train_runtime"),
            "gpu": metadata.get("gpu"),
            "adapter_dir": metadata_path.parent.relative_to(PROJECT_ROOT).as_posix(),
            "metrics_file": metrics_path.relative_to(PROJECT_ROOT).as_posix(),
            "quarter_epoch_metrics": curve,
        })
    best_by_group = {}
    for group in sorted({row["comparison_group"] for row in rows}):
        candidates = [row for row in rows if row["comparison_group"] == group]
        best_by_group[group] = min(candidates, key=lambda row: row["eval_loss"] or float("inf"))
    summary = {
        "warning": "Validation loss is comparable only within the same comparison_group.",
        "experiments": rows,
        "best_by_group": best_by_group,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
