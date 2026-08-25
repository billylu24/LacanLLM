"""Configuration, hashing, and artifact contracts for training experiments."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lacanllm.data.io import sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "training" / "qlora_v1.json"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class TrialParams:
    learning_rate: float
    rank: int
    alpha_ratio: int
    dropout: float
    effective_batch_size: int
    warmup_ratio: float
    scheduler: str
    weight_decay: float

    @property
    def alpha(self) -> int:
        return self.rank * self.alpha_ratio

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "alpha": self.alpha}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrialParams:
        return cls(
            learning_rate=float(value["learning_rate"]),
            rank=int(value["rank"]),
            alpha_ratio=int(value["alpha_ratio"]),
            dropout=float(value["dropout"]),
            effective_batch_size=int(value["effective_batch_size"]),
            warmup_ratio=float(value["warmup_ratio"]),
            scheduler=str(value["scheduler"]),
            weight_decay=float(value["weight_decay"]),
        )


@dataclass(frozen=True)
class ExperimentConfig:
    source: Path
    raw: dict[str, Any]
    config_hash: str

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> ExperimentConfig:
        resolved = path.resolve()
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        required = {"experiment_version", "seed", "model_id", "judge_model_id", "paths", "search"}
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"Training config is missing keys: {missing}")
        config = cls(source=resolved, raw=raw, config_hash=canonical_hash(raw))
        config.validate()
        return config

    def validate(self) -> None:
        search = self.raw["search"]
        anchors = search["anchors"]
        if int(search["trials"]) < len(anchors):
            raise ValueError("search.trials cannot be smaller than the anchor count")
        fixed = self.raw["fixed_training"]
        if int(fixed["per_device_train_batch_size"]) != 1:
            raise ValueError("The experiment contract fixes per-device train batch size at one")
        for anchor in anchors:
            params = TrialParams.from_dict(anchor)
            if params.effective_batch_size < 1:
                raise ValueError("effective_batch_size must be positive")
            self.assert_in_space(params)

    def assert_in_space(self, params: TrialParams) -> None:
        space = self.raw["search"]["space"]
        low, high = (float(value) for value in space["learning_rate"])
        if not low <= params.learning_rate <= high:
            raise ValueError(f"learning_rate is outside [{low}, {high}]")
        categorical = {
            "rank": params.rank,
            "alpha_ratio": params.alpha_ratio,
            "dropout": params.dropout,
            "effective_batch_size": params.effective_batch_size,
            "warmup_ratio": params.warmup_ratio,
            "scheduler": params.scheduler,
            "weight_decay": params.weight_decay,
        }
        for key, value in categorical.items():
            if value not in space[key]:
                raise ValueError(f"{key}={value!r} is not in the configured search space")

    def path(self, name: str) -> Path:
        return (PROJECT_ROOT / self.raw["paths"][name]).resolve()

    @property
    def artifact_root(self) -> Path:
        return self.path("artifact_root")

    def trial_dir(self, number: int) -> Path:
        return self.artifact_root / "trials" / f"trial-{number:03d}"

    def data_hashes(self, *, include_test: bool = False) -> dict[str, str]:
        names = ["train", "validation", "challenge"]
        if include_test:
            names.append("test")
        return {name: sha256_file(self.path(name)) for name in names}

    def run_contract(self, params: TrialParams) -> dict[str, Any]:
        return {
            "experiment_version": self.raw["experiment_version"],
            "config_hash": self.config_hash,
            "model_id": self.raw["model_id"],
            "data_hashes": self.data_hashes(),
            "fixed_training": self.raw["fixed_training"],
            "quantization": self.raw["quantization"],
            "params": params.to_dict(),
            "seed": self.raw["seed"],
        }

    def run_hash(self, params: TrialParams) -> str:
        return canonical_hash(self.run_contract(params))


def locked_winner(config: ExperimentConfig) -> dict[str, Any]:
    path = config.artifact_root / "winner.json"
    if not path.is_file():
        raise RuntimeError("Test is sealed until winner.json exists")
    winner = json.loads(path.read_text(encoding="utf-8"))
    if winner.get("locked") is not True or winner.get("config_hash") != config.config_hash:
        raise RuntimeError("Test is sealed until the winner is locked for the active config hash")
    return winner


def verify_test_seal(config: ExperimentConfig) -> dict[str, Any]:
    winner = locked_winner(config)
    seal = json.loads(config.path("test_seal").read_text(encoding="utf-8"))
    if seal.get("sealed") is not True:
        raise RuntimeError("The test manifest is not sealed")
    actual = sha256_file(config.path("test"))
    if actual != seal.get("sha256"):
        raise RuntimeError("The sealed Test SHA-256 does not match")
    return {"winner": winner, "seal": seal, "actual_sha256": actual}
