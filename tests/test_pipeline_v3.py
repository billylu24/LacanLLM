from __future__ import annotations

import json
from pathlib import Path

import pytest

from lacanllm.pipeline_v3 import stages
from lacanllm.pipeline_v3.backend import extract_json
from lacanllm.pipeline_v3.config import PipelineConfig, load_config, require_hash
from lacanllm.pipeline_v3.io import file_hash, object_hash, read_jsonl, write_jsonl
from lacanllm.pipeline_v3.records import hard_filter, judgment_passes, validate_generation
from lacanllm.pipeline_v3.stages import SOURCE_ROWS, SOURCE_SHA256, audit, clean, deduplicate, queue, smoke, split

REPO = Path(__file__).parents[1]
SMOKE_CONFIG = REPO / "configs/pipeline_v3/smoke_gemma4_12b.json"


@pytest.fixture(scope="session")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> PipelineConfig:
    root = tmp_path_factory.mktemp("pipeline") / "data" / "pipeline_v3" / "smoke"
    raw = json.loads(SMOKE_CONFIG.read_text(encoding="utf-8"))
    raw["source_path"] = str(REPO / raw["source_path"])
    raw["work_root"] = str(root)
    path = root.parent / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(path)
    clean(config)
    split(config)
    queue(config)
    return config


def test_source_snapshot_and_cleaning_replay(prepared: PipelineConfig) -> None:
    source = Path(prepared.raw["source_path"])
    assert file_hash(source) == SOURCE_SHA256
    assert sum(1 for _ in read_jsonl(source)) == SOURCE_ROWS
    clean_report = json.loads(prepared.artifact("reports/clean.json").read_text())
    assert clean_report["rows"] == 32_028
    assert clean_report["unicode_nfkc_rows"] == 15


def test_fixed_split_is_exact_and_source_disjoint(prepared: PipelineConfig) -> None:
    report = json.loads(prepared.artifact("reports/split.json").read_text())
    assert report["rows"] == {"Test": 3203, "Train": 25622, "Validation": 3203}
    assert report["sources"]["Test"] == [
        "lacan_text_002.txt",
        "lacan_text_005.txt",
        "lacan_text_011.txt",
        "lacan_text_014.txt",
        "lacan_text_022.txt",
    ]
    assert report["sources"]["Validation"] == [
        "lacan_text_001.txt",
        "lacan_text_012.txt",
        "lacan_text_020.txt",
        "lacan_text_038.txt",
        "lacan_text_040.txt",
    ]
    sets = [set(report["sources"][name]) for name in ("Train", "Validation", "Test")]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])


def test_context_queue_constraints(prepared: PipelineConfig) -> None:
    rows = list(read_jsonl(prepared.artifact("03_queue/candidates.jsonl")))
    assert len(rows) == 6
    assert sum(row["context_kind"] == "single" for row in rows) == 4
    assert sum(row["context_kind"] == "pair" for row in rows) == 2
    assert {row["split"] for row in rows} == {"Train"}
    for row in rows:
        contexts = row["contexts"]
        assert sum(len(item["text"]) for item in contexts) <= 3000
        if len(contexts) == 2:
            assert contexts[0]["source_file"] == contexts[1]["source_file"]
            assert 0 < abs(contexts[0]["paragraph_index"] - contexts[1]["paragraph_index"]) <= 10


def _valid_record(question_type: str = "definition") -> dict[str, object]:
    text = "The subject encounters a limit in speech, and the passage describes that limit explicitly."
    candidate = {
        "candidate_id": "candidate",
        "contexts": [{"context_id": "ctx", "text": text}],
    }
    generated = validate_generation(
        {
            "question": "What limit does the passage describe?",
            "reference_answer": (
                "The passage describes a limit encountered in speech. "
                "It explicitly attributes that limit to the subject's encounter."
            ),
            "evidence": [{"context_id": "ctx", "quote": "The subject encounters a limit in speech"}],
            "question_type": question_type,
        },
        candidate,
    )
    return {**candidate, **generated}


def test_exact_evidence_offsets_and_pair_coverage() -> None:
    record = _valid_record()
    assert record["evidence"][0]["start"] == 0  # type: ignore[index]
    assert hard_filter(record) == []
    paired = dict(record)
    paired["contexts"] = [*record["contexts"], {"context_id": "ctx2", "text": "A nearby paragraph."}]  # type: ignore[index]
    assert hard_filter(paired) == ["paired_context_missing_evidence"]
    with pytest.raises(ValueError, match="continuous exact source span"):
        validate_generation(
            {
                "question": "What limit does the passage describe?",
                "reference_answer": "It describes a limit in speech. The claim remains source bound.",
                "evidence": [{"context_id": "ctx", "quote": "not present"}],
            },
            {"contexts": record["contexts"]},
        )


def test_question_type_is_metadata_only() -> None:
    first = _valid_record("definition")
    second = _valid_record("ambiguous")
    assert hard_filter(first) == hard_filter(second) == []
    judgment = {
        "question_answerability": "answerable",
        "response_appropriate": True,
        "faithful": True,
        "evidence_supports_response": True,
        "self_contained": True,
        "overclaim": False,
        "contradiction": False,
        "answerability_score": 4,
        "faithfulness_score": 4,
        "evidence_score": 4,
        "self_containment_score": 4,
    }
    assert judgment_passes(judgment)
    first["question_type"] = "anything"
    assert hard_filter(first) == []


def test_json_extraction_and_config_hash_isolation(tmp_path: Path) -> None:
    assert extract_json('prefix\n{"ok": true}\nsuffix') == {"ok": True}
    with pytest.raises(ValueError, match="no parseable JSON"):
        extract_json("not json")
    path = tmp_path / "mixed.jsonl"
    write_jsonl(path, [{"config_hash": "old"}, {"config_hash": "new"}])
    with pytest.raises(ValueError, match="mixed or stale"):
        require_hash(list(read_jsonl(path)), "new", path)


def test_production_self_judge_is_rejected() -> None:
    raw = json.loads(SMOKE_CONFIG.read_text())
    raw["profile"] = "production"
    config = PipelineConfig(Path("config.json"), raw, object_hash(raw))
    with pytest.raises(ValueError, match="different generator and judge"):
        config.validate(production=True)


def test_global_dedup_uses_test_validation_train_precedence(tmp_path: Path) -> None:
    raw = json.loads(SMOKE_CONFIG.read_text())
    raw["source_path"] = str(REPO / raw["source_path"])
    raw["work_root"] = str(tmp_path / "data/pipeline_v3/dedup")
    config = PipelineConfig(Path("config.json"), raw, object_hash(raw))
    common = {
        **config.stamp(),
        "question": "What does the source say about speech?",
        "reference_answer": "The source describes a limit in speech. It presents that limit directly.",
        "contexts": [{"context_id": "ctx", "text": "A stable shared context."}],
        "evidence": [{"context_id": "ctx", "quote": "A stable", "start": 0, "end": 8}],
        "hard_filter_pass": True,
    }
    rows = [
        {**common, "candidate_id": "train", "split": "Train"},
        {**common, "candidate_id": "test", "split": "Test"},
        {**common, "candidate_id": "validation", "split": "Validation"},
    ]
    write_jsonl(config.artifact("05_hard_filter/records.jsonl"), rows)
    report = deduplicate(config)
    assert report["passing"] == 1
    output = list(read_jsonl(config.artifact("06_deduplicate/records.jsonl")))
    assert output[0]["candidate_id"] == "test"
    assert output[0]["dedup_pass"] is True
    assert {row["duplicate_of"] for row in output[1:]} == {"test"}


def test_one_structured_repair_retry(monkeypatch: pytest.MonkeyPatch, prepared: PipelineConfig, tmp_path: Path) -> None:
    raw = dict(prepared.raw)
    raw["work_root"] = str(tmp_path / "data/pipeline_v3/repair")
    config = PipelineConfig(Path("config.json"), raw, object_hash(raw))
    candidates = list(read_jsonl(prepared.artifact("03_queue/candidates.jsonl")))[:1]
    candidates[0] = {**candidates[0], **config.stamp()}
    write_jsonl(config.artifact("03_queue/candidates.jsonl"), candidates)

    class RepairBackend:
        calls = 0

        def call(self, prompt: str, candidate: dict[str, object], repair: bool = False):  # noqa: ANN001
            del prompt
            self.calls += 1
            if not repair:
                return {"bad": True}, {}
            context = candidate["contexts"][0]  # type: ignore[index]
            return (
                {
                    "question": "What claim does this passage make?",
                    "reference_answer": (
                        "The passage states the selected claim directly. The cited text supplies its basis."
                    ),
                    "evidence": [{"context_id": context["context_id"], "quote": context["text"][:40]}],
                },
                {},
            )

        def close(self) -> None:
            return

    backend = RepairBackend()
    monkeypatch.setattr(stages, "make_backend", lambda *args, **kwargs: backend)
    report = stages.generate(config, backend="fake")
    assert report["appended_rows"] == 1
    assert report["repair_retries"] == 1
    assert backend.calls == 2


def test_complete_fake_smoke_and_idempotent_resume(prepared: PipelineConfig) -> None:
    report = smoke(prepared, backend="fake", remote_preflight=False)
    assert report["hard_gate_pass"] is True
    assert report["generated_rows"] == report["judged_rows"] == 6
    assert report["second_run_generation_appends"] == 0
    assert report["second_run_judgment_appends"] == 0
    assert report["stage_hashes_unchanged"] is True
    rerun = smoke(prepared, backend="fake", remote_preflight=False)
    assert rerun["second_run_generation_appends"] == 0
    assert rerun["second_run_judgment_appends"] == 0


def test_audit_detects_test_seal_tampering(prepared: PipelineConfig) -> None:
    test_path = prepared.artifact("08_select/test.jsonl")
    test_path.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches"):
        audit(prepared)
