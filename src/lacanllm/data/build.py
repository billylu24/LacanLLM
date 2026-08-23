"""Build the phase-1, source-isolated validation and test datasets."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lacanllm.data.io import read_jsonl, sha256_file, write_json, write_jsonl
from lacanllm.data.provenance import ParagraphIndex
from lacanllm.data.quality import assess_quality
from lacanllm.data.split import reserve_holdout_sources, select_with_source_cap
from lacanllm.data.text import fingerprint

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class BuildConfig:
    dataset_version: str
    paragraphs_file: Path
    qa_candidates_file: Path
    output_dir: Path
    audit_file: Path
    rejections_file: Path
    validation_size: int
    test_size: int
    seed: int
    min_question_chars: int
    max_question_chars: int
    min_answer_chars: int
    max_answer_chars: int
    minimum_provenance_score: float
    max_examples_per_source: int

    @classmethod
    def load(cls, path: Path) -> BuildConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key in ("paragraphs_file", "qa_candidates_file", "output_dir", "audit_file", "rejections_file"):
            value = Path(raw[key])
            raw[key] = value if value.is_absolute() else PROJECT_ROOT / value
        return cls(**raw)


def build(config: BuildConfig) -> dict[str, Any]:
    if config.validation_size <= 0 or config.test_size <= 0:
        raise ValueError("Validation and test sizes must be positive")
    for path in (config.paragraphs_file, config.qa_candidates_file):
        if not path.is_file():
            raise FileNotFoundError(path)

    index = ParagraphIndex(config.paragraphs_file)
    rejection_counts: Counter[str] = Counter()
    rejections: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for line_number, row in read_jsonl(config.qa_candidates_file):
        quality = assess_quality(
            row.get("instruction"),
            row.get("output"),
            min_question_chars=config.min_question_chars,
            max_question_chars=config.max_question_chars,
            min_answer_chars=config.min_answer_chars,
            max_answer_chars=config.max_answer_chars,
        )
        if not quality.accepted:
            for reason in quality.rejection_reasons:
                rejection_counts[reason] += 1
            rejections.append({"candidate_line": line_number, "reasons": list(quality.rejection_reasons)})
            continue

        provenance = index.match(quality.answer)
        if provenance is None or provenance.score < config.minimum_provenance_score:
            rejection_counts["provenance_not_recovered"] += 1
            rejections.append({"candidate_line": line_number, "reasons": ["provenance_not_recovered"]})
            continue

        qa_id = fingerprint(f"{quality.question}\n{quality.answer}")
        candidates.append(
            {
                "qa_id": qa_id,
                "question": quality.question,
                "answer": quality.answer,
                "question_type": quality.question_type,
                "source_file": provenance.source_file,
                "paragraph_index": provenance.paragraph_index,
                "evidence": provenance.evidence,
                "quality_score": quality.score,
                "provenance_score": provenance.score,
                "provenance_method": provenance.method,
                "answer_fingerprint": fingerprint(quality.answer),
                "question_fingerprint": fingerprint(quality.question),
                "evidence_fingerprint": fingerprint(provenance.evidence),
                "legacy_candidate_line": line_number,
            }
        )

    candidates.sort(key=lambda item: (item["quality_score"], item["provenance_score"], item["qa_id"]), reverse=True)
    deduplicated: list[dict[str, Any]] = []
    seen_answers: set[str] = set()
    seen_questions: set[str] = set()
    seen_evidence: set[str] = set()
    for row in candidates:
        if row["answer_fingerprint"] in seen_answers:
            rejection_counts["duplicate_answer"] += 1
            continue
        if row["question_fingerprint"] in seen_questions:
            rejection_counts["duplicate_question"] += 1
            continue
        if row["evidence_fingerprint"] in seen_evidence:
            rejection_counts["duplicate_evidence"] += 1
            continue
        seen_answers.add(row["answer_fingerprint"])
        seen_questions.add(row["question_fingerprint"])
        seen_evidence.add(row["evidence_fingerprint"])
        deduplicated.append(row)

    validation_sources, test_sources, training_sources = reserve_holdout_sources(
        deduplicated,
        validation_target=config.validation_size,
        test_target=config.test_size,
        seed=config.seed,
        per_source_cap=config.max_examples_per_source,
    )
    validation_pool = [row for row in deduplicated if row["source_file"] in validation_sources]
    test_pool = [row for row in deduplicated if row["source_file"] in test_sources]
    validation = select_with_source_cap(
        validation_pool,
        target=config.validation_size,
        per_source_cap=config.max_examples_per_source,
    )
    test = select_with_source_cap(
        test_pool,
        target=config.test_size,
        per_source_cap=config.max_examples_per_source,
    )

    rng = random.Random(config.seed)
    rng.shuffle(validation)
    rng.shuffle(test)
    for split_name, rows in (("validation", validation), ("test", test)):
        for row in rows:
            row["split"] = split_name
            row["dataset_version"] = config.dataset_version
            row["review_status"] = "pending_human_review"

    validation_path = config.output_dir / "validation.jsonl"
    test_path = config.output_dir / "test.jsonl"
    write_jsonl(validation_path, validation)
    write_jsonl(test_path, test)
    write_jsonl(config.rejections_file, rejections)
    review_path = config.output_dir / "human_review.csv"
    write_human_review_queue(review_path, validation + test)

    validation_answer_ids = {row["answer_fingerprint"] for row in validation}
    test_answer_ids = {row["answer_fingerprint"] for row in test}
    validation_question_ids = {row["question_fingerprint"] for row in validation}
    test_question_ids = {row["question_fingerprint"] for row in test}
    validation_evidence_ids = {row["evidence_fingerprint"] for row in validation}
    test_evidence_ids = {row["evidence_fingerprint"] for row in test}
    shared_sources = validation_sources & test_sources
    shared_answers = validation_answer_ids & test_answer_ids
    shared_questions = validation_question_ids & test_question_ids
    shared_evidence = validation_evidence_ids & test_evidence_ids
    audit: dict[str, Any] = {
        "dataset_version": config.dataset_version,
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "paragraphs_file": str(config.paragraphs_file.relative_to(PROJECT_ROOT)),
            "paragraphs_sha256": sha256_file(config.paragraphs_file),
            "qa_candidates_file": str(config.qa_candidates_file.relative_to(PROJECT_ROOT)),
            "qa_candidates_sha256": sha256_file(config.qa_candidates_file),
        },
        "paragraphs_indexed": index.paragraph_count,
        "accepted_before_deduplication": len(candidates),
        "accepted_after_deduplication": len(deduplicated),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "validation": split_summary(validation),
        "test": split_summary(test),
        "human_review_queue": str(review_path.relative_to(PROJECT_ROOT)),
        "training_sources_reserved": sorted(training_sources),
        "training_source_count": len(training_sources),
        "shared_sources": sorted(shared_sources),
        "shared_answer_fingerprints": sorted(shared_answers),
        "shared_question_fingerprints": sorted(shared_questions),
        "shared_evidence_fingerprints": sorted(shared_evidence),
        "checks": {
            "target_total_met": len(validation) + len(test) == config.validation_size + config.test_size,
            "source_isolation": not shared_sources,
            "answer_isolation": not shared_answers,
            "question_isolation": not shared_questions,
            "evidence_isolation": not shared_evidence,
            "source_cap_respected": all(
                count <= config.max_examples_per_source
                for rows in (validation, test)
                for count in Counter(row["source_file"] for row in rows).values()
            ),
            "all_provenance_scores_pass": all(
                row["provenance_score"] >= config.minimum_provenance_score for row in validation + test
            ),
        },
        "config": {
            **asdict(config),
            **{
                key: str(value.relative_to(PROJECT_ROOT))
                for key, value in asdict(config).items()
                if isinstance(value, Path)
            },
        },
    }
    write_json(config.audit_file, audit)
    if not all(audit["checks"].values()):
        raise RuntimeError(f"Dataset audit failed: {audit['checks']}")
    return audit


def write_human_review_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    """Create a review sheet without silently presenting synthetic QA as gold data."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "qa_id",
        "split",
        "source_file",
        "paragraph_index",
        "question_type",
        "question",
        "answer",
        "evidence",
        "answerability_1_5",
        "theoretical_accuracy_1_5",
        "evidence_fidelity_1_5",
        "wording_quality_1_5",
        "decision",
        "reviewer",
        "notes",
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def split_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "sources": len({row["source_file"] for row in rows}),
        "question_types": dict(sorted(Counter(row["question_type"] for row in rows).items())),
        "mean_question_chars": round(sum(len(row["question"]) for row in rows) / len(rows), 2),
        "mean_answer_chars": round(sum(len(row["answer"]) for row in rows) / len(rows), 2),
        "mean_provenance_score": round(sum(row["provenance_score"] for row in rows) / len(rows), 4),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "data" / "evaluation_v1.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config if args.config.is_absolute() else PROJECT_ROOT / args.config
    audit = build(BuildConfig.load(config_path))
    print(json.dumps(audit, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
