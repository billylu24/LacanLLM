"""Plot eval loss versus epoch for the completed quantization sweep."""

import json
from pathlib import Path

import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_ROOT = PROJECT_ROOT / "adapters"
OUTPUT = PROJECT_ROOT / "experiments" / "quantization_epoch_comparison.png"


def main() -> None:
    groups = {"4-bit NF4": [], "8-bit": []}
    for metadata_path in sorted(ADAPTER_ROOT.glob("gemma4_e2b_*_5000/training_metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        bits = metadata.get("quantization_bits")
        loss = metadata.get("eval_metrics", {}).get("eval_loss")
        epoch = metadata.get("num_train_epochs")
        if loss is None:
            continue
        key = "4-bit NF4" if bits == 4 else "8-bit"
        groups[key].append((float(epoch), float(loss)))

    plt.figure(figsize=(9, 5.5))
    for label, points in groups.items():
        points.sort()
        if points:
            xs, ys = zip(*points)
            plt.plot(xs, ys, marker="o", linewidth=2, label=label)
            for x, y in points:
                plt.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    plt.xlabel("Training epoch")
    plt.ylabel("Validation loss (lower is better)")
    plt.title("Gemma 4 E2B: quantization and epoch comparison (5,000 samples)")
    plt.xticks([0.25, 0.5, 0.75, 1.0])
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUTPUT, dpi=180)
    print(OUTPUT)


if __name__ == "__main__":
    main()
