"""Build a reduced final release from existing Pipeline v2 consensus rows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lacanllm.data.io import sha256_file, write_json, write_jsonl
from lacanllm.data.pipeline_v2_core import (
    PROJECT_ROOT,
    SPLITS,
    PipelineConfig,
    _global_deduplicate,
    _select_with_quotas,
    canonical_sha256,
    fingerprint,
    load_split_manifest,
    utc_now,
)

DEFAULT_PIPELINE_CONFIG = PROJECT_ROOT / "configs" / "data" / "pipeline_v2.json"
DEFAULT_RELEASE_CONFIG = PROJECT_ROOT / "configs" / "data" / "final_release.json"


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def build_release(pipeline: PipelineConfig, release_path: Path) -> dict[str, Any]:
    """Select, seal, and audit a release without changing artifact provenance hashes."""

    resolved_release_path = _resolve(release_path)
    profile = json.loads(resolved_release_path.read_text(encoding="utf-8"))
    profile_hash = canonical_sha256(profile)
    excluded = set(map(str, profile["excluded_question_types"]))
    quotas = profile["final_quotas"]
    floors = profile.get("source_floors", {})
    split_manifest = load_split_manifest(pipeline)
    pools, global_duplicates = _global_deduplicate(pipeline)
    outputs = {
        "train": pipeline.path("train_output"),
        "validation": pipeline.path("benchmark_root") / "validation.jsonl",
        "test": pipeline.path("benchmark_root") / "test.jsonl",
        "challenge": pipeline.path("benchmark_root") / "challenge.jsonl",
    }
    selected_by_split: dict[str, list[dict[str, Any]]] = {}
    selection: dict[str, Any] = {}

    for split in SPLITS:
        split_quotas = {str(key): int(value) for key, value in quotas[split].items()}
        if excluded & set(split_quotas):
            raise ValueError(f"Excluded type appears in {split} quotas: {excluded & set(split_quotas)}")
        eligible = [row for row in pools[split] if str(row.get("question_type")) not in excluded]
        selected, deficits = _select_with_quotas(
            eligible,
            split_quotas,
            int(pipeline.raw["final_source_caps"][split]),
            source_floor=int(floors.get(split, 0)),
            required_sources=split_manifest["splits"][split],
        )
        if deficits:
            raise RuntimeError(f"Release selection deficits for {split}: {deficits}")
        rows = [
            {
                **row,
                "id": fingerprint(f"{pipeline.raw['sft_version']}:{row['candidate_id']}"),
                "messages": [
                    {"role": "user", "content": row["question"]},
                    {"role": "assistant", "content": row["answer"]},
                ],
                "dataset_version": (
                    pipeline.raw["sft_version"] if split == "train" else pipeline.raw["benchmark_version"]
                ),
                "release_version": profile["release_version"],
                "release_profile_hash": profile_hash,
                "selected_at": utc_now(),
            }
            for row in selected
        ]
        write_jsonl(outputs[split], rows)
        selected_by_split[split] = rows
        selection[split] = {
            "rows": len(rows),
            "target": sum(split_quotas.values()),
            "types": dict(sorted(Counter(row["question_type"] for row in rows).items())),
            "sources": dict(sorted(Counter(str(row["source_file"]) for row in rows).items())),
        }

    test_seal = {
        "sealed": True,
        "benchmark_grade": "silver",
        "dataset_version": pipeline.raw["benchmark_version"],
        "release_version": profile["release_version"],
        "release_profile_hash": profile_hash,
        "rows": len(selected_by_split["test"]),
        "sha256": sha256_file(outputs["test"]),
        "sealed_at": utc_now(),
        "pipeline_config_hash": pipeline.config_hash,
    }
    write_json(pipeline.path("test_manifest"), test_seal)

    all_rows = [row for split in SPLITS for row in selected_by_split[split]]
    questions = [fingerprint(row["question"]) for row in all_rows]
    answers = [fingerprint(row["answer"]) for row in all_rows]
    checks = {
        "target_counts": all(selection[split]["rows"] == selection[split]["target"] for split in SPLITS),
        "type_quotas": all(selection[split]["types"] == dict(sorted(quotas[split].items())) for split in SPLITS),
        "excluded_types_absent": all(row["question_type"] not in excluded for row in all_rows),
        "global_exact_questions_unique": len(questions) == len(set(questions)),
        "global_exact_answers_unique": len(answers) == len(set(answers)),
        "pipeline_provenance_preserved": all(row.get("config_hash") == pipeline.config_hash for row in all_rows),
        "dual_judge_present": all(set(row.get("judge_results", {})) == {"rubric", "adversarial"} for row in all_rows),
        "silver_benchmark_labels": all(
            row.get("benchmark_grade") == "silver"
            for split in ("validation", "test", "challenge")
            for row in selected_by_split[split]
        ),
        "test_sealed": test_seal["sha256"] == sha256_file(outputs["test"]),
        "split_manifest_valid": all(split_manifest["checks"].values()),
    }
    report = {
        "release_version": profile["release_version"],
        "created_at": utc_now(),
        "pipeline_version": pipeline.pipeline_version,
        "pipeline_config_hash": pipeline.config_hash,
        "release_profile_hash": profile_hash,
        "release_profile": str(resolved_release_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "excluded_question_types": sorted(excluded),
        "benchmark_grade": "silver",
        "selection": selection,
        "global_duplicate_rows": len(global_duplicates),
        "artifact_sha256": {split: sha256_file(path) for split, path in outputs.items()},
        "test_seal": test_seal,
        "checks": checks,
        "known_limitations": [
            "No human review was performed.",
            "Anonymous source files may originate from the same underlying work.",
            "Validation has no source floor because one fixed source has only 32 eligible consensus rows.",
            "The other and ambiguous question types are intentionally excluded from this release.",
        ],
    }
    manifest_path = _resolve(Path(profile["manifest_file"]))
    audit_path = _resolve(Path(profile["audit_file"]))
    write_json(manifest_path, report)
    write_json(audit_path, report)
    if not all(checks.values()):
        raise RuntimeError(f"Final release audit failed: {checks}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-config", type=Path, default=DEFAULT_PIPELINE_CONFIG)
    parser.add_argument("--release-config", type=Path, default=DEFAULT_RELEASE_CONFIG)
    args = parser.parse_args()
    report = build_release(PipelineConfig.load(args.pipeline_config), args.release_config)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
