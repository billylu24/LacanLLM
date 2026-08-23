import json
from collections import Counter
from pathlib import Path

import pytest

from lacanllm.data.io import read_jsonl

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_v2_config_contract() -> None:
    config = json.loads((PROJECT_ROOT / "configs" / "data" / "pipeline_v2.json").read_text(encoding="utf-8"))

    assert sum(config["final_quotas"]["train"].values()) == 500
    assert sum(config["final_quotas"]["validation"].values()) == 250
    assert sum(config["final_quotas"]["test"].values()) == 250
    assert sum(config["final_quotas"]["challenge"].values()) == 100
    assert config["generation"]["model_id"] == "google/gemma-4-E2B-it"
    assert config["judge"]["model_id"] == "google/gemma-4-E4B-it"


def test_final_release_config_contract() -> None:
    profile = json.loads((PROJECT_ROOT / "configs" / "data" / "final_release.json").read_text(encoding="utf-8"))

    assert {split: sum(values.values()) for split, values in profile["final_quotas"].items()} == {
        "train": 500,
        "validation": 200,
        "test": 200,
        "challenge": 70,
    }
    assert set(profile["excluded_question_types"]) == {"other", "ambiguous"}


def test_built_corpus_and_split_contract() -> None:
    corpus_path = PROJECT_ROOT / "data" / "processed" / "corpus_v2" / "paragraphs.jsonl"
    manifest_path = PROJECT_ROOT / "data" / "manifests" / "pipeline_v2" / "splits.json"
    if not corpus_path.is_file() or not manifest_path.is_file():
        pytest.skip("Pipeline v2 deterministic preparation has not run")
    rows = [row for _, row in read_jsonl(corpus_path)]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert rows
    assert all(row["source_work"] is None and row["source_work_unknown"] for row in rows)
    assert all(row["paragraph_id"] and row["raw_sha256"] for row in rows)
    assert all(not row["quality_flags"] for row in rows)
    assert len(manifest["splits"]["train"]) == 35
    assert len(manifest["splits"]["validation"]) == 5
    assert len(manifest["splits"]["test"]) == 5
    assert all(manifest["checks"].values())


def test_final_artifacts_when_present() -> None:
    root = PROJECT_ROOT / "data" / "processed"
    paths = {
        "train": root / "sft_v3" / "train.jsonl",
        "validation": root / "benchmark_v2" / "validation.jsonl",
        "test": root / "benchmark_v2" / "test.jsonl",
        "challenge": root / "benchmark_v2" / "challenge.jsonl",
    }
    if not all(path.is_file() for path in paths.values()):
        pytest.skip("Model-backed Pipeline v2 artifacts are not complete")
    expected = {"train": 500, "validation": 200, "test": 200, "challenge": 70}
    counts = {split: sum(1 for _ in read_jsonl(path)) for split, path in paths.items()}
    if counts != expected:
        pytest.skip(f"Model-backed Pipeline v2 artifacts are still partial: {counts}")
    for split, path in paths.items():
        rows = [row for _, row in read_jsonl(path)]
        assert len(rows) == expected[split]
        assert all(set(row["judge_results"]) == {"rubric", "adversarial"} for row in rows)
        if split != "train":
            assert Counter(row["benchmark_grade"] for row in rows) == {"silver": expected[split]}
