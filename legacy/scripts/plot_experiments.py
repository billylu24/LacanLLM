"""Plot the cumulative eval-loss curves for the continuous runs."""

import json
from pathlib import Path

import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "experiments" / "quantization_epoch_comparison.png"


def main() -> None:
    groups = {
        "4-bit NF4": PROJECT_ROOT / "experiments" / "gemma4_e2b_4bit_nf4_continuous_1p5ep_5000_metrics.jsonl",
        "8-bit": PROJECT_ROOT / "experiments" / "gemma4_e2b_8bit_continuous_1p5ep_5000_metrics.jsonl",
    }

    plt.figure(figsize=(9, 5.5))
    for label, metrics_path in groups.items():
        points = []
        if metrics_path.exists():
            for line in metrics_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("epoch") is not None and row.get("eval_loss") is not None:
                    points.append((float(row["epoch"]), float(row["eval_loss"])))
        points.sort()
        if points:
            xs, ys = zip(*points)
            plt.plot(xs, ys, marker="o", linewidth=2, label=label)
            for x, y in points:
                plt.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    plt.xlabel("Training epoch")
    plt.ylabel("Validation loss (lower is better)")
    plt.title("Gemma 4 E2B: continuous 1.5-epoch comparison (5,000 samples)")
    plt.xticks([0.25, 0.5, 0.75, 1.0, 1.25, 1.5])
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUTPUT, dpi=180)
    print(OUTPUT)


if __name__ == "__main__":
    main()
