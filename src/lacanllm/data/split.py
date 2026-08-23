"""Source-isolated and capacity-aware evaluation splitting."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

Row = dict[str, Any]


def reserve_holdout_sources(
    rows: list[Row],
    *,
    validation_target: int,
    test_target: int,
    seed: int,
    per_source_cap: int,
) -> tuple[set[str], set[str], set[str]]:
    """Reserve the fewest high-capacity sources needed for two holdout splits.

    Sources not needed to satisfy holdout capacity remain available exclusively
    for SFT generation. This prevents a small evaluation set from consuming the
    entire source corpus.
    """

    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_file"])].append(row)
    if len(grouped) < 2:
        raise ValueError("At least two source files are required for isolated splitting")

    rng = random.Random(seed)
    sources = list(grouped)
    rng.shuffle(sources)
    sources.sort(key=lambda source: min(len(grouped[source]), per_source_cap), reverse=True)

    validation_sources: set[str] = set()
    test_sources: set[str] = set()
    capacities = {"validation": 0, "test": 0}
    for source in sources:
        validation_full = capacities["validation"] >= validation_target
        test_full = capacities["test"] >= test_target
        if validation_full and test_full:
            break
        contribution = min(len(grouped[source]), per_source_cap)
        if validation_full:
            destination = "test"
        elif test_full:
            destination = "validation"
        else:
            validation_completion = capacities["validation"] / validation_target
            test_completion = capacities["test"] / test_target
            destination = "validation" if validation_completion <= test_completion else "test"
        if destination == "validation":
            validation_sources.add(source)
        else:
            test_sources.add(source)
        capacities[destination] += contribution
    if capacities["validation"] < validation_target or capacities["test"] < test_target:
        raise ValueError(
            "Insufficient source-isolated capacity for requested holdouts: "
            f"validation={capacities['validation']}/{validation_target}, test={capacities['test']}/{test_target}"
        )
    training_sources = set(grouped) - validation_sources - test_sources
    if not training_sources:
        raise ValueError("Holdout reservation consumed every available source")
    return validation_sources, test_sources, training_sources


def select_with_source_cap(rows: list[Row], *, target: int, per_source_cap: int) -> list[Row]:
    ranked = sorted(
        rows,
        key=lambda row: (float(row["quality_score"]), float(row["provenance_score"]), row["qa_id"]),
        reverse=True,
    )
    selected: list[Row] = []
    source_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)

    # Round-robin over question types prevents the highest-scoring generic type
    # from occupying the entire held-out set.
    by_type: dict[str, list[Row]] = defaultdict(list)
    for row in ranked:
        by_type[str(row["question_type"])].append(row)
    type_order = sorted(by_type)
    while len(selected) < target and any(by_type.values()):
        type_order.sort(key=lambda item: (type_counts[item], item))
        made_progress = False
        for question_type in type_order:
            bucket = by_type[question_type]
            while bucket:
                row = bucket.pop(0)
                source = str(row["source_file"])
                if source_counts[source] >= per_source_cap:
                    continue
                selected.append(row)
                source_counts[source] += 1
                type_counts[question_type] += 1
                made_progress = True
                break
            if len(selected) >= target:
                break
        if not made_progress:
            break

    if len(selected) != target:
        raise ValueError(
            f"Could select only {len(selected)} of {target} requested rows under source cap {per_source_cap}"
        )
    return selected
