"""Prepare, generate, filter, and audit new source-grounded SFT data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lacanllm.data.io import read_jsonl, sha256_file, write_json, write_jsonl
from lacanllm.data.quality import (
    BOILERPLATE,
    META_QUESTION_PATTERNS,
    MOJIBAKE_MARKERS,
    classify_question,
    repeated_ngram,
)
from lacanllm.data.text import clean_human_text, comparison_key, fingerprint, hamming_distance, simhash64, word_tokens

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTENT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "his",
    "i",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "what",
    "which",
    "with",
}
GENERIC_SOURCE_REFERENCES = (
    "according to the text",
    "according to this text",
    "according to the passage",
    "according to this passage",
    "according to the source",
    "the text suggests",
    "the text states",
    "the passage suggests",
    "the passage states",
    "the author suggests",
    "the author argues",
    "the author states",
    "in the text",
    "in this text",
    "in the passage",
    "in this passage",
)
AMBIGUOUS_QUESTION_REFERENCES = (
    "the author",
    "the speaker",
    "the phenomenon",
    "this phenomenon",
    "in question",
    "what concept is being",
    "what idea is being",
)


class SimHashIndex:
    """Band-indexed SimHash lookup with exact Hamming-distance confirmation."""

    def __init__(self, max_distance: int, bands: int = 16):
        self.max_distance = max_distance
        self.bands = bands
        self.band_bits = 64 // bands
        self.mask = (1 << self.band_bits) - 1
        self.buckets: dict[tuple[int, int], set[int]] = defaultdict(set)

    def is_near_duplicate(self, value: str) -> bool:
        value_hash = simhash64(value)
        candidates: set[int] = set()
        for band in range(self.bands):
            band_value = (value_hash >> (band * self.band_bits)) & self.mask
            candidates.update(self.buckets[(band, band_value)])
        return any(hamming_distance(value_hash, candidate) <= self.max_distance for candidate in candidates)

    def add(self, value: str) -> None:
        value_hash = simhash64(value)
        for band in range(self.bands):
            band_value = (value_hash >> (band * self.band_bits)) & self.mask
            self.buckets[(band, band_value)].add(value_hash)


@dataclass(frozen=True)
class SFTConfig:
    dataset_version: str
    paragraphs_file: Path
    evaluation_audit_file: Path
    queue_file: Path
    generations_file: Path
    train_file: Path
    audit_file: Path
    model_id: str
    seed: int
    queue_size: int
    target_train_size: int
    human_review_sample_size: int
    max_paragraphs_per_source: int
    min_evidence_chars: int
    max_evidence_chars: int
    min_question_chars: int
    max_question_chars: int
    min_answer_chars: int
    max_answer_chars: int
    min_evidence_quote_chars: int
    max_evidence_quote_chars: int
    min_answer_evidence_overlap: float
    max_new_tokens: int
    generation_batch_size: int
    temperature: float
    top_p: float
    load_in_4bit: bool

    @classmethod
    def load(cls, path: Path) -> SFTConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "paragraphs_file",
            "evaluation_audit_file",
            "queue_file",
            "generations_file",
            "train_file",
            "audit_file",
        ):
            value = Path(raw[key])
            raw[key] = value if value.is_absolute() else PROJECT_ROOT / value
        return cls(**raw)


def is_usable_evidence(text: str, config: SFTConfig) -> tuple[bool, str | None]:
    lowered = text.casefold()
    if not config.min_evidence_chars <= len(text) <= config.max_evidence_chars:
        return False, "evidence_length"
    if any(term in lowered for term in BOILERPLATE):
        return False, "boilerplate"
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return False, "mojibake"
    alphabetical = len(re.findall(r"[A-Za-z]", text))
    if not text or alphabetical / len(text) < 0.6:
        return False, "low_alphabetic_ratio"
    if repeated_ngram(text):
        return False, "repetitive_evidence"
    return True, None


def prepare_queue(config: SFTConfig) -> dict[str, Any]:
    """Build a deterministic generation queue using training-only sources."""

    evaluation_audit = json.loads(config.evaluation_audit_file.read_text(encoding="utf-8"))
    training_sources = set(evaluation_audit["training_sources_reserved"])
    holdout_sources = {
        row["source_file"]
        for path in (
            config.evaluation_audit_file.parent.parent / "processed" / "evaluation_v1" / "validation.jsonl",
            config.evaluation_audit_file.parent.parent / "processed" / "evaluation_v1" / "test.jsonl",
        )
        for _, row in read_jsonl(path)
    }
    if training_sources & holdout_sources:
        raise RuntimeError("Evaluation audit contains overlapping training and holdout sources")

    rejections: Counter[str] = Counter()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_evidence: set[str] = set()
    for _, row in read_jsonl(config.paragraphs_file):
        source_file = str(row.get("source_file") or "")
        if source_file not in training_sources:
            continue
        evidence = clean_human_text(row.get("text"))
        usable, reason = is_usable_evidence(evidence, config)
        if not usable:
            rejections[reason or "unknown"] += 1
            continue
        evidence_id = fingerprint(evidence)
        if evidence_id in seen_evidence:
            rejections["duplicate_evidence"] += 1
            continue
        seen_evidence.add(evidence_id)
        grouped[source_file].append(
            {
                "evidence_id": evidence_id,
                "source_file": source_file,
                "paragraph_index": int(row["paragraph_index"]),
                "evidence": evidence,
                "evidence_chars": len(evidence),
                "evidence_quality_score": round(1.0 - min(abs(len(evidence) - 700) / 7000, 0.2), 4),
            }
        )

    rng = random.Random(config.seed)
    for source_rows in grouped.values():
        rng.shuffle(source_rows)
        source_rows.sort(key=lambda row: (row["evidence_quality_score"], row["evidence_id"]), reverse=True)
        del source_rows[config.max_paragraphs_per_source :]

    selected: list[dict[str, Any]] = []
    sources = sorted(grouped)
    while len(selected) < config.queue_size and any(grouped[source] for source in sources):
        rng.shuffle(sources)
        sources.sort(key=lambda source: len(grouped[source]), reverse=True)
        for source in sources:
            if grouped[source]:
                selected.append(grouped[source].pop(0))
            if len(selected) >= config.queue_size:
                break
    if len(selected) < config.queue_size:
        raise ValueError(f"Only {len(selected)} eligible training paragraphs for queue target {config.queue_size}")
    for queue_index, row in enumerate(selected, 1):
        row["queue_index"] = queue_index
        row["dataset_version"] = config.dataset_version

    write_jsonl(config.queue_file, selected)
    summary = {
        "dataset_version": config.dataset_version,
        "queue_rows": len(selected),
        "training_sources": len({row["source_file"] for row in selected}),
        "holdout_sources_excluded": len(holdout_sources),
        "shared_holdout_sources": sorted({row["source_file"] for row in selected} & holdout_sources),
        "rejection_counts": dict(sorted(rejections.items())),
        "queue_file": str(config.queue_file.relative_to(PROJECT_ROOT)),
    }
    return summary


GENERATION_PROMPT = """You are creating high-quality supervised fine-tuning data
for a scholarly assistant specializing in Jacques Lacan.

Using only the source passage below, produce one question-answer pair.

Requirements:
- The question must be a natural, specific scholarly question answerable from the passage.
- Ask the scholarly question directly: never use the words text, passage, excerpt, source, or "according to".
- The question must stand alone. Name the concept, person, work, or claim instead of saying
  "the author", "the speaker", "the phenomenon", or another context-dependent placeholder.
- The answer must directly answer the question in 2-5 concise sentences.
- The answer must stand alone and must not refer to a text, passage, excerpt, or source.
- Paraphrase and synthesize; do not copy the whole passage.
- Do not add facts that are absent from the source.
- evidence_quote must be one exact continuous quotation of 30-320 characters from the source.
- question_type must be one of: definition, comparison, explanation, clinical, textual_interpretation, other.
- Return only the following tags. Do not use Markdown or JSON.

<question>...</question>
<answer>...</answer>
<evidence_quote>...</evidence_quote>
<question_type>...</question_type>

Source passage:
{evidence}
"""


def parse_generated_json(text: str) -> dict[str, str]:
    """Parse the current tagged format with a JSON fallback for older runs."""

    required = ("question", "answer", "evidence_quote")
    tagged = {
        key: clean_human_text(match.group(1)) if (match := re.search(rf"<{key}>(.*?)</{key}>", text, re.DOTALL)) else ""
        for key in (*required, "question_type")
    }
    if all(tagged[key] for key in required):
        tagged["question_type"] = tagged["question_type"] or classify_question(tagged["question"])
        return tagged

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No tagged payload or JSON object found")
    candidate = text[start : end + 1]
    candidate = re.sub(r"}\s*,\s*\"question_type\"\s*:", ',"question_type":', candidate)
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Generated payload must be an object")
    parsed = {key: clean_human_text(payload.get(key)) for key in required}
    missing = [key for key in required if not parsed[key]]
    if missing:
        raise ValueError(f"Generated JSON has empty fields: {missing}")
    parsed["question_type"] = clean_human_text(payload.get("question_type")) or classify_question(parsed["question"])
    return parsed


def load_generator(config: SFTConfig):
    """Load the gated generation model only when the generate command is used."""

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is required for generation")
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for local SFT generation")
    quantization_config = (
        BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4") if config.load_in_4bit else None
    )
    processor = AutoProcessor.from_pretrained(config.model_id, token=token)
    model = AutoModelForMultimodalLM.from_pretrained(
        config.model_id,
        token=token,
        device_map="auto",
        quantization_config=quantization_config,
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        attn_implementation="sdpa",
    )
    model.eval()
    return model, processor


def generate_batch(model, processor, prompts: list[str], config: SFTConfig) -> list[str]:
    import torch

    inputs = processor.apply_chat_template(
        [[{"role": "user", "content": prompt}] for prompt in prompts],
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        padding=True,
        add_generation_prompt=True,
        enable_thinking=False,
    ).to(model.device)
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=config.max_new_tokens,
            do_sample=config.temperature > 0,
            temperature=config.temperature,
            top_p=config.top_p,
            pad_token_id=processor.tokenizer.eos_token_id,
        )
    generated = output[:, inputs["input_ids"].shape[-1] :]
    return [text.strip() for text in processor.batch_decode(generated, skip_special_tokens=True)]


def run_generation(config: SFTConfig, *, limit: int | None = None) -> dict[str, Any]:
    """Generate resumably, flushing one complete JSONL record per source paragraph."""

    if not config.queue_file.is_file():
        raise FileNotFoundError(f"Prepare the generation queue first: {config.queue_file}")
    completed: set[str] = set()
    if config.generations_file.exists():
        completed = {str(row["evidence_id"]) for _, row in read_jsonl(config.generations_file)}
    pending = [row for _, row in read_jsonl(config.queue_file) if row["evidence_id"] not in completed]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return {"generated": 0, "already_completed": len(completed), "pending": 0}

    model, processor = load_generator(config)
    config.generations_file.parent.mkdir(parents=True, exist_ok=True)
    generated_count = 0
    parse_errors = 0
    with config.generations_file.open("a", encoding="utf-8", newline="\n") as handle:
        for batch_start in range(0, len(pending), config.generation_batch_size):
            batch = pending[batch_start : batch_start + config.generation_batch_size]
            prompts = [GENERATION_PROMPT.format(evidence=source["evidence"]) for source in batch]
            try:
                raw_outputs = generate_batch(model, processor, prompts, config)
            except RuntimeError:
                if len(batch) == 1:
                    raise
                raw_outputs = [generate_batch(model, processor, [prompt], config)[0] for prompt in prompts]
            for source, raw_output in zip(batch, raw_outputs, strict=True):
                record: dict[str, Any] = {
                    **source,
                    "generator_model": config.model_id,
                    "generator_prompt_version": "sft_v2.4",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "raw_generation": raw_output,
                }
                try:
                    record.update(parse_generated_json(raw_output))
                    record["generation_status"] = "parsed"
                except (ValueError, json.JSONDecodeError) as exc:
                    record["generation_status"] = "parse_error"
                    record["generation_error"] = str(exc)
                    parse_errors += 1
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                generated_count += 1
    return {
        "generated": generated_count,
        "parse_errors": parse_errors,
        "already_completed": len(completed),
        "remaining": len([row for _, row in read_jsonl(config.queue_file)]) - len(completed) - generated_count,
    }


def content_overlap(answer: str, evidence: str) -> float:
    answer_tokens = {token for token in word_tokens(answer) if token not in CONTENT_STOPWORDS and len(token) > 2}
    evidence_tokens = {token for token in word_tokens(evidence) if token not in CONTENT_STOPWORDS and len(token) > 2}
    if not answer_tokens:
        return 0.0
    return len(answer_tokens & evidence_tokens) / len(answer_tokens)


def validate_generation(row: dict[str, Any], config: SFTConfig) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    if row.get("generation_status") != "parsed":
        return ["generation_parse_error"], {}
    question = clean_human_text(row.get("question"))
    answer = clean_human_text(row.get("answer"))
    evidence = clean_human_text(row.get("evidence"))
    quote = clean_human_text(row.get("evidence_quote"))
    if not config.min_question_chars <= len(question) <= config.max_question_chars:
        reasons.append("question_length")
    if not question.endswith("?"):
        reasons.append("question_not_interrogative")
    if any(pattern in question.casefold() for pattern in META_QUESTION_PATTERNS):
        reasons.append("meta_question")
    if any(pattern in question.casefold() for pattern in GENERIC_SOURCE_REFERENCES):
        reasons.append("generic_source_reference")
    if any(pattern in question.casefold() for pattern in AMBIGUOUS_QUESTION_REFERENCES):
        reasons.append("ambiguous_question_reference")
    if not config.min_answer_chars <= len(answer) <= config.max_answer_chars:
        reasons.append("answer_length")
    if repeated_ngram(answer):
        reasons.append("repetitive_answer")
    if any(pattern in answer.casefold() for pattern in GENERIC_SOURCE_REFERENCES):
        reasons.append("generic_source_reference")
    if len(quote) < config.min_evidence_quote_chars:
        reasons.append("evidence_quote_length")
    if len(quote) > config.max_evidence_quote_chars:
        reasons.append("evidence_quote_length")
    if comparison_key(quote) not in comparison_key(evidence):
        reasons.append("evidence_quote_not_exact")
    overlap = content_overlap(answer, evidence)
    if overlap < config.min_answer_evidence_overlap:
        reasons.append("low_answer_evidence_overlap")
    answer_key = comparison_key(answer)
    if len(word_tokens(answer)) >= 40 and answer_key in comparison_key(evidence):
        reasons.append("long_direct_copy")
    normalized = {
        "question": question,
        "answer": answer,
        "evidence_quote": quote,
        "question_type": classify_question(question),
        "answer_evidence_overlap": round(overlap, 4),
    }
    return reasons, normalized


def filter_generations(config: SFTConfig, *, allow_partial: bool = False) -> dict[str, Any]:
    if not config.generations_file.is_file():
        raise FileNotFoundError(config.generations_file)
    if not config.queue_file.is_file():
        raise FileNotFoundError(config.queue_file)
    evaluation_audit = json.loads(config.evaluation_audit_file.read_text(encoding="utf-8"))
    training_sources = set(evaluation_audit["training_sources_reserved"])
    queued_evidence_ids = {str(row["evidence_id"]) for _, row in read_jsonl(config.queue_file)}
    rejection_counts: Counter[str] = Counter()
    accepted: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    seen_answers: set[str] = set()
    seen_evidence: set[str] = set()
    near_questions = SimHashIndex(max_distance=14)
    near_answers = SimHashIndex(max_distance=8)

    for _, stored_row in read_jsonl(config.generations_file):
        row = dict(stored_row)
        if row.get("generation_status") != "parsed" and row.get("raw_generation"):
            try:
                row.update(parse_generated_json(str(row["raw_generation"])))
                row["generation_status"] = "parsed"
            except (ValueError, json.JSONDecodeError):
                pass
        reasons, normalized = validate_generation(row, config)
        if str(row.get("evidence_id", "")) not in queued_evidence_ids:
            reasons.append("not_in_current_queue")
        if str(row.get("source_file")) not in training_sources:
            reasons.append("holdout_source")
        question_id = fingerprint(normalized.get("question", ""))
        answer_id = fingerprint(normalized.get("answer", ""))
        evidence_id = str(row.get("evidence_id", ""))
        if not reasons and question_id in seen_questions:
            reasons.append("duplicate_question")
        if not reasons and answer_id in seen_answers:
            reasons.append("duplicate_answer")
        if not reasons and evidence_id in seen_evidence:
            reasons.append("duplicate_evidence")
        if not reasons and near_questions.is_near_duplicate(normalized["question"]):
            reasons.append("near_duplicate_question")
        if not reasons and near_answers.is_near_duplicate(normalized["answer"]):
            reasons.append("near_duplicate_answer")
        if reasons:
            rejection_counts.update(reasons)
            continue
        seen_questions.add(question_id)
        seen_answers.add(answer_id)
        seen_evidence.add(evidence_id)
        near_questions.add(normalized["question"])
        near_answers.add(normalized["answer"])
        quality_score = 0.55 * normalized["answer_evidence_overlap"]
        quality_score += 0.25 * float(row["evidence_quality_score"])
        quality_score += 0.2 * (1.0 - min(abs(len(normalized["answer"]) - 350) / 1800, 0.2))
        accepted.append(
            {
                "sft_id": fingerprint(f"{normalized['question']}\n{normalized['answer']}"),
                "messages": [
                    {"role": "user", "content": normalized["question"]},
                    {"role": "assistant", "content": normalized["answer"]},
                ],
                **normalized,
                "source_file": row["source_file"],
                "paragraph_index": row["paragraph_index"],
                "evidence": row["evidence"],
                "evidence_id": evidence_id,
                "generator_model": row["generator_model"],
                "generator_prompt_version": row["generator_prompt_version"],
                "quality_score": round(quality_score, 4),
                "dataset_version": config.dataset_version,
                "review_status": "pending_human_sample_review",
            }
        )

    accepted.sort(key=lambda row: (row["quality_score"], row["sft_id"]), reverse=True)
    selected = accepted[: config.target_train_size]
    if len(selected) < config.target_train_size and not allow_partial:
        raise RuntimeError(
            f"Only {len(selected)} accepted generations for target {config.target_train_size}; "
            "continue generation or pass --allow-partial for a probe build"
        )
    write_jsonl(config.train_file, selected)
    review_path = config.train_file.with_name("human_review_sample.csv")
    write_sft_review_sample(review_path, selected, config)
    holdout_sources = {
        row["source_file"]
        for path in (
            PROJECT_ROOT / "data" / "processed" / "evaluation_v1" / "validation.jsonl",
            PROJECT_ROOT / "data" / "processed" / "evaluation_v1" / "test.jsonl",
        )
        for _, row in read_jsonl(path)
    }
    selected_sources = {row["source_file"] for row in selected}
    audit = {
        "dataset_version": config.dataset_version,
        "created_at": datetime.now(UTC).isoformat(),
        "generation_rows": sum(1 for _ in read_jsonl(config.generations_file)),
        "accepted_before_target_limit": len(accepted),
        "train_rows": len(selected),
        "target_train_rows": config.target_train_size,
        "training_sources": len(selected_sources),
        "question_types": dict(sorted(Counter(row["question_type"] for row in selected).items())),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "mean_answer_evidence_overlap": (
            round(sum(row["answer_evidence_overlap"] for row in selected) / len(selected), 4) if selected else 0.0
        ),
        "shared_holdout_sources": sorted(selected_sources & holdout_sources),
        "human_review_sample": str(review_path.relative_to(PROJECT_ROOT)),
        "human_review_sample_rows": min(len(selected), config.human_review_sample_size),
        "inputs": {
            "paragraphs_sha256": sha256_file(config.paragraphs_file),
            "evaluation_audit_sha256": sha256_file(config.evaluation_audit_file),
            "generations_sha256": sha256_file(config.generations_file),
        },
        "checks": {
            "target_met": len(selected) == config.target_train_size,
            "holdout_source_isolation": not (selected_sources & holdout_sources),
            "unique_questions": len({fingerprint(row["question"]) for row in selected}) == len(selected),
            "unique_answers": len({fingerprint(row["answer"]) for row in selected}) == len(selected),
            "unique_evidence": len({row["evidence_id"] for row in selected}) == len(selected),
        },
        "partial_probe": allow_partial and len(selected) < config.target_train_size,
        "config": config_for_json(config),
    }
    write_json(config.audit_file, audit)
    required_checks = {key: value for key, value in audit["checks"].items() if key != "target_met" or not allow_partial}
    if not all(required_checks.values()):
        raise RuntimeError(f"SFT audit failed: {audit['checks']}")
    return audit


def write_sft_review_sample(path: Path, rows: list[dict[str, Any]], config: SFTConfig) -> None:
    """Write a deterministic, source-diverse human-review sample."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_file"])].append(row)
    rng = random.Random(config.seed)
    for source_rows in grouped.values():
        rng.shuffle(source_rows)
    sources = sorted(grouped)
    sampled: list[dict[str, Any]] = []
    target = min(len(rows), config.human_review_sample_size)
    while len(sampled) < target and any(grouped[source] for source in sources):
        rng.shuffle(sources)
        for source in sources:
            if grouped[source]:
                sampled.append(grouped[source].pop())
            if len(sampled) >= target:
                break

    fields = (
        "sft_id",
        "source_file",
        "paragraph_index",
        "question_type",
        "question",
        "answer",
        "evidence_quote",
        "evidence",
        "answer_evidence_overlap",
        "quality_score",
        "answerability_1_5",
        "theoretical_accuracy_1_5",
        "evidence_fidelity_1_5",
        "wording_quality_1_5",
        "decision",
        "reviewer",
        "notes",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sampled)
    temporary.replace(path)


def config_for_json(config: SFTConfig) -> dict[str, Any]:
    payload = asdict(config)
    return {
        key: str(value.relative_to(PROJECT_ROOT)) if isinstance(value, Path) else value
        for key, value in payload.items()
    }


def pipeline_status(config: SFTConfig) -> dict[str, Any]:
    queue_rows = list(read_jsonl(config.queue_file)) if config.queue_file.exists() else []
    generation_rows = list(read_jsonl(config.generations_file)) if config.generations_file.exists() else []
    completed_ids = {str(row["evidence_id"]) for _, row in generation_rows}
    statuses = Counter(str(row.get("generation_status", "unknown")) for _, row in generation_rows)
    versions = Counter(str(row.get("generator_prompt_version", "unknown")) for _, row in generation_rows)
    audit = json.loads(config.audit_file.read_text(encoding="utf-8")) if config.audit_file.exists() else {}
    return {
        "queue_rows": len(queue_rows),
        "generated_rows": len(generation_rows),
        "unique_completed_evidence": len(completed_ids),
        "remaining_rows": max(0, len(queue_rows) - len(completed_ids)),
        "generation_statuses": dict(sorted(statuses.items())),
        "prompt_versions": dict(sorted(versions.items())),
        "latest_filtered_train_rows": audit.get("train_rows", 0),
        "target_train_rows": config.target_train_size,
        "generation_complete": bool(queue_rows) and len(completed_ids) == len(queue_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "generate", "filter", "status"))
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "data" / "sft_v2.json")
    parser.add_argument("--limit", type=int, default=None, help="Generate only this many pending rows")
    parser.add_argument("--allow-partial", action="store_true", help="Allow a probe dataset smaller than target")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config if args.config.is_absolute() else PROJECT_ROOT / args.config
    config = SFTConfig.load(config_path)
    if args.command == "prepare":
        result = prepare_queue(config)
    elif args.command == "generate":
        result = run_generation(config, limit=args.limit)
    elif args.command == "filter":
        result = filter_generations(config, allow_partial=args.allow_partial)
    else:
        result = pipeline_status(config)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
