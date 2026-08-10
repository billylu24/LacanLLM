"""Deterministic data selection, splitting, and leakage auditing.

The command-line scripts intentionally stay thin.  The rules in this module
are pure Python so they can be unit-tested without downloading a model.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

Row = dict[str, Any]


def normalize_for_comparison(value: Any) -> str:
    """Normalize human text before duplicate and leakage comparisons."""

    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def text_fingerprint(value: Any) -> str:
    """Return a stable, non-reversible identifier for normalized text."""

    normalized = normalize_for_comparison(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash an artifact in chunks so experiment inputs can be identified exactly."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deduplicate_by_output(rows: Iterable[Row]) -> tuple[list[Row], int]:
    """Keep the first (therefore highest-ranked) row for each answer text."""

    unique: list[Row] = []
    seen: set[str] = set()
    duplicate_count = 0
    for row in rows:
        fingerprint = text_fingerprint(row.get("output"))
        if fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        unique.append(row)
    return unique, duplicate_count


def _source_groups(rows: list[Row]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {}
    for row in rows:
        source = normalize_for_comparison(row.get("source_file"))
        if source:
            groups.setdefault(source, []).append(row)
    return groups


def split_rows(
    rows: list[Row],
    *,
    validation_ratio: float,
    seed: int,
) -> tuple[list[Row], list[Row], str]:
    """Split rows deterministically, preferring whole-source isolation.

    When at least two usable sources exist, a source is never present in both
    splits.  Legacy datasets without provenance fall back to a seeded row split;
    output-level deduplication must happen before calling this function.
    """

    if not rows:
        raise ValueError("Cannot split an empty dataset.")
    if not 0 < validation_ratio < 1:
        raise ValueError("validation_ratio must be between 0 and 1.")

    rng = random.Random(seed)
    target_validation_rows = max(1, round(len(rows) * validation_ratio))
    groups = _source_groups(rows)

    if len(groups) >= 2 and sum(map(len, groups.values())) == len(rows):
        sources = list(groups)
        rng.shuffle(sources)
        validation_sources: set[str] = set()
        validation_count = 0
        for source in sources:
            if validation_count >= target_validation_rows and validation_sources:
                break
            validation_sources.add(source)
            validation_count += len(groups[source])

        # Never place every source in validation, even with very imbalanced files.
        if len(validation_sources) == len(sources):
            validation_sources.remove(sources[-1])

        train_rows = [
            row
            for row in rows
            if normalize_for_comparison(row.get("source_file")) not in validation_sources
        ]
        validation_rows = [
            row
            for row in rows
            if normalize_for_comparison(row.get("source_file")) in validation_sources
        ]
        strategy = "grouped_by_source"
    else:
        shuffled = list(rows)
        rng.shuffle(shuffled)
        validation_rows = shuffled[:target_validation_rows]
        train_rows = shuffled[target_validation_rows:]
        strategy = "seeded_row_fallback"

    if not train_rows or not validation_rows:
        raise ValueError("Split produced an empty train or validation partition.")
    rng.shuffle(train_rows)
    rng.shuffle(validation_rows)
    return train_rows, validation_rows, strategy


@dataclass(frozen=True)
class SplitAudit:
    train_rows: int
    validation_rows: int
    train_sources: int
    validation_sources: int
    missing_train_sources: int
    missing_validation_sources: int
    shared_sources: int
    shared_instructions: int
    shared_outputs: int
    split_strategy: str

    @property
    def leakage_free(self) -> bool:
        return self.shared_instructions == 0 and self.shared_outputs == 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "leakage_free": self.leakage_free}


def audit_split(
    train_rows: list[Row],
    validation_rows: list[Row],
    *,
    split_strategy: str,
) -> SplitAudit:
    """Measure exact normalized overlap between train and validation."""

    def values(rows: list[Row], field: str) -> set[str]:
        return {
            normalized
            for row in rows
            if (normalized := normalize_for_comparison(row.get(field)))
        }

    train_sources = values(train_rows, "source_file")
    validation_sources = values(validation_rows, "source_file")
    return SplitAudit(
        train_rows=len(train_rows),
        validation_rows=len(validation_rows),
        train_sources=len(train_sources),
        validation_sources=len(validation_sources),
        missing_train_sources=sum(not row.get("source_file") for row in train_rows),
        missing_validation_sources=sum(not row.get("source_file") for row in validation_rows),
        shared_sources=len(train_sources & validation_sources),
        shared_instructions=len(values(train_rows, "instruction") & values(validation_rows, "instruction")),
        shared_outputs=len(values(train_rows, "output") & values(validation_rows, "output")),
        split_strategy=split_strategy,
    )


def assert_no_content_leakage(audit: SplitAudit) -> None:
    """Fail fast instead of silently training on a contaminated split."""

    if not audit.leakage_free:
        raise ValueError(
            "Train/validation leakage detected: "
            f"{audit.shared_instructions} shared instructions and "
            f"{audit.shared_outputs} shared outputs."
        )


def write_audit(path: Path, audit: SplitAudit, **extra: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**audit.to_dict(), **extra}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_jsonl(path: Path) -> list[Row]:
    """Read JSONL with line-aware errors for easier debugging."""

    rows: list[Row] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object in {path} at line {line_number}.")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
