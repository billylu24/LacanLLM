import json
from pathlib import Path

from lacanllm.data.io import read_jsonl, write_jsonl
from lacanllm.data.pipeline_v2 import _generation_pending, _judge_pending
from lacanllm.data.pipeline_v2_core import (
    PipelineConfig,
    _candidate_row,
    _select_with_quotas,
    build_generation_queue,
    consensus_rows,
    deduplicate_rows,
    hard_filter_row,
    normalize_source_text,
    normalized_evidence_span,
    paragraph_rejection_reasons,
    type_suitability_score,
)
from lacanllm.data.pipeline_v2_models import generation_prompt, judge_prompt, run_generation, run_judge

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def temp_config(tmp_path: Path) -> PipelineConfig:
    raw = json.loads((PROJECT_ROOT / "configs" / "data" / "pipeline_v2.json").read_text(encoding="utf-8"))
    raw["paths"] = {
        "raw_corpus": str(tmp_path / "raw.jsonl"),
        "clean_corpus": str(tmp_path / "corpus.jsonl"),
        "clean_rejections": str(tmp_path / "clean_rejections.jsonl"),
        "split_manifest": str(tmp_path / "splits.json"),
        "interim_root": str(tmp_path / "interim"),
        "train_output": str(tmp_path / "train.jsonl"),
        "benchmark_root": str(tmp_path / "benchmark"),
        "audit_file": str(tmp_path / "audit.json"),
        "test_manifest": str(tmp_path / "test_seal.json"),
        "record_file": str(tmp_path / "record.md"),
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return PipelineConfig.load(path)


def source_text() -> str:
    return (
        "For Lacan, desire is articulated through the signifying relation and cannot be reduced to biological need. "
        "It emerges in relation to the demand of the Other, while preserving a remainder that demand cannot absorb. "
        "This distinction explains why desire persists instead of ending when a particular need has been satisfied."
    )


def valid_candidate(config: PipelineConfig) -> dict:
    text = source_text()
    quote = "desire is articulated through the signifying relation and cannot be reduced to biological need"
    return {
        "candidate_id": "candidate-1",
        "split": "train",
        "target_question_type": "definition",
        "challenge_category": None,
        "source_file": "source.txt",
        "source_work": None,
        "source_work_unknown": True,
        "context_ids": ["paragraph-1"],
        "contexts": [
            {
                "paragraph_id": "paragraph-1",
                "source_file": "source.txt",
                "paragraph_index": 1,
                "text": text,
            }
        ],
        "source_text": text,
        "evidence_quality_score": 0.95,
        "question": "How is desire distinguished from biological need in Lacan's account?",
        "answer": (
            "Desire is organized through the signifying relation rather than reducible to biological need. "
            "It arises through demand addressed to the Other, yet retains a remainder that satisfaction of need "
            "cannot eliminate."
        ),
        "evidence_quotes": [quote],
        "generated_question_type": "definition",
        "pipeline_version": config.pipeline_version,
        "config_hash": config.config_hash,
    }


def judge_payload() -> dict:
    return {
        "answerable": True,
        "faithful": True,
        "evidence_supports_answer": True,
        "self_contained": True,
        "overclaim": False,
        "contradiction": False,
        "challenge_valid": True,
        "scores": {
            "answerability": 5,
            "faithfulness": 5,
            "evidence_support": 5,
            "self_contained": 5,
        },
        "canonical_question_type": "definition",
        "unsupported_claims": [],
        "reason_codes": [],
        "reason": "Every answer claim is supported by the context.",
    }


def test_normalization_is_conservative() -> None:
    cleaned, operations = normalize_source_text("\ufeffLacan\tuses  desire.\nNext line.")
    assert cleaned == "Lacan uses desire. Next line."
    assert "bom_removed" in operations
    assert "whitespace_normalized" in operations


def test_paragraph_filter_reports_multiple_reasons(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    reasons = paragraph_rejection_reasons("ISBN 123456789 �", config)
    assert {"length", "publisher_metadata", "mojibake", "low_alphabetic_ratio"} <= set(reasons)


def test_normalized_quote_maps_to_source_offsets() -> None:
    text = "Lacan's account of desire—unlike need—depends on the Other."
    span = normalized_evidence_span("desire unlike need depends on the other", text)
    assert span is not None
    assert text[span[0] : span[1]] == "desire—unlike need—depends on the Other"


def test_type_suitability_routes_clinical_evidence() -> None:
    clinical = "The analyst works with transference during treatment of a patient's symptom and neurosis."
    administrative = "The audience met last night and several people raised their hands after the meeting."
    assert type_suitability_score(clinical, "clinical_application") > type_suitability_score(
        administrative,
        "clinical_application",
    )


def test_candidate_id_changes_with_generation_prompt_version(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    contexts = [
        {
            "paragraph_id": "paragraph-1",
            "source_file": "source.txt",
            "paragraph_index": 1,
            "cleaned_text": source_text(),
        }
    ]
    first = _candidate_row(config, "train", "definition", contexts)
    config.raw["generation"]["prompt_version"] = "pipeline_v2.generate.changed"
    second = _candidate_row(config, "train", "definition", contexts)

    assert first["candidate_id"] != second["candidate_id"]
    assert second["generation_prompt_version"] == "pipeline_v2.generate.changed"


def test_refill_queue_can_keep_partial_allocation(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    config.raw["candidate_source_caps"]["train"] = 1
    write_jsonl(
        config.path("clean_corpus"),
        [
            {
                "paragraph_id": "paragraph-1",
                "source_file": "source.txt",
                "paragraph_index": 1,
                "cleaned_text": source_text(),
            }
        ],
    )
    config.path("split_manifest").write_text(
        json.dumps(
            {
                "config_hash": config.config_hash,
                "splits": {"train": ["source.txt"]},
            }
        ),
        encoding="utf-8",
    )

    result = build_generation_queue(
        config,
        "train",
        requested={"definition": 2},
        allow_partial=True,
    )

    assert result["added_rows"] == 1
    assert result["unallocated_by_type"] == {"definition": 1}


def test_quota_selection_reserves_source_floor() -> None:
    rows = [
        {
            "candidate_id": f"{source}-{index}",
            "source_file": source,
            "question_type": "definition",
            "quality_score": 1.0 - index / 100,
        }
        for source in ("a", "b")
        for index in range(3)
    ]
    selected, deficits = _select_with_quotas(
        rows,
        {"definition": 4},
        3,
        source_floor=2,
        required_sources=("a", "b"),
    )
    assert deficits == {}
    assert {source: sum(row["source_file"] == source for row in selected) for source in ("a", "b")} == {
        "a": 2,
        "b": 2,
    }


def test_hard_filter_accepts_grounded_candidate(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    reasons, normalized = hard_filter_row(valid_candidate(config), config)
    assert reasons == []
    assert normalized["evidence_spans"][0]["paragraph_id"] == "paragraph-1"
    assert normalized["hard_filter_metrics"]["answer_source_overlap"] >= 0.35


def test_hard_filter_selects_best_of_extra_valid_quotes(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    candidate["evidence_quotes"] = [
        "This distinction explains why desire persists instead of ending when a particular need has been satisfied.",
        candidate["evidence_quotes"][0],
    ]
    reasons, normalized = hard_filter_row(candidate, config)
    assert reasons == []
    assert len(normalized["evidence_quotes"]) == 1
    assert normalized["hard_filter_metrics"]["original_quote_count"] == 2
    assert len(normalized["hard_filter_metrics"]["discarded_extra_quotes"]) == 1


def test_negative_challenge_derives_auditable_boundary_quote(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    candidate.update(
        {
            "split": "challenge",
            "target_question_type": "unanswerable",
            "challenge_category": "unanswerable",
            "question": (
                "Which institutional procedure follows from Lacan's distinction between desire and biological need?"
            ),
            "answer": (
                "The available context defines desire in relation to need, demand, and the Other, but it gives no "
                "institutional procedure. The requested procedure therefore cannot be answered without evidence "
                "beyond the supplied context."
            ),
            "evidence_quotes": [],
            "generated_question_type": "unanswerable",
        }
    )

    reasons, normalized = hard_filter_row(candidate, config)

    assert reasons == []
    assert len(normalized["evidence_quotes"]) == 1
    assert len(normalized["evidence_spans"]) == 1
    assert normalized["hard_filter_metrics"]["derived_evidence_quote"] is True
    span = normalized["evidence_spans"][0]
    assert source_text()[span["start"] : span["end"]] == span["source_quote"]


def test_negative_challenge_normalizes_extra_quotes_to_one(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    candidate.update(
        {
            "split": "challenge",
            "target_question_type": "ambiguous",
            "challenge_category": "ambiguous",
            "question": "How should this relation be interpreted?",
            "answer": (
                "The question does not identify which relation is meant, so a unique interpretation is impossible. "
                "It must first specify whether it asks about need, demand, desire, or the Other."
            ),
            "evidence_quotes": [
                "For Lacan, desire is articulated through the signifying relation "
                "and cannot be reduced to biological need",
                "It emerges in relation to the demand of the Other, while preserving "
                "a remainder that demand cannot absorb",
            ],
            "generated_question_type": "ambiguous",
        }
    )

    reasons, normalized = hard_filter_row(candidate, config)

    assert reasons == []
    assert len(normalized["evidence_quotes"]) == 1
    assert len(normalized["evidence_spans"]) == 1
    assert normalized["hard_filter_metrics"]["original_quote_count"] == 2
    assert len(normalized["hard_filter_metrics"]["discarded_extra_quotes"]) == 1


def test_negative_challenge_uses_category_contract_instead_of_ordinary_scores(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    candidate.update(
        {
            "split": "challenge",
            "target_question_type": "unanswerable",
            "challenge_category": "unanswerable",
        }
    )
    reasons, candidate = hard_filter_row(candidate, config)
    assert reasons == []
    write_jsonl(config.interim("deduplicated", "challenge"), [candidate])
    payload = judge_payload()
    payload.update(
        {
            "answerable": False,
            "canonical_question_type": "unanswerable",
            "scores": {
                "answerability": 1,
                "faithfulness": 5,
                "evidence_support": 5,
                "self_contained": 5,
            },
        }
    )
    judge_record = {
        "candidate_id": candidate["candidate_id"],
        "judge_status": "parsed",
        "judge_result": payload,
        "config_hash": config.config_hash,
    }
    write_jsonl(config.interim("judge_rubric", "challenge"), [judge_record])
    write_jsonl(config.interim("judge_adversarial", "challenge"), [judge_record])

    accepted, rejected = consensus_rows(config, "challenge")

    assert len(accepted) == 1
    assert rejected == []
    assert accepted[0]["consensus_version"] == "pipeline_v2.consensus.2"


def test_other_type_has_explicit_generation_and_judge_contract(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    candidate.update(
        {
            "target_question_type": "other",
            "generation_prompt_version": "pipeline_v2.generate.other.1",
        }
    )

    generated = generation_prompt(candidate)
    judged = judge_prompt(candidate, "rubric")

    assert "argumentative or methodological structure" in generated
    assert "canonical_question_type is other" in judged


def test_deduplication_retains_cluster_audit(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    _, first = hard_filter_row(valid_candidate(config), config)
    second = {**first, "candidate_id": "candidate-2"}
    kept, rejected = deduplicate_rows([first, second], question_distance=14, answer_distance=8)
    assert len(kept) == 1
    assert len(rejected) == 1
    assert rejected[0]["duplicate_cluster_id"]
    assert rejected[0]["duplicate_representative_id"] == kept[0]["candidate_id"]


def test_generation_pending_detects_completed_queue(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    queue_path = config.interim("queues", "train")
    output_path = config.interim("generations", "train")
    write_jsonl(queue_path, [candidate])

    assert _generation_pending(config, "train") is True

    write_jsonl(output_path, [candidate])
    assert _generation_pending(config, "train") is False


def test_judge_pending_detects_completed_pass(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    candidate = valid_candidate(config)
    write_jsonl(config.interim("deduplicated", "train"), [candidate])

    assert _judge_pending(config, "train", "rubric") is True

    write_jsonl(
        config.interim("judge_rubric", "train"),
        [{"candidate_id": candidate["candidate_id"], "config_hash": config.config_hash}],
    )
    assert _judge_pending(config, "train", "rubric") is False


def test_fake_generation_is_resumable(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    excluded = {"question", "answer", "evidence_quotes", "generated_question_type"}
    task = {key: value for key, value in valid_candidate(config).items() if key not in excluded}
    queue_path = config.interim("queues", "train")
    write_jsonl(queue_path, [task])
    payload = {
        "question": valid_candidate(config)["question"],
        "answer": valid_candidate(config)["answer"],
        "evidence_quotes": valid_candidate(config)["evidence_quotes"],
        "question_type": "definition",
    }
    def fake(prompts, **kwargs):
        return [json.dumps(payload) for _ in prompts]

    first = run_generation(config, "train", backend=fake)
    second = run_generation(config, "train", backend=fake)
    assert first["generated"] == 1
    assert second["generated"] == 0
    assert sum(1 for _ in read_jsonl(config.interim("generations", "train"))) == 1


def test_fake_judge_retries_and_records_strict_result(tmp_path: Path) -> None:
    config = temp_config(tmp_path)
    _, candidate = hard_filter_row(valid_candidate(config), config)
    write_jsonl(config.interim("deduplicated", "train"), [candidate])
    outputs = iter(["not json", json.dumps(judge_payload())])

    def fake(prompts, **kwargs):
        return [next(outputs) for _ in prompts]

    result = run_judge(config, "train", "rubric", backend=fake)
    stored = [row for _, row in read_jsonl(config.interim("judge_rubric", "train"))][0]
    assert result["judged"] == 1
    assert result["parse_failures"] == 0
    assert len(stored["raw_judge_outputs"]) == 2
    assert stored["judge_result"]["faithful"] is True
