from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import object_hash


@dataclass(frozen=True)
class PipelineConfig:
    path: Path
    raw: dict[str, Any]
    config_hash: str

    @property
    def root(self) -> Path:
        return Path(self.raw["work_root"])

    @property
    def profile(self) -> str:
        return str(self.raw["profile"])

    def artifact(self, name: str) -> Path:
        return self.root / name

    def stamp(self) -> dict[str, str]:
        return {
            "schema_version": str(self.raw["schema_version"]),
            "pipeline_version": str(self.raw["pipeline_version"]),
            "config_hash": self.config_hash,
            "generator_model_id": str(self.raw["generator"]["model_id"]),
            "generator_model_revision": str(self.raw["generator"]["revision"]),
            "generator_prompt_version": str(self.raw["generator"]["prompt_version"]),
            "judge_model_id": str(self.raw["judge"]["model_id"]),
            "judge_model_revision": str(self.raw["judge"]["revision"]),
            "judge_prompt_version": str(self.raw["judge"]["prompt_version"]),
        }

    def validate(self, production: bool = False) -> None:
        required = {"schema_version", "pipeline_version", "profile", "source_path", "work_root", "generator", "judge"}
        missing = required - self.raw.keys()
        if missing:
            raise ValueError(f"missing configuration keys: {sorted(missing)}")
        if "pipeline_v3" not in Path(self.raw["work_root"]).parts:
            raise ValueError("work_root must be a versioned pipeline_v3 path")
        for role in ("generator", "judge"):
            model = self.raw[role]
            if model.get("thinking") is not False:
                raise ValueError(f"{role}.thinking must be false")
            if not model.get("model_id") or not model.get("revision") or not model.get("family"):
                raise ValueError(f"{role} requires exact family, model_id, and revision")
        if production or self.profile == "production":
            ids = [self.raw[role]["model_id"] for role in ("generator", "judge")]
            families = [self.raw[role]["family"] for role in ("generator", "judge")]
            if any(str(value).startswith("SET_") for value in (*ids, *families)):
                raise ValueError("production is locked until exact generator and judge model IDs are configured")
            if (ids[0] == ids[1] or families[0] == families[1]) and not self.raw.get("allow_self_judge", False):
                raise ValueError("formal production requires different generator and judge model families")


def load_config(path: str | Path) -> PipelineConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = PipelineConfig(config_path, raw, object_hash(raw))
    config.validate()
    return config


def require_hash(rows: list[dict[str, Any]], expected: str, artifact: Path) -> None:
    hashes = {row.get("config_hash") for row in rows}
    if hashes and hashes != {expected}:
        raise ValueError(f"mixed or stale config hashes in {artifact}: {sorted(str(x) for x in hashes)}")
