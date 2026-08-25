import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

from lacanllm.training.benchmark import _training_shared_kv_cache
from lacanllm.training.cli import parse_args
from lacanllm.training.config import ExperimentConfig, TrialParams, atomic_json, canonical_hash, verify_test_seal
from lacanllm.training.data import mask_before_response, multimodal_messages, stratified_sample
from lacanllm.training.evaluation import (
    aggregate_pairs,
    judge_prompt,
    normalize_score_floor,
    normalize_swapped,
    parse_json_object,
    validate_judgment,
)
from lacanllm.training.search import (
    RECOVERY_REVISION,
    _completed_training,
    _reenqueue_recoverable_failures,
    suggest_params,
)
from lacanllm.training.worker import predict


def test_active_training_config_has_six_valid_anchors():
    config = ExperimentConfig.load()
    assert config.raw["search"]["trials"] == 16
    assert len(config.raw["search"]["anchors"]) == 6
    assert {TrialParams.from_dict(row).rank for row in config.raw["search"]["anchors"]} == {8, 16, 32}


def test_mask_before_response_masks_only_prefix():
    assert mask_before_response([1, 2, 3, 4, 5], [2, 3]) == [-100, -100, -100, 4, 5]
    with pytest.raises(ValueError, match="non-empty assistant"):
        mask_before_response([1, 2, 3], [2, 3])


def test_text_messages_are_adapted_for_gemma4_processor():
    messages = multimodal_messages([{"role": "user", "content": "hello"}])
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_stratified_sample_is_deterministic_and_balanced():
    rows = [
        {"id": f"{kind}-{index}", "question_type": kind}
        for kind in ("definition", "comparison")
        for index in range(5)
    ]
    first = stratified_sample(rows, 3, seed=3407)
    second = stratified_sample(list(reversed(rows)), 3, seed=3407)
    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert {kind: sum(row["question_type"] == kind for row in first) for kind in ("definition", "comparison")} == {
        "definition": 3,
        "comparison": 3,
    }


def _judgment(winner: str, *, challenge_a: bool = True, challenge_b: bool = True):
    return {
        "winner": winner,
        "a_scores": {"correctness": 5, "faithfulness": 5, "coverage": 4},
        "b_scores": {"correctness": 3, "faithfulness": 3, "coverage": 3},
        "a_contradiction": False,
        "b_contradiction": True,
        "a_overclaim": False,
        "b_overclaim": True,
        "challenge_a_valid": challenge_a,
        "challenge_b_valid": challenge_b,
        "reason": "A is better.",
    }


def test_swapped_judgment_normalizes_back_to_candidate_a():
    swapped = normalize_swapped(_judgment("B", challenge_a=False, challenge_b=True))
    assert swapped["winner"] == "A"
    assert swapped["challenge_a_valid"] is True
    assert swapped["a_scores"]["correctness"] == 3


def test_pair_aggregation_uses_only_order_consensus():
    records = [
        {"question_type": "definition", "first": _judgment("A"), "second": _judgment("A")},
        {"question_type": "definition", "first": _judgment("tie"), "second": _judgment("tie")},
        {"question_type": "definition", "first": _judgment("A"), "second": _judgment("B")},
    ]
    summary = aggregate_pairs(records)
    assert summary["consensus_cases"] == 2
    assert summary["paired_score"] == 0.75


def test_judgment_schema_is_strict():
    assert validate_judgment(_judgment("A"))["winner"] == "A"
    invalid = _judgment("A")
    invalid["a_scores"]["correctness"] = 5.0
    with pytest.raises(ValueError, match="integers"):
        validate_judgment(invalid)


def test_judge_zero_score_is_audited_and_mapped_to_scale_floor():
    raw = _judgment("A")
    raw["b_scores"]["correctness"] = 0
    normalized = normalize_score_floor(raw)
    assert normalized["b_scores"]["correctness"] == 1
    assert normalized["_score_floor_normalized"] is True
    validate_judgment(normalized)


def test_truncated_final_judge_reason_is_recovered_with_audit_marker():
    raw = json.dumps(_judgment("A"))
    truncated = raw[: raw.index('"reason"')] + '"reason": "an unfinished and overly long rationale'
    parsed = parse_json_object(truncated)
    assert parsed["winner"] == "A"
    assert parsed["reason"] == "[truncated at generation limit]"
    assert parsed["_reason_truncated"] is True
    validate_judgment(parsed)


def test_complete_judge_json_is_extracted_before_trailing_text():
    parsed = parse_json_object(json.dumps(_judgment("B")) + " trailing text")
    assert parsed["winner"] == "B"
    assert parsed["reason"] == "A is better."
    assert "_reason_truncated" not in parsed


def test_judge_prompt_caps_rationale_length():
    prompt = judge_prompt(
        {"question_type": "definition", "question": "Q", "answer": "A", "contexts": []},
        "candidate",
        "baseline",
    )
    assert "at most 40 words" in prompt


class FakeTrial:
    def suggest_float(self, name, low, high, *, log):
        assert name == "learning_rate" and log is True
        return low

    def suggest_categorical(self, name, choices):
        return choices[0]


def test_suggested_trial_is_inside_configured_space():
    config = ExperimentConfig.load()
    params = suggest_params(FakeTrial(), config)
    config.assert_in_space(params)
    assert params.learning_rate == config.raw["search"]["space"]["learning_rate"][0]


def test_cli_exposes_reproducible_smoke_command():
    args = parse_args(["smoke"])
    assert args.command == "smoke"
    assert args.config.name == "qlora_v1.json"


def test_training_shared_kv_cache_does_not_accumulate_batch_history():
    transformers = pytest.importorskip("transformers")
    torch = pytest.importorskip("torch")
    config = transformers.Gemma4TextConfig(
        num_hidden_layers=2,
        num_kv_shared_layers=1,
        layer_types=["sliding_attention", "sliding_attention"],
    )
    cache = _training_shared_kv_cache(config)
    keys = torch.randn(1, 1, 3, 4)
    values = torch.randn(1, 1, 3, 4)
    returned_keys, returned_values = cache.update(keys, values, 0)
    assert returned_keys is keys and returned_values is values
    assert cache.get_seq_length() == 0


def test_predict_creates_missing_output_and_resumes(tmp_path: Path, monkeypatch):
    rows_file = tmp_path / "rows.jsonl"
    rows_file.write_text(
        '\n'.join(
            json.dumps({"id": value, "question_type": "definition"})
            for value in ("one", "two")
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "predictions.jsonl"
    generated = []

    class FakeGenerator:
        def __init__(self, model_id, adapter_dir):
            pass

        def generate(self, rows, *, max_new_tokens):
            generated.extend(row["id"] for row in rows)
            return [
                {"id": row["id"], "question_type": row["question_type"], "prediction": "ok"}
                for row in rows
            ]

        def close(self):
            pass

    monkeypatch.setattr("lacanllm.training.runtime.LocalGenerator", FakeGenerator)
    config = ExperimentConfig.load()
    args = Namespace(rows_file=rows_file, output=output, adapter_dir="")
    predict(config, args)
    predict(config, args)
    assert generated == ["one", "two"]
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_failed_objective_reuses_completed_training(tmp_path: Path):
    optuna = pytest.importorskip("optuna")
    config = ExperimentConfig.load()
    raw = json.loads(json.dumps(config.raw))
    raw["paths"]["artifact_root"] = str(tmp_path / "artifacts")
    scoped = ExperimentConfig(source=config.source, raw=raw, config_hash=canonical_hash(raw))
    params = TrialParams.from_dict(raw["search"]["anchors"][0])
    trial_dir = scoped.trial_dir(0)
    (trial_dir / "adapter").mkdir(parents=True)
    (trial_dir / "adapter" / "adapter_model.safetensors").touch()
    atomic_json(
        trial_dir / "training_metadata.json",
        {"status": "completed", "run_hash": scoped.run_hash(params)},
    )
    study = optuna.create_study()
    study.enqueue_trial(raw["search"]["anchors"][0])

    def fail_after_suggestion(trial):
        suggest_params(trial, scoped)
        raise RuntimeError("interrupted after training")

    study.optimize(fail_after_suggestion, n_trials=1, catch=(RuntimeError,))
    _reenqueue_recoverable_failures(study, scoped)
    assert [trial.state for trial in study.trials] == [
        optuna.trial.TrialState.FAIL,
        optuna.trial.TrialState.WAITING,
    ]
    assert study.trials[1].user_attrs == {
        "recovery_revision": RECOVERY_REVISION,
        "recovery_source_trial": 0,
    }
    assert _completed_training(scoped, params)[0] == 0

    def fail_recovery(trial):
        suggest_params(trial, scoped)
        raise RuntimeError("evaluation failed again")

    study.optimize(fail_recovery, n_trials=1, catch=(RuntimeError,))
    _reenqueue_recoverable_failures(study, scoped)
    assert [trial.state for trial in study.trials] == [
        optuna.trial.TrialState.FAIL,
        optuna.trial.TrialState.FAIL,
    ]


def test_waiting_anchors_do_not_reduce_search_budget(tmp_path: Path, monkeypatch):
    optuna = pytest.importorskip("optuna")
    config = ExperimentConfig.load()
    raw = json.loads(json.dumps(config.raw))
    raw["paths"]["artifact_root"] = str(tmp_path / "artifacts")
    scoped = ExperimentConfig(source=config.source, raw=raw, config_hash=canonical_hash(raw))
    calls = []

    class StudyProxy:
        def __init__(self, study):
            self._study = study

        def __getattr__(self, name):
            return getattr(self._study, name)

        def optimize(self, objective, *, n_trials, gc_after_trial):
            calls.append(n_trials)

    real_create = optuna.create_study

    def create_proxy(**kwargs):
        return StudyProxy(real_create(**kwargs))

    monkeypatch.setattr(optuna, "create_study", create_proxy)
    from lacanllm.training.search import run_search

    result = run_search(scoped, trials=16)
    assert calls == [16]
    assert result["total_trials"] == 6


def test_test_artifact_remains_sealed_until_winner_is_locked(tmp_path: Path):
    test_file = tmp_path / "test.jsonl"
    test_file.write_text('{"id":"one"}\n', encoding="utf-8")
    seal_file = tmp_path / "seal.json"
    digest = hashlib.sha256(test_file.read_bytes()).hexdigest()
    atomic_json(seal_file, {"sealed": True, "sha256": digest})
    artifact_root = tmp_path / "artifacts"
    raw = ExperimentConfig.load().raw
    raw = json.loads(json.dumps(raw))
    raw["paths"].update(
        {
            "test": str(test_file),
            "test_seal": str(seal_file),
            "artifact_root": str(artifact_root),
        }
    )
    config = ExperimentConfig(source=tmp_path / "config.json", raw=raw, config_hash=canonical_hash(raw))
    with pytest.raises(RuntimeError, match="sealed"):
        verify_test_seal(config)
    atomic_json(artifact_root / "winner.json", {"locked": True, "config_hash": config.config_hash})
    assert verify_test_seal(config)["actual_sha256"] == digest
