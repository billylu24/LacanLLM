"""Lightweight text metrics used by the reproducible evaluation script."""

from __future__ import annotations

import re


def word_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.casefold()))


def reference_overlap(prediction: str, reference: str) -> dict[str, float]:
    """Compute transparent lexical precision/recall/F1 diagnostics.

    These metrics are intentionally described as diagnostics, not as proof of
    theoretical correctness.  Human/domain evaluation remains necessary.
    """

    prediction_tokens = word_tokens(prediction)
    reference_tokens = word_tokens(reference)
    shared = prediction_tokens & reference_tokens
    precision = len(shared) / max(1, len(prediction_tokens))
    recall = len(shared) / max(1, len(reference_tokens))
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return {
        "lexical_precision": round(precision, 4),
        "lexical_recall": round(recall, 4),
        "lexical_f1": round(f1, 4),
    }
