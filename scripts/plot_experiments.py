"""Plot every available Gemma experiment curve from metrics JSONL files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = PROJECT_ROOT / "experiments"


def readable_label(metrics_path: Path) -> str:
    name = metrics_path.stem.removesuffix("_metrics")
    return name.replace("gemma4_e2b_", "").replace("_", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=EXPERIMENT_ROOT / "quantization_epoch_comparison.png",
    )
    args = parser.parse_args()

    metrics_files = sorted(EXPERIMENT_ROOT.glob("gemma4_e2b_*_metrics.jsonl"))
    if not metrics_files:
        raise FileNotFoundError(f"No metrics JSONL files found in {EXPERIMENT_ROOT}")

    plt.figure(figsize=(10, 6))
    for metrics_path in metrics_files:
        points = []
        for line in metrics_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("epoch") is not None and row.get("eval_loss") is not None:
                points.append((float(row["epoch"]), float(row["eval_loss"])))
        points.sort()
        if points:
            epochs, losses = zip(*points)
            plt.plot(epochs, losses, marker="o", linewidth=2, label=readable_label(metrics_path))

    plt.xlabel("Training epoch")
    plt.ylabel("Validation loss (lower is better)")
    plt.title("Gemma 4 E2B QLoRA experiment comparison")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    plt.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(args.output, dpi=180)
    print(args.output)


if __name__ == "__main__":
    main()
