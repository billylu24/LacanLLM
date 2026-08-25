"""Read-only experiment validation before any GPU training starts."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from lacanllm.training.config import ExperimentConfig, atomic_json
from lacanllm.training.data import multimodal_messages, read_rows, stratified_sample, validate_messages
from lacanllm.training.runtime import environment_snapshot

EXPECTED_COUNTS = {"train": 500, "validation": 200, "challenge": 70}


def preflight(config: ExperimentConfig, *, tokenize: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "config_hash": config.config_hash,
        "experiment_version": config.raw["experiment_version"],
        "data_hashes": config.data_hashes(),
        "environment": environment_snapshot(),
        "datasets": {},
    }
    loaded = {}
    for name, expected in EXPECTED_COUNTS.items():
        rows = read_rows(config.path(name))
        validate_messages(rows)
        if len(rows) != expected:
            raise RuntimeError(f"{name} has {len(rows)} rows; expected {expected}")
        loaded[name] = rows
        result["datasets"][name] = {
            "rows": len(rows),
            "types": dict(sorted(Counter(str(row["question_type"]) for row in rows).items())),
        }
    search = config.raw["search"]
    validation_screen = stratified_sample(
        loaded["validation"],
        int(search["screen_validation_per_type"]),
        seed=int(config.raw["seed"]),
    )
    challenge_screen = stratified_sample(
        loaded["challenge"],
        int(search["screen_challenge_per_type"]),
        seed=int(config.raw["seed"]),
    )
    result["screen"] = {
        "validation_ids": [row["id"] for row in validation_screen],
        "challenge_ids": [row["id"] for row in challenge_screen],
    }
    seal = json.loads(config.path("test_seal").read_text(encoding="utf-8"))
    if seal.get("sealed") is not True or int(seal.get("rows", 0)) != 200:
        raise RuntimeError("The Test manifest is not a sealed 200-row contract")
    result["test_seal"] = {
        "sealed": True,
        "rows": seal["rows"],
        "sha256": seal["sha256"],
        "note": "The Test artifact itself was not opened during preflight.",
    }
    if tokenize:
        result["token_lengths"] = token_lengths(config, loaded)
    atomic_json(config.artifact_root / "preflight.json", result)
    return result


def token_lengths(config: ExperimentConfig, datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(str(config.raw["model_id"]), local_files_only=True)
    tokenizer = processor.tokenizer
    maximum = int(config.raw["fixed_training"]["max_seq_length"])
    result = {}
    for name, rows in datasets.items():
        lengths = []
        for row in rows:
            text = processor.apply_chat_template(
                multimodal_messages(row["messages"]),
                tokenize=False,
                add_generation_prompt=False,
                enable_thinking=False,
            )
            lengths.append(len(tokenizer(text, add_special_tokens=False)["input_ids"]))
        ordered = sorted(lengths)
        result[name] = {
            "min": ordered[0],
            "median": ordered[len(ordered) // 2],
            "p95": ordered[int((len(ordered) - 1) * 0.95)],
            "max": ordered[-1],
            "over_max_seq_length": sum(length > maximum for length in ordered),
        }
        if result[name]["over_max_seq_length"]:
            raise RuntimeError(f"{name} contains examples longer than max_seq_length={maximum}")
    return result
