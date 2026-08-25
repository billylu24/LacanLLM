"""Optuna search orchestration and sealed evaluation workflow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lacanllm.training.config import ExperimentConfig, TrialParams, atomic_json, canonical_hash, verify_test_seal
from lacanllm.training.data import read_rows, stratified_sample
from lacanllm.training.evaluation import aggregate_pairs, bootstrap_interval

MAX_RECOVERY_ATTEMPTS_PER_PARAMETER_SET = 1
RECOVERY_REVISION = "judge-json-v2"


def _run_worker(config: ExperimentConfig, arguments: list[str], *, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    command = [sys.executable, "-m", "lacanllm.training.worker", *arguments, "--config", str(config.source)]
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=environment, check=False)
    if result.returncode:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-40:])
        raise RuntimeError(f"GPU worker failed with exit code {result.returncode}; log={log_path}\n{tail}")


def _materialize_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    expected = [str(row["id"]) for row in rows]
    if path.is_file() and [str(row["id"]) for row in read_rows(path)] == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def suggest_params(trial: Any, config: ExperimentConfig) -> TrialParams:
    space = config.raw["search"]["space"]
    params = TrialParams(
        learning_rate=trial.suggest_float(
            "learning_rate",
            float(space["learning_rate"][0]),
            float(space["learning_rate"][1]),
            log=True,
        ),
        rank=trial.suggest_categorical("rank", space["rank"]),
        alpha_ratio=trial.suggest_categorical("alpha_ratio", space["alpha_ratio"]),
        dropout=trial.suggest_categorical("dropout", space["dropout"]),
        effective_batch_size=trial.suggest_categorical("effective_batch_size", space["effective_batch_size"]),
        warmup_ratio=trial.suggest_categorical("warmup_ratio", space["warmup_ratio"]),
        scheduler=trial.suggest_categorical("scheduler", space["scheduler"]),
        weight_decay=trial.suggest_categorical("weight_decay", space["weight_decay"]),
    )
    config.assert_in_space(params)
    return params


def screen_rows(config: ExperimentConfig) -> list[dict[str, Any]]:
    search = config.raw["search"]
    validation = stratified_sample(
        read_rows(config.path("validation")),
        int(search["screen_validation_per_type"]),
        seed=int(config.raw["seed"]),
    )
    challenge = stratified_sample(
        read_rows(config.path("challenge")),
        int(search["screen_challenge_per_type"]),
        seed=int(config.raw["seed"]),
    )
    return validation + challenge


def predictions_by_id(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {str(row["id"]): str(row["prediction"]) for row in rows}


def ensure_predictions(
    config: ExperimentConfig,
    rows: list[dict[str, Any]],
    *,
    output_path: Path,
    adapter_dir: Path | None,
) -> dict[str, str]:
    expected = {str(row["id"]) for row in rows}
    existing = predictions_by_id(read_rows(output_path)) if output_path.is_file() else {}
    missing = [row for row in rows if str(row["id"]) not in existing]
    if missing:
        rows_file = output_path.with_suffix(".rows.jsonl")
        _materialize_rows(rows_file, rows)
        arguments = ["predict", "--rows-file", str(rows_file), "--output", str(output_path)]
        if adapter_dir is not None:
            arguments.extend(("--adapter-dir", str(adapter_dir)))
        _run_worker(config, arguments, log_path=output_path.with_suffix(".worker.log"))
        existing = predictions_by_id(read_rows(output_path))
    if set(existing) != expected:
        extras = sorted(set(existing) - expected)
        if extras:
            raise RuntimeError(f"Prediction file contains rows outside the active evaluation set: {extras[:3]}")
    return {row_id: existing[row_id] for row_id in expected}


def evaluate_adapter(
    config: ExperimentConfig,
    rows: list[dict[str, Any]],
    *,
    name: str,
    adapter_dir: Path,
) -> dict[str, Any]:
    evaluation_dir = config.artifact_root / "evaluations" / name
    rows_file = evaluation_dir / "rows.jsonl"
    _materialize_rows(rows_file, rows)
    row_set_hash = canonical_hash(sorted(str(row["id"]) for row in rows))[:16]
    ensure_predictions(
        config,
        rows,
        output_path=config.artifact_root / "evaluations" / "base" / f"{row_set_hash}.jsonl",
        adapter_dir=None,
    )
    candidate = ensure_predictions(
        config,
        rows,
        output_path=evaluation_dir / "candidate_predictions.jsonl",
        adapter_dir=adapter_dir,
    )
    success_rate = sum(bool(value.strip()) for value in candidate.values()) / len(rows)
    judgment_path = evaluation_dir / "paired_judgments.jsonl"
    _run_worker(
        config,
        [
            "judge",
            "--rows-file",
            str(rows_file),
            "--candidate",
            str(evaluation_dir / "candidate_predictions.jsonl"),
            "--baseline",
            str(config.artifact_root / "evaluations" / "base" / f"{row_set_hash}.jsonl"),
            "--output",
            str(judgment_path),
        ],
        log_path=evaluation_dir / "judge.worker.log",
    )
    records = read_rows(judgment_path)
    summary = aggregate_pairs(records)
    low, high = bootstrap_interval(
        records,
        samples=int(config.raw["evaluation"]["bootstrap_samples"]),
        seed=int(config.raw["seed"]),
    )
    summary.update(
        {
            "name": name,
            "adapter_dir": str(adapter_dir),
            "generation_success_rate": success_rate,
            "paired_score_ci95": [low, high],
        }
    )
    atomic_json(evaluation_dir / "summary.json", summary)
    return summary


def objective_factory(config: ExperimentConfig):
    rows = screen_rows(config)

    def objective(trial: Any) -> float:
        params = suggest_params(trial, config)
        recovered = _completed_training(config, params)
        training_number = trial.number
        evaluation_number = trial.number
        if recovered is None:
            _run_worker(
                config,
                ["train", "--trial", str(trial.number), "--params-json", json.dumps(params.to_dict())],
                log_path=config.trial_dir(trial.number) / "train.worker.log",
            )
        else:
            training_number, _ = recovered
            if training_number != trial.number:
                trial.set_user_attr("recovered_from_trial", training_number)
            recovery_source = trial.user_attrs.get("recovery_source_trial")
            if recovery_source is not None:
                evaluation_number = int(recovery_source)
                trial.set_user_attr("evaluation_artifact_trial", evaluation_number)
        metadata = json.loads(
            (config.trial_dir(training_number) / "training_metadata.json").read_text(encoding="utf-8")
        )
        summary = evaluate_adapter(
            config,
            rows,
            name=f"screen-trial-{evaluation_number:03d}",
            adapter_dir=Path(metadata["adapter_dir"]),
        )
        for key, value in summary.items():
            if isinstance(value, int | float | str | bool) or value is None:
                trial.set_user_attr(key, value)
        search = config.raw["search"]
        if float(summary["consensus_rate"]) < float(config.raw["evaluation"]["judge_min_consensus_rate"]):
            trial.set_user_attr("consensus_gate_failed", True)
            return 0.0
        if float(summary["generation_success_rate"]) < float(search["generation_success_floor"]):
            return 0.0
        candidate_accuracy = summary.get("challenge_candidate_accuracy")
        baseline_accuracy = summary.get("challenge_baseline_accuracy")
        if candidate_accuracy is not None and baseline_accuracy is not None:
            tolerance = float(search["challenge_regression_tolerance"])
            if float(candidate_accuracy) < float(baseline_accuracy) - tolerance:
                trial.set_user_attr("challenge_gate_failed", True)
                return 0.0
        return float(summary["paired_score"])

    return objective


def _completed_training(config: ExperimentConfig, params: TrialParams) -> tuple[int, dict[str, Any]] | None:
    """Return a contract-matching adapter left by an earlier interrupted objective."""
    expected_hash = config.run_hash(params)
    trials_dir = config.artifact_root / "trials"
    for metadata_path in sorted(trials_dir.glob("trial-*/training_metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            number = int(metadata_path.parent.name.removeprefix("trial-"))
        except (json.JSONDecodeError, ValueError):
            continue
        adapter = metadata_path.parent / "adapter" / "adapter_model.safetensors"
        if metadata.get("status") == "completed" and metadata.get("run_hash") == expected_hash and adapter.is_file():
            return number, metadata
    return None


def _reenqueue_recoverable_failures(study: Any, config: ExperimentConfig) -> None:
    """Retry a trained parameter set once after an interrupted objective."""
    import optuna

    active_fingerprints = {
        canonical_hash(trial.params)
        for trial in study.trials
        if trial.params and trial.state != optuna.trial.TrialState.FAIL
    }
    for trial in study.trials:
        if trial.state != optuna.trial.TrialState.FAIL or not trial.params:
            continue
        fingerprint = canonical_hash(trial.params)
        if fingerprint in active_fingerprints:
            continue
        recovery_attempts = sum(
            canonical_hash(candidate.params) == fingerprint
            and candidate.user_attrs.get("recovery_revision") == RECOVERY_REVISION
            for candidate in study.trials
            if candidate.params
        )
        if recovery_attempts >= MAX_RECOVERY_ATTEMPTS_PER_PARAMETER_SET:
            active_fingerprints.add(fingerprint)
            continue
        try:
            params = TrialParams.from_dict(trial.params)
            config.assert_in_space(params)
        except (KeyError, TypeError, ValueError):
            continue
        recovered = _completed_training(config, params)
        if recovered is None:
            continue
        study.enqueue_trial(
            trial.params,
            user_attrs={
                "recovery_revision": RECOVERY_REVISION,
                "recovery_source_trial": trial.number,
            },
        )
        active_fingerprints.add(fingerprint)


def run_search(config: ExperimentConfig, *, trials: int | None = None) -> dict[str, Any]:
    import optuna

    config.artifact_root.mkdir(parents=True, exist_ok=True)
    study = optuna.create_study(
        study_name=str(config.raw["experiment_version"]),
        storage=f"sqlite:///{config.artifact_root / 'study.sqlite3'}",
        direction="maximize",
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=int(config.raw["seed"])),
        pruner=optuna.pruners.HyperbandPruner(
            min_resource=1,
            max_resource=int(config.raw["fixed_training"]["max_epochs"]),
            reduction_factor=2,
        ),
    )
    if not study.trials:
        for anchor in config.raw["search"]["anchors"]:
            study.enqueue_trial(anchor)
    _reenqueue_recoverable_failures(study, config)
    target = int(trials or config.raw["search"]["trials"])
    # Enqueued anchors are WAITING trials and must not count as completed work.
    # optimize(n_trials=N) consumes WAITING anchors first, then asks TPE for the
    # remaining trials, so subtract only terminal trials on resume.
    terminal_states = {
        optuna.trial.TrialState.COMPLETE,
        optuna.trial.TrialState.PRUNED,
    }
    finished = sum(trial.state in terminal_states for trial in study.trials)
    remaining = max(0, target - finished)
    if remaining:
        study.optimize(objective_factory(config), n_trials=remaining, gc_after_trial=True)
    completed = [trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE]
    summary = {
        "study_name": study.study_name,
        "requested_trials": target,
        "total_trials": len(study.trials),
        "completed_trials": len(completed),
        "pruned_trials": sum(trial.state == optuna.trial.TrialState.PRUNED for trial in study.trials),
        "failed_trials": sum(trial.state == optuna.trial.TrialState.FAIL for trial in study.trials),
        "best_trial": study.best_trial.number if completed else None,
        "best_value": study.best_value if completed else None,
        "best_params": study.best_params if completed else None,
    }
    atomic_json(config.artifact_root / "search_summary.json", summary)
    return summary


def evaluate_top(config: ExperimentConfig, *, count: int | None = None) -> dict[str, Any]:
    import optuna

    study = optuna.load_study(
        study_name=str(config.raw["experiment_version"]),
        storage=f"sqlite:///{config.artifact_root / 'study.sqlite3'}",
    )
    completed = sorted(
        (trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE),
        key=lambda trial: float(trial.value or 0.0),
        reverse=True,
    )
    top_count = int(count or config.raw["search"]["top_full_evaluation"])
    rows = read_rows(config.path("validation")) + read_rows(config.path("challenge"))
    results = []
    for trial in completed[:top_count]:
        metadata = json.loads((config.trial_dir(trial.number) / "training_metadata.json").read_text(encoding="utf-8"))
        results.append(
            {
                "trial": trial.number,
                "params": trial.params,
                "screen_value": trial.value,
                "full": evaluate_adapter(
                    config,
                    rows,
                    name=f"full-trial-{trial.number:03d}",
                    adapter_dir=Path(metadata["adapter_dir"]),
                ),
            }
        )
    eligible = [
        result
        for result in results
        if result["full"]["generation_success_rate"] >= config.raw["search"]["generation_success_floor"]
        and result["full"]["consensus_rate"] >= config.raw["evaluation"]["judge_min_consensus_rate"]
        and not _challenge_failed(result["full"], config)
    ]
    if not eligible:
        raise RuntimeError("No top trial passed the full generation and Challenge gates")
    winner = max(
        eligible,
        key=lambda result: (
            result["full"]["paired_score"],
            -result["full"]["candidate_contradiction_rate"],
            -result["full"]["candidate_overclaim_rate"],
            -int(result["params"]["rank"]),
        ),
    )
    locked = {
        "locked": True,
        "config_hash": config.config_hash,
        "trial": winner["trial"],
        "adapter_dir": str(config.trial_dir(winner["trial"]) / "adapter"),
        "selection": winner,
    }
    atomic_json(config.artifact_root / "top_evaluation.json", {"results": results, "winner": locked})
    atomic_json(config.artifact_root / "winner.json", locked)
    return {"results": results, "winner": locked}


def _challenge_failed(summary: dict[str, Any], config: ExperimentConfig) -> bool:
    candidate = summary.get("challenge_candidate_accuracy")
    baseline = summary.get("challenge_baseline_accuracy")
    if candidate is None or baseline is None:
        return False
    return float(candidate) < float(baseline) - float(config.raw["search"]["challenge_regression_tolerance"])


def evaluate_test(config: ExperimentConfig) -> dict[str, Any]:
    verified = verify_test_seal(config)
    completion = config.artifact_root / "test_evaluation_complete.json"
    if completion.exists():
        raise RuntimeError("Sealed Test evaluation has already been completed for this experiment")
    winner = verified["winner"]
    summary = evaluate_adapter(
        config,
        read_rows(config.path("test")),
        name="sealed-test",
        adapter_dir=Path(winner["adapter_dir"]),
    )
    result = {
        "config_hash": config.config_hash,
        "winner_trial": winner["trial"],
        "test_sha256": verified["actual_sha256"],
        "summary": summary,
    }
    atomic_json(completion, result)
    return result
