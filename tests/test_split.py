from lacanllm.data.split import reserve_holdout_sources, select_with_source_cap


def rows() -> list[dict]:
    return [
        {
            "source_file": f"source-{source}",
            "qa_id": f"{source}-{index}",
            "question_type": "definition" if index % 2 else "explanation",
            "quality_score": 0.9,
            "provenance_score": 1.0,
        }
        for source in range(6)
        for index in range(10)
    ]


def test_holdout_sources_have_no_overlap_and_leave_training_sources() -> None:
    validation, test, training = reserve_holdout_sources(
        rows(), validation_target=15, test_target=15, seed=3407, per_source_cap=10
    )
    assert validation
    assert test
    assert training
    assert validation.isdisjoint(test)
    assert validation.isdisjoint(training)
    assert test.isdisjoint(training)
    assert validation | test | training == {row["source_file"] for row in rows()}


def test_selection_respects_source_cap() -> None:
    selected = select_with_source_cap(rows(), target=18, per_source_cap=4)
    counts: dict[str, int] = {}
    for row in selected:
        counts[row["source_file"]] = counts.get(row["source_file"], 0) + 1
    assert len(selected) == 18
    assert max(counts.values()) <= 4
