import json

import pytest

from lacanllm.data import (
    assert_no_content_leakage,
    audit_split,
    deduplicate_by_output,
    read_jsonl,
    split_rows,
)


def row(index: int, *, source: str | None = None, output: str | None = None) -> dict:
    return {
        "instruction": f"Question {index}?",
        "output": output or f"Answer {index}",
        "source_file": source,
    }


def test_deduplicate_by_output_keeps_first_ranked_row() -> None:
    rows = [row(1, output="Same answer"), row(2, output="  same   ANSWER "), row(3)]

    unique, removed = deduplicate_by_output(rows)

    assert [item["instruction"] for item in unique] == ["Question 1?", "Question 3?"]
    assert removed == 1


def test_grouped_split_keeps_sources_isolated() -> None:
    rows = [row(i, source="seminar-a.txt") for i in range(6)] + [
        row(i + 10, source="seminar-b.txt") for i in range(6)
    ]

    train, validation, strategy = split_rows(rows, validation_ratio=0.25, seed=3407)
    audit = audit_split(train, validation, split_strategy=strategy)

    assert strategy == "grouped_by_source"
    assert audit.shared_sources == 0
    assert audit.leakage_free


def test_missing_provenance_uses_deterministic_fallback() -> None:
    rows = [row(i) for i in range(20)]

    first = split_rows(rows, validation_ratio=0.2, seed=3407)
    second = split_rows(rows, validation_ratio=0.2, seed=3407)

    assert first == second
    assert first[2] == "seeded_row_fallback"


def test_leakage_gate_rejects_shared_output() -> None:
    audit = audit_split(
        [row(1, output="shared")],
        [row(2, output="SHARED")],
        split_strategy="test",
    )

    with pytest.raises(ValueError, match="leakage"):
        assert_no_content_leakage(audit)


def test_jsonl_error_reports_line_number(tmp_path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(row(1)) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 2"):
        read_jsonl(path)

