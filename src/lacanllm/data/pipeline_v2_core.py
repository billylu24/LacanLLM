"""Deterministic core stages for the LacanLLM pipeline v2.

This module contains no model loading.  It is intentionally usable in unit and
integration tests with generated records supplied by a fake model backend.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lacanllm.data.io import read_jsonl, sha256_file, write_json, write_jsonl
from lacanllm.data.quality import BAD_ANSWER_PATTERNS, BOILERPLATE, META_QUESTION_PATTERNS, MOJIBAKE_MARKERS
from lacanllm.data.text import WORD_PATTERN, clean_human_text, comparison_key, fingerprint, hamming_distance, simhash64

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REGULAR_TYPES = (
    "definition",
    "explanation",
    "comparison",
    "textual_interpretation",
    "cross_concept",
    "clinical_application",
    "other",
)
CHALLENGE_TYPES = ("unanswerable", "ambiguous", "concept_confusion", "cross_concept")
SPLITS = ("train", "validation", "test", "challenge")
GENERIC_REFERENCES = (
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
AMBIGUOUS_REFERENCES = (
    "the author",
    "the speaker",
    "the phenomenon",
    "this phenomenon",
    "in question",
    "what concept is being",
    "what idea is being",
)
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
TYPE_SIGNALS = {
    "definition": (
        "means",
        "defined",
        "definition",
        "called",
        "term",
        "designate",
        "consists in",
        "is what",
    ),
    "explanation": (
        "because",
        "therefore",
        "thus",
        "function",
        "effect",
        "in order to",
        "so that",
        "consequence",
    ),
    "comparison": (
        "difference",
        "distinction",
        "between",
        "unlike",
        "rather than",
        "on the one hand",
        "on the other hand",
        "relation between",
    ),
    "textual_interpretation": (
        "seminar",
        "freud",
        "says",
        "formulation",
        "phrase",
        "written",
        "discourse",
        "signifier",
    ),
    "cross_concept": (
        "relation",
        "between",
        "desire",
        "jouissance",
        "other",
        "real",
        "symbolic",
        "imaginary",
        "signifier",
    ),
    "clinical_application": (
        "clinical",
        "analysis",
        "analyst",
        "patient",
        "treatment",
        "symptom",
        "psychosis",
        "neurosis",
        "transference",
        "cure",
    ),
    "concept_confusion": (
        "desire",
        "demand",
        "need",
        "jouissance",
        "other",
        "real",
        "symbolic",
        "imaginary",
        "subject",
        "ego",
        "signifier",
    ),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PipelineConfig:
    """Loaded JSON configuration with workspace-relative paths resolved."""

    source_path: Path
    raw: dict[str, Any]
    config_hash: str

    @classmethod
    def load(cls, path: Path) -> PipelineConfig:
        resolved = path if path.is_absolute() else PROJECT_ROOT / path
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        return cls(resolved, raw, canonical_sha256(raw))

    def path(self, key: str) -> Path:
        value = Path(self.raw["paths"][key])
        return value if value.is_absolute() else PROJECT_ROOT / value

    def interim(self, stage: str, split: str, suffix: str = "jsonl") -> Path:
        return self.path("interim_root") / stage / f"{split}.{suffix}"

    @property
    def pipeline_version(self) -> str:
        return str(self.raw["pipeline_version"])


def normalize_source_text(value: object) -> tuple[str, list[str]]:
    """Normalize representation without rewriting words or punctuation."""

    original = str(value or "")
    operations: list[str] = []
    text = unicodedata.normalize("NFKC", original)
    if text != original:
        operations.append("unicode_nfkc")
    if "\ufeff" in text:
        text = text.replace("\ufeff", "")
        operations.append("bom_removed")
    normalized = re.sub(r"[\t\f\v ]+", " ", text)
    normalized = re.sub(r"\s*\n\s*", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized != text:
        operations.append("whitespace_normalized")
    return normalized, operations


def repeated_ngram(text: str, n: int, repetitions: int) -> bool:
    tokens = comparison_key(text).split()
    if len(tokens) < n * repetitions:
        return False
    counts: Counter[tuple[str, ...]] = Counter()
    for index in range(len(tokens) - n + 1):
        ngram = tuple(tokens[index : index + n])
        counts[ngram] += 1
        if counts[ngram] >= repetitions:
            return True
    return False


def paragraph_rejection_reasons(text: str, config: PipelineConfig) -> list[str]:
    rules = config.raw["cleaning"]
    reasons: list[str] = []
    if not int(rules["min_chars"]) <= len(text) <= int(rules["max_chars"]):
        reasons.append("length")
    lowered = text.casefold()
    if any(term in lowered for term in BOILERPLATE):
        reasons.append("publisher_metadata")
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        reasons.append("mojibake")
    alphabetic = len(re.findall(r"[A-Za-z]", text))
    ratio = alphabetic / len(text) if text else 0.0
    if ratio < float(rules["min_alphabetic_ratio"]):
        reasons.append("low_alphabetic_ratio")
    if repeated_ngram(text, int(rules["repeated_ngram_size"]), int(rules["repeated_ngram_count"])):
        reasons.append("repeated_ngram")
    return list(dict.fromkeys(reasons))


def clean_corpus(config: PipelineConfig) -> dict[str, Any]:
    """Create the traceable corpus and retain every rejected raw row."""

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    rejection_counts: Counter[str] = Counter()
    for line_number, row in read_jsonl(config.path("raw_corpus")):
        raw_text = str(row.get("text") or "")
        cleaned_text, operations = normalize_source_text(raw_text)
        source_file = str(row.get("source_file") or "")
        paragraph_index = row.get("paragraph_index")
        reasons = paragraph_rejection_reasons(cleaned_text, config)
        normalized_id = fingerprint(cleaned_text)
        if normalized_id in seen:
            reasons.append("exact_duplicate")
        paragraph_id = fingerprint(f"{source_file}:{paragraph_index}:{cleaned_text}")
        common = {
            "paragraph_id": paragraph_id,
            "source_file": source_file,
            "source_work": None,
            "source_work_unknown": True,
            "paragraph_index": paragraph_index,
            "raw_text": raw_text,
            "cleaned_text": cleaned_text,
            "raw_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "cleaning_operations": operations,
            "quality_flags": reasons,
            "corpus_version": config.raw["corpus_version"],
            "config_hash": config.config_hash,
        }
        if not source_file or paragraph_index is None:
            reasons.append("missing_provenance")
        if reasons:
            rejection_counts.update(reasons)
            rejected.append({"input_line": line_number, **common, "rejection_reasons": reasons})
            continue
        seen[normalized_id] = paragraph_id
        accepted.append(common)
    write_jsonl(config.path("clean_corpus"), accepted)
    write_jsonl(config.path("clean_rejections"), rejected)
    return {
        "input_rows": len(accepted) + len(rejected),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "output_sha256": sha256_file(config.path("clean_corpus")),
    }


def build_split_manifest(config: PipelineConfig) -> dict[str, Any]:
    clean_path = config.path("clean_corpus")
    if not clean_path.is_file():
        raise FileNotFoundError(f"Run clean first: {clean_path}")
    sources = sorted({str(row["source_file"]) for _, row in read_jsonl(clean_path)})
    validation = set(map(str, config.raw["validation_sources"]))
    test = set(map(str, config.raw["test_sources"]))
    missing = sorted((validation | test) - set(sources))
    train = set(sources) - validation - test
    if missing:
        raise ValueError(f"Configured holdout sources absent from clean corpus: {missing}")
    if validation & test or train & validation or train & test:
        raise RuntimeError("Source split overlap detected")
    counts = Counter(str(row["source_file"]) for _, row in read_jsonl(clean_path))
    manifest = {
        "pipeline_version": config.pipeline_version,
        "created_at": utc_now(),
        "config_hash": config.config_hash,
        "clean_corpus_sha256": sha256_file(clean_path),
        "source_unit": "anonymous_source_file",
        "source_work_available": False,
        "known_limitation": "Anonymous files may belong to the same underlying work.",
        "splits": {
            "train": sorted(train),
            "validation": sorted(validation),
            "test": sorted(test),
            "challenge": sorted(test),
        },
        "source_row_counts": dict(sorted(counts.items())),
        "checks": {
            "train_validation_disjoint": not (train & validation),
            "train_test_disjoint": not (train & test),
            "validation_test_disjoint": not (validation & test),
            "source_count_35_5_5": (len(train), len(validation), len(test)) == (35, 5, 5),
        },
    }
    if not all(manifest["checks"].values()):
        raise RuntimeError(f"Split checks failed: {manifest['checks']}")
    write_json(config.path("split_manifest"), manifest)
    return manifest


def load_split_manifest(config: PipelineConfig) -> dict[str, Any]:
    path = config.path("split_manifest")
    if not path.is_file():
        raise FileNotFoundError(f"Run split first: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("config_hash") != config.config_hash:
        raise RuntimeError("Split manifest was built with a different configuration")
    return manifest


def split_quotas(config: PipelineConfig, split: str, *, initial: bool) -> dict[str, int]:
    final = {str(key): int(value) for key, value in config.raw["final_quotas"][split].items()}
    if not initial:
        return final
    multiplier = float(config.raw["initial_candidate_multiplier"])
    return {key: int(round(value * multiplier)) for key, value in final.items()}


def _quality_score(text: str) -> float:
    return round(1.0 - min(abs(len(text) - 700) / 7000, 0.2), 4)


def type_suitability_score(text: str, target_type: str) -> float:
    """Cheap evidence-side routing score; it never determines final quality."""

    lowered = text.casefold()
    signals = TYPE_SIGNALS.get(target_type, ())
    hits = sum(lowered.count(signal) for signal in signals)
    concept_hits = sum(
        lowered.count(signal)
        for signal in TYPE_SIGNALS["cross_concept"]
    )
    score = 0.65 * _quality_score(text) + 0.07 * min(hits, 5) + 0.025 * min(concept_hits, 4)
    if target_type == "textual_interpretation" and any(mark in text for mark in ('“', '”', '"', "‘", "’")):
        score += 0.08
    if target_type == "definition" and any(
        marker in lowered for marker in ("meeting last night", "are there some", "raise their hands", "audience")
    ):
        score -= 0.15
    return round(score, 4)


def _existing_context_ids(config: PipelineConfig) -> set[str]:
    used: set[str] = set()
    for split in SPLITS:
        path = config.interim("queues", split)
        if path.is_file():
            for _, row in read_jsonl(path):
                used.update(map(str, row.get("context_ids", [])))
    return used


def _source_rows(config: PipelineConfig, split: str) -> dict[str, list[dict[str, Any]]]:
    manifest = load_split_manifest(config)
    allowed = set(manifest["splits"][split])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, row in read_jsonl(config.path("clean_corpus")):
        if str(row["source_file"]) in allowed:
            grouped[str(row["source_file"])].append(row)
    for source, rows in grouped.items():
        rows.sort(key=lambda row: (int(row["paragraph_index"]), str(row["paragraph_id"])))
        if not rows:
            raise RuntimeError(f"No clean rows for {source}")
    return grouped


def _candidate_row(
    config: PipelineConfig,
    split: str,
    target_type: str,
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    context_ids = [str(row["paragraph_id"]) for row in contexts]
    generation_prompt_version = (
        "pipeline_v2.generate.other.1"
        if target_type == "other"
        else str(config.raw["generation"]["prompt_version"])
    )
    candidate_id = fingerprint(
        f"{config.config_hash}:{split}:{target_type}:{generation_prompt_version}:{':'.join(context_ids)}"
    )
    source_text = "\n\n".join(str(row["cleaned_text"]) for row in contexts)
    return {
        "candidate_id": candidate_id,
        "split": split,
        "target_question_type": target_type,
        "challenge_category": target_type if split == "challenge" else None,
        "source_file": contexts[0]["source_file"],
        "source_work": None,
        "source_work_unknown": True,
        "context_ids": context_ids,
        "contexts": [
            {
                "paragraph_id": row["paragraph_id"],
                "source_file": row["source_file"],
                "paragraph_index": row["paragraph_index"],
                "text": row["cleaned_text"],
            }
            for row in contexts
        ],
        "source_text": source_text,
        "evidence_quality_score": round(
            sum(_quality_score(str(row["cleaned_text"])) for row in contexts) / len(contexts),
            4,
        ),
        "type_suitability_score": type_suitability_score(source_text, target_type),
        "queue_version": config.raw["queue_version"],
        "generation_prompt_version": generation_prompt_version,
        "pipeline_version": config.pipeline_version,
        "config_hash": config.config_hash,
    }


def build_generation_queue(
    config: PipelineConfig,
    split: str,
    *,
    requested: dict[str, int] | None = None,
    append: bool = False,
    preferred_sources: Iterable[str] = (),
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, source-balanced queue for one split."""

    if split not in SPLITS:
        raise ValueError(f"Unknown split: {split}")
    requested_quotas = requested or split_quotas(config, split, initial=True)
    quotas = dict(requested_quotas)
    valid_types = CHALLENGE_TYPES if split == "challenge" else REGULAR_TYPES
    unknown = set(quotas) - set(valid_types)
    if unknown:
        raise ValueError(f"Invalid types for {split}: {sorted(unknown)}")
    queue_path = config.interim("queues", split)
    existing = [row for _, row in read_jsonl(queue_path)] if append and queue_path.is_file() else []
    if existing and any(row.get("config_hash") != config.config_hash for row in existing):
        raise RuntimeError("Existing queue uses a different configuration")
    maximum = int(config.raw["max_attempts"][split])
    requested_total = sum(quotas.values())
    if len(existing) + requested_total > maximum:
        if not allow_partial:
            raise RuntimeError(f"{split} queue would exceed max attempts {maximum}")
        remaining = max(0, maximum - len(existing))
        limited_quotas: dict[str, int] = {}
        for target_type, target in quotas.items():
            allocated = min(target, remaining)
            if allocated > 0:
                limited_quotas[target_type] = allocated
                remaining -= allocated
        quotas = limited_quotas
    unallocated = {
        target_type: int(requested_quotas[target_type]) - int(quotas.get(target_type, 0))
        for target_type in requested_quotas
        if int(requested_quotas[target_type]) > int(quotas.get(target_type, 0))
    }
    grouped = _source_rows(config, split)
    preferred_source_set = set(preferred_sources)
    unknown_sources = preferred_source_set - set(grouped)
    if unknown_sources:
        raise ValueError(f"Preferred sources are outside {split}: {sorted(unknown_sources)}")
    used = _existing_context_ids(config)
    source_counts = Counter(str(row["source_file"]) for row in existing)
    source_cap = int(config.raw["candidate_source_caps"][split])
    rng = random.Random(int(config.raw["seed"]) + SPLITS.index(split))
    source_order = sorted(grouped)
    rng.shuffle(source_order)
    ranked_by_type = {
        (source, target_type): sorted(
            rows,
            key=lambda row: (
                type_suitability_score(str(row["cleaned_text"]), target_type),
                str(row["paragraph_id"]),
            ),
            reverse=True,
        )
        for source, rows in grouped.items()
        for target_type in valid_types
    }
    positions = {(source, target_type): 0 for source in source_order for target_type in valid_types}

    def take_single(source: str, target_type: str) -> list[dict[str, Any]] | None:
        rows = ranked_by_type[(source, target_type)]
        position_key = (source, target_type)
        while positions[position_key] < len(rows):
            row = rows[positions[position_key]]
            positions[position_key] += 1
            if str(row["paragraph_id"]) not in used:
                return [row]
        return None

    def take_pair(source: str, target_type: str) -> list[dict[str, Any]] | None:
        ranked = ranked_by_type[(source, target_type)]
        ordered = grouped[source]
        by_index = {int(row["paragraph_index"]): row for row in ordered}
        position_key = (source, target_type)
        while positions[position_key] < len(ranked):
            first = ranked[positions[position_key]]
            positions[position_key] += 1
            first_id = str(first["paragraph_id"])
            if first_id in used:
                continue
            first_index = int(first["paragraph_index"])
            neighbors = [
                by_index[index]
                for distance in range(1, 11)
                for index in (first_index + distance, first_index - distance)
                if index in by_index
            ]
            neighbors.sort(
                key=lambda row: (
                    type_suitability_score(str(row["cleaned_text"]), target_type),
                    str(row["paragraph_id"]),
                ),
                reverse=True,
            )
            for second in neighbors:
                second_id = str(second["paragraph_id"])
                distance = abs(int(second["paragraph_index"]) - int(first["paragraph_index"]))
                combined = len(str(first["cleaned_text"])) + len(str(second["cleaned_text"]))
                if second_id not in used and 1 <= distance <= 10 and combined <= 3000:
                    return [first, second]
        return None

    added: list[dict[str, Any]] = []
    for target_type, target in quotas.items():
        type_added = 0
        while type_added < target:
            candidates = sorted(
                source_order,
                key=lambda source: (
                    source not in preferred_source_set,
                    source_counts[source],
                    source,
                ),
            )
            made_progress = False
            for source in candidates:
                if source_counts[source] >= source_cap:
                    continue
                contexts = (
                    take_pair(source, target_type)
                    if target_type == "cross_concept"
                    else take_single(source, target_type)
                )
                if not contexts:
                    continue
                context_ids = {str(row["paragraph_id"]) for row in contexts}
                if context_ids & used:
                    continue
                row = _candidate_row(config, split, target_type, contexts)
                added.append(row)
                used.update(context_ids)
                source_counts[source] += 1
                type_added += 1
                made_progress = True
                if type_added >= target:
                    break
            if not made_progress:
                if allow_partial:
                    unallocated[target_type] = unallocated.get(target_type, 0) + target - type_added
                    break
                raise RuntimeError(f"Unable to allocate {target_type}: {type_added}/{target} for {split}")
    combined_rows = existing + added
    write_jsonl(queue_path, combined_rows)
    return {
        "split": split,
        "previous_rows": len(existing),
        "added_rows": len(added),
        "queue_rows": len(combined_rows),
        "requested_by_type": requested_quotas,
        "allocated_request_by_type": quotas,
        "unallocated_by_type": dict(sorted(unallocated.items())),
        "preferred_sources": sorted(preferred_source_set),
        "queue_by_type": dict(sorted(Counter(row["target_question_type"] for row in combined_rows).items())),
        "source_counts": dict(sorted(source_counts.items())),
        "queue_sha256": sha256_file(queue_path),
    }


def parse_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object found")
    payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Payload is not a JSON object")
    return payload


def substantive_overlap(answer: str, source: str) -> float:
    answer_tokens = {
        token for token in comparison_key(answer).split() if token not in CONTENT_STOPWORDS and len(token) > 2
    }
    source_tokens = {
        token for token in comparison_key(source).split() if token not in CONTENT_STOPWORDS and len(token) > 2
    }
    return len(answer_tokens & source_tokens) / len(answer_tokens) if answer_tokens else 0.0


def normalized_evidence_span(quote: str, source: str) -> tuple[int, int] | None:
    """Map a punctuation/case-normalized quote to a continuous source span."""

    quote_tokens = [match.group(0).replace("’", "'").casefold() for match in WORD_PATTERN.finditer(quote)]
    source_matches = list(WORD_PATTERN.finditer(source))
    source_tokens = [match.group(0).replace("’", "'").casefold() for match in source_matches]
    if not quote_tokens or len(quote_tokens) > len(source_tokens):
        return None
    width = len(quote_tokens)
    for index in range(len(source_tokens) - width + 1):
        if source_tokens[index : index + width] == quote_tokens:
            return source_matches[index].start(), source_matches[index + width - 1].end()
    return None


def _coerce_generation(row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if all(key in row for key in ("question", "answer", "evidence_quotes")):
        payload = row
    else:
        try:
            payload = parse_json_payload(str(row.get("raw_generation") or ""))
        except (ValueError, json.JSONDecodeError) as exc:
            return {}, str(exc)
    question = clean_human_text(payload.get("question"))
    answer = clean_human_text(payload.get("answer"))
    quotes_value = payload.get("evidence_quotes", payload.get("evidence_quote", []))
    if isinstance(quotes_value, str):
        quotes = [clean_human_text(quotes_value)] if quotes_value.strip() else []
    elif isinstance(quotes_value, list):
        quotes = [clean_human_text(value) for value in quotes_value if clean_human_text(value)]
    else:
        quotes = []
    return {
        "question": question,
        "answer": answer,
        "evidence_quotes": quotes,
        "generated_question_type": clean_human_text(payload.get("question_type")),
    }, None


def hard_filter_row(row: dict[str, Any], config: PipelineConfig) -> tuple[list[str], dict[str, Any]]:
    rules = config.raw["hard_filter"]
    parsed, parse_error = _coerce_generation(row)
    if parse_error:
        return ["generation_parse_error"], {"generation_parse_error": parse_error}
    question = parsed["question"]
    answer = parsed["answer"]
    quotes = parsed["evidence_quotes"]
    source = clean_human_text(row.get("source_text"))
    split = str(row.get("split"))
    category = str(row.get("challenge_category") or "")
    reasons: list[str] = []
    if not int(rules["min_question_chars"]) <= len(question) <= int(rules["max_question_chars"]):
        reasons.append("question_length")
    if not question.endswith("?"):
        reasons.append("question_not_interrogative")
    lowered_question = question.casefold()
    if any(pattern in lowered_question for pattern in META_QUESTION_PATTERNS):
        reasons.append("meta_question")
    allow_ambiguity = split == "challenge" and category == "ambiguous"
    if not allow_ambiguity and any(pattern in lowered_question for pattern in GENERIC_REFERENCES):
        reasons.append("generic_source_reference")
    if not allow_ambiguity and any(pattern in lowered_question for pattern in AMBIGUOUS_REFERENCES):
        reasons.append("ambiguous_question_reference")
    if not int(rules["min_answer_chars"]) <= len(answer) <= int(rules["max_answer_chars"]):
        reasons.append("answer_length")
    lowered_answer = answer.casefold()
    if any(pattern in lowered_answer for pattern in BAD_ANSWER_PATTERNS):
        reasons.append("placeholder_or_refusal")
    if any(pattern in lowered_answer for pattern in GENERIC_REFERENCES):
        reasons.append("generic_source_reference")
    if repeated_ngram(answer, 8, 3):
        reasons.append("repetitive_answer")

    negative_challenge_categories = {"unanswerable", "ambiguous"}
    expected_quotes = 2 if category == "cross_concept" else 1
    original_quotes = list(quotes)
    discarded_quotes: list[str] = []
    derived_evidence_quote = False
    if split == "challenge" and category in negative_challenge_categories and not quotes:
        # A negative example still needs auditable provenance. The quote marks
        # the closest available evidence whose insufficiency or ambiguity the
        # reference answer diagnoses; it is not treated as an answer by itself.
        candidates: list[str] = []
        for context in row.get("contexts") or []:
            context_text = str(context.get("text") or "")
            candidates.extend(
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", context_text)
                if int(rules["min_quote_chars"])
                <= len(sentence.strip())
                <= int(rules["max_quote_chars"])
            )
        if candidates:
            quotes = [
                max(
                    candidates,
                    key=lambda quote: (
                        substantive_overlap(f"{question} {answer}", quote),
                        len(quote),
                        quote,
                    ),
                )
            ]
            parsed["evidence_quotes"] = quotes
            derived_evidence_quote = True
    if expected_quotes == 1 and len(quotes) > 1:
        quotes = sorted(
            quotes,
            key=lambda quote: (substantive_overlap(answer, quote), len(quote), quote),
            reverse=True,
        )
        discarded_quotes = quotes[1:]
        quotes = quotes[:1]
        parsed["evidence_quotes"] = quotes
    if len(quotes) != expected_quotes:
        reasons.append("evidence_quote_count")
    evidence_spans: list[dict[str, Any]] = []
    available_contexts = list(row.get("contexts") or [])
    used_context_ids: set[str] = set()
    for quote in quotes:
        if not int(rules["min_quote_chars"]) <= len(quote) <= int(rules["max_quote_chars"]):
            reasons.append("evidence_quote_length")
            continue
        found: dict[str, Any] | None = None
        for context in available_contexts:
            context_id = str(context["paragraph_id"])
            if context_id in used_context_ids and expected_quotes > 1:
                continue
            span = normalized_evidence_span(quote, str(context["text"]))
            if span:
                found = {
                    "paragraph_id": context_id,
                    "source_file": context["source_file"],
                    "paragraph_index": context["paragraph_index"],
                    "start": span[0],
                    "end": span[1],
                    "source_quote": str(context["text"])[span[0] : span[1]],
                }
                used_context_ids.add(context_id)
                break
        if found is None:
            reasons.append("evidence_quote_not_contiguous")
        else:
            evidence_spans.append(found)

    overlap = substantive_overlap(answer, source)
    grounding_required = not (
        split == "challenge" and category in negative_challenge_categories
    )
    if grounding_required and overlap < float(rules["min_answer_source_overlap"]):
        reasons.append("low_answer_source_overlap")
    if (
        len(comparison_key(answer).split()) >= int(rules["long_copy_min_tokens"])
        and comparison_key(answer) in comparison_key(source)
    ):
        reasons.append("long_direct_copy")
    metrics = {
        "question_chars": len(question),
        "answer_chars": len(answer),
        "answer_source_overlap": round(overlap, 4),
        "quote_count": len(quotes),
        "original_quote_count": len(original_quotes),
        "discarded_extra_quotes": discarded_quotes,
        "derived_evidence_quote": derived_evidence_quote,
        "hard_quality_score": round(0.7 * overlap + 0.3 * float(row.get("evidence_quality_score", 0.0)), 4),
    }
    normalized = {
        **row,
        **parsed,
        "evidence_spans": evidence_spans,
        "hard_filter_metrics": metrics,
        "hard_filter_version": "pipeline_v2.hard_filter.3",
    }
    return list(dict.fromkeys(reasons)), normalized


def hard_filter_split(config: PipelineConfig, split: str) -> dict[str, Any]:
    input_path = config.interim("generations", split)
    if not input_path.is_file():
        raise FileNotFoundError(f"No generations for {split}: {input_path}")
    queue_ids = {str(row["candidate_id"]) for _, row in read_jsonl(config.interim("queues", split))}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for _, row in read_jsonl(input_path):
        reasons, normalized = hard_filter_row(row, config)
        if str(row.get("candidate_id")) not in queue_ids:
            reasons.append("not_in_current_queue")
        if str(row.get("split")) != split:
            reasons.append("wrong_split")
        if row.get("config_hash") != config.config_hash:
            reasons.append("config_hash_mismatch")
        if reasons:
            counts.update(reasons)
            rejected.append({**row, "rejection_stage": "hard_filter", "rejection_reasons": reasons})
        else:
            accepted.append(normalized)
    write_jsonl(config.interim("hard_filtered", split), accepted)
    write_jsonl(config.interim("rejections_hard", split), rejected)
    return {
        "split": split,
        "input_rows": len(accepted) + len(rejected),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_counts": dict(sorted(counts.items())),
    }


class NearDuplicateIndex:
    def __init__(self, max_distance: int, bands: int = 16):
        self.max_distance = max_distance
        self.bands = bands
        self.band_bits = 64 // bands
        self.mask = (1 << self.band_bits) - 1
        self.buckets: dict[tuple[int, int], set[int]] = defaultdict(set)
        self.members: dict[int, str] = {}

    def find(self, value: str) -> tuple[str, int] | None:
        value_hash = simhash64(value)
        candidates: set[int] = set()
        for band in range(self.bands):
            band_value = (value_hash >> (band * self.band_bits)) & self.mask
            candidates.update(self.buckets[(band, band_value)])
        matches = [
            (self.members[candidate], hamming_distance(value_hash, candidate))
            for candidate in candidates
            if hamming_distance(value_hash, candidate) <= self.max_distance
        ]
        return min(matches, key=lambda item: (item[1], item[0])) if matches else None

    def add(self, member_id: str, value: str) -> None:
        value_hash = simhash64(value)
        self.members.setdefault(value_hash, member_id)
        for band in range(self.bands):
            band_value = (value_hash >> (band * self.band_bits)) & self.mask
            self.buckets[(band, band_value)].add(value_hash)


def deduplicate_rows(
    rows: Iterable[dict[str, Any]],
    *,
    question_distance: int,
    answer_distance: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = sorted(
        rows,
        key=lambda row: (float(row.get("hard_filter_metrics", {}).get("hard_quality_score", 0)), row["candidate_id"]),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    exact_questions: dict[str, str] = {}
    exact_answers: dict[str, str] = {}
    exact_contexts: dict[str, str] = {}
    near_questions = NearDuplicateIndex(question_distance)
    near_answers = NearDuplicateIndex(answer_distance)
    for row in ranked:
        candidate_id = str(row["candidate_id"])
        question_key = fingerprint(row["question"])
        answer_key = fingerprint(row["answer"])
        context_key = fingerprint(" ".join(map(str, row.get("context_ids", []))))
        match: tuple[str, str, int | None] | None = None
        if question_key in exact_questions:
            match = (exact_questions[question_key], "exact_question", 0)
        elif answer_key in exact_answers:
            match = (exact_answers[answer_key], "exact_answer", 0)
        elif context_key in exact_contexts:
            match = (exact_contexts[context_key], "exact_context", 0)
        elif near := near_questions.find(str(row["question"])):
            match = (near[0], "near_question", near[1])
        elif near := near_answers.find(str(row["answer"])):
            match = (near[0], "near_answer", near[1])
        if match:
            representative, reason, distance = match
            rejected.append(
                {
                    **row,
                    "rejection_stage": "deduplication",
                    "rejection_reasons": [reason],
                    "duplicate_cluster_id": fingerprint(f"duplicate:{representative}"),
                    "duplicate_representative_id": representative,
                    "duplicate_distance": distance,
                    "duplicate_algorithm": "canonical_sha256+simhash64_v1",
                }
            )
            continue
        exact_questions[question_key] = candidate_id
        exact_answers[answer_key] = candidate_id
        exact_contexts[context_key] = candidate_id
        near_questions.add(candidate_id, str(row["question"]))
        near_answers.add(candidate_id, str(row["answer"]))
        kept.append({**row, "duplicate_cluster_id": fingerprint(f"singleton:{candidate_id}")})
    return kept, rejected


def deduplicate_splits(config: PipelineConfig) -> dict[str, Any]:
    rules = config.raw["hard_filter"]
    summary: dict[str, Any] = {}
    for split in SPLITS:
        input_path = config.interim("hard_filtered", split)
        if not input_path.is_file():
            continue
        rows = [row for _, row in read_jsonl(input_path)]
        kept, rejected = deduplicate_rows(
            rows,
            question_distance=int(rules["question_simhash_distance"]),
            answer_distance=int(rules["answer_simhash_distance"]),
        )
        write_jsonl(config.interim("deduplicated", split), kept)
        write_jsonl(config.interim("rejections_dedup", split), rejected)
        summary[split] = {
            "input_rows": len(rows),
            "kept_rows": len(kept),
            "duplicate_rows": len(rejected),
            "duplicate_reasons": dict(sorted(Counter(row["rejection_reasons"][0] for row in rejected).items())),
        }
    return summary


def score_consensus(row: dict[str, Any], rubric: dict[str, Any], adversarial: dict[str, Any]) -> float:
    weights = {
        "faithfulness": 0.30,
        "evidence_support": 0.25,
        "answerability": 0.20,
        "self_contained": 0.15,
    }
    total = 0.0
    for key, weight in weights.items():
        mean = (float(rubric["scores"][key]) + float(adversarial["scores"][key])) / 2
        total += weight * (mean / 5)
    lexical = float(row["hard_filter_metrics"]["answer_source_overlap"])
    return round(total + 0.10 * min(lexical, 1.0), 4)


def _valid_judge_payload(payload: dict[str, Any]) -> bool:
    booleans = (
        "answerable",
        "faithful",
        "evidence_supports_answer",
        "self_contained",
        "overclaim",
        "contradiction",
        "challenge_valid",
    )
    scores = ("answerability", "faithfulness", "evidence_support", "self_contained")
    return (
        all(isinstance(payload.get(key), bool) for key in booleans)
        and isinstance(payload.get("scores"), dict)
        and all(isinstance(payload["scores"].get(key), int) and 1 <= payload["scores"][key] <= 5 for key in scores)
        and str(payload.get("canonical_question_type")) in set(REGULAR_TYPES) | set(CHALLENGE_TYPES)
        and isinstance(payload.get("unsupported_claims"), list)
        and isinstance(payload.get("reason_codes"), list)
        and isinstance(payload.get("reason"), str)
    )


def consensus_rows(config: PipelineConfig, split: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_path = config.interim("deduplicated", split)
    rows = {str(row["candidate_id"]): row for _, row in read_jsonl(base_path)}
    passes: dict[str, dict[str, dict[str, Any]]] = {}
    for pass_name in ("rubric", "adversarial"):
        path = config.interim(f"judge_{pass_name}", split)
        passes[pass_name] = {str(row["candidate_id"]): row for _, row in read_jsonl(path)} if path.is_file() else {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    minimum = int(config.raw["judge"]["minimum_dimension_score"])
    for candidate_id, row in rows.items():
        rubric_record = passes["rubric"].get(candidate_id)
        adversarial_record = passes["adversarial"].get(candidate_id)
        reasons: list[str] = []
        if not rubric_record or not adversarial_record:
            reasons.append("missing_judge_pass")
            payloads: list[dict[str, Any]] = []
        else:
            payloads = [rubric_record.get("judge_result", {}), adversarial_record.get("judge_result", {})]
            if not all(_valid_judge_payload(payload) for payload in payloads):
                reasons.append("invalid_judge_payload")
        if not reasons:
            rubric, adversarial = payloads
            if rubric["canonical_question_type"] != adversarial["canonical_question_type"]:
                reasons.append("judge_type_disagreement")
            category = str(row.get("challenge_category") or "")
            allowed_types = set(CHALLENGE_TYPES if split == "challenge" else REGULAR_TYPES)
            if any(payload["canonical_question_type"] not in allowed_types for payload in payloads):
                reasons.append("judge_type_out_of_scope")
            if split == "challenge" and any(
                payload["canonical_question_type"] != category for payload in payloads
            ):
                reasons.append("judge_challenge_type_mismatch")
            if split != "challenge" or category == "cross_concept":
                for payload in payloads:
                    if not payload["answerable"]:
                        reasons.append("judge_not_answerable")
                    if not payload["faithful"]:
                        reasons.append("judge_not_faithful")
                    if not payload["evidence_supports_answer"]:
                        reasons.append("judge_evidence_not_supportive")
                    if not payload["self_contained"]:
                        reasons.append("judge_not_self_contained")
                    if not payload["challenge_valid"]:
                        reasons.append("judge_invalid_ordinary_qa")
            elif category == "unanswerable":
                if any(
                    payload["answerable"]
                    or not payload["challenge_valid"]
                    or not payload["faithful"]
                    or not payload["evidence_supports_answer"]
                    or not payload["self_contained"]
                    for payload in payloads
                ):
                    reasons.append("invalid_unanswerable_challenge")
            elif category == "ambiguous":
                if any(
                    payload["answerable"]
                    or payload["self_contained"]
                    or not payload["challenge_valid"]
                    or not payload["faithful"]
                    for payload in payloads
                ):
                    reasons.append("invalid_ambiguous_challenge")
            elif category == "concept_confusion" and any(
                not payload["challenge_valid"]
                or not payload["faithful"]
                or not payload["evidence_supports_answer"]
                or not payload["self_contained"]
                for payload in payloads
            ):
                reasons.append("invalid_concept_confusion_challenge")
            for payload in payloads:
                if payload["overclaim"]:
                    reasons.append("judge_overclaim")
                if payload["contradiction"]:
                    reasons.append("judge_contradiction")
                if (split != "challenge" or category == "cross_concept") and min(
                    map(int, payload["scores"].values())
                ) < minimum:
                    reasons.append("judge_score_below_threshold")
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            rejected.append({**row, "rejection_stage": "judge_consensus", "rejection_reasons": reasons})
            continue
        rubric = payloads[0]
        adversarial = payloads[1]
        accepted.append(
            {
                **row,
                "question_type": rubric["canonical_question_type"],
                "judge_results": {
                    "rubric": rubric,
                    "adversarial": adversarial,
                },
                "quality_score": score_consensus(row, rubric, adversarial),
                "review_status": "automated_consensus",
                "benchmark_grade": "silver" if split != "train" else None,
                "consensus_version": "pipeline_v2.consensus.2",
            }
        )
    return accepted, rejected


def build_consensus(config: PipelineConfig) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for split in SPLITS:
        path = config.interim("deduplicated", split)
        if not path.is_file():
            continue
        accepted, rejected = consensus_rows(config, split)
        write_jsonl(config.interim("consensus", split), accepted)
        write_jsonl(config.interim("rejections_judge", split), rejected)
        summary[split] = {
            "input_rows": len(accepted) + len(rejected),
            "accepted_rows": len(accepted),
            "rejected_rows": len(rejected),
            "accepted_types": dict(sorted(Counter(row["question_type"] for row in accepted).items())),
            "rejection_counts": dict(
                sorted(Counter(reason for row in rejected for reason in row["rejection_reasons"]).items())
            ),
        }
    return summary


def _global_deduplicate(config: PipelineConfig) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    rules = config.raw["hard_filter"]
    precedence = ("challenge", "test", "validation", "train")
    output: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    rejected: list[dict[str, Any]] = []
    question_index = NearDuplicateIndex(int(rules["question_simhash_distance"]))
    answer_index = NearDuplicateIndex(int(rules["answer_simhash_distance"]))
    exact_questions: dict[str, str] = {}
    exact_answers: dict[str, str] = {}
    representatives: dict[str, str] = {}
    for split in precedence:
        path = config.interim("consensus", split)
        if not path.is_file():
            continue
        rows = sorted(
            (row for _, row in read_jsonl(path)),
            key=lambda row: (float(row["quality_score"]), row["candidate_id"]),
            reverse=True,
        )
        for row in rows:
            question_key = fingerprint(row["question"])
            answer_key = fingerprint(row["answer"])
            match: tuple[str, str, int] | None = None
            if question_key in exact_questions:
                match = (exact_questions[question_key], "global_exact_question", 0)
            elif answer_key in exact_answers:
                match = (exact_answers[answer_key], "global_exact_answer", 0)
            elif near := question_index.find(str(row["question"])):
                match = (near[0], "global_near_question", near[1])
            elif near := answer_index.find(str(row["answer"])):
                match = (near[0], "global_near_answer", near[1])
            if match:
                representative, reason, distance = match
                rejected.append(
                    {
                        **row,
                        "rejection_stage": "global_deduplication",
                        "rejection_reasons": [reason],
                        "duplicate_representative_id": representative,
                        "duplicate_representative_split": representatives[representative],
                        "duplicate_cluster_id": fingerprint(f"global:{representative}"),
                        "duplicate_distance": distance,
                    }
                )
                continue
            candidate_id = str(row["candidate_id"])
            exact_questions[question_key] = candidate_id
            exact_answers[answer_key] = candidate_id
            representatives[candidate_id] = split
            question_index.add(candidate_id, str(row["question"]))
            answer_index.add(candidate_id, str(row["answer"]))
            output[split].append(row)
    return output, rejected


def _select_with_quotas(
    rows: list[dict[str, Any]],
    quotas: dict[str, int],
    source_cap: int,
    *,
    source_floor: int = 0,
    required_sources: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    selected_ids: set[str] = set()
    deficits: dict[str, int] = {}

    # Reserve the source floor first while respecting type quotas. This avoids
    # discovering after Top-N selection that a small holdout source vanished.
    if source_floor:
        source_buckets = {
            source: sorted(
                (row for row in rows if str(row["source_file"]) == source),
                key=lambda row: (float(row["quality_score"]), row["candidate_id"]),
                reverse=True,
            )
            for source in required_sources
        }
        while any(source_counts[source] < source_floor for source in required_sources):
            made_progress = False
            for source in sorted(required_sources, key=lambda item: (source_counts[item], item)):
                if source_counts[source] >= source_floor:
                    continue
                eligible = [
                    row
                    for row in source_buckets[source]
                    if row["candidate_id"] not in selected_ids
                    and type_counts[row["question_type"]] < quotas.get(row["question_type"], 0)
                ]
                if not eligible:
                    continue
                eligible.sort(
                    key=lambda row: (
                        quotas[row["question_type"]] - type_counts[row["question_type"]],
                        float(row["quality_score"]),
                        row["candidate_id"],
                    ),
                    reverse=True,
                )
                row = eligible[0]
                selected.append(row)
                selected_ids.add(str(row["candidate_id"]))
                source_counts[source] += 1
                type_counts[str(row["question_type"])] += 1
                made_progress = True
            if not made_progress:
                break

    for question_type, target in quotas.items():
        bucket = sorted(
            (
                row
                for row in rows
                if row["question_type"] == question_type and row["candidate_id"] not in selected_ids
            ),
            key=lambda row: (float(row["quality_score"]), row["candidate_id"]),
            reverse=True,
        )
        chosen = type_counts[question_type]
        while chosen < target and bucket:
            bucket.sort(
                key=lambda row: (
                    source_counts[str(row["source_file"])],
                    -float(row["quality_score"]),
                    row["candidate_id"],
                )
            )
            row = bucket.pop(0)
            source = str(row["source_file"])
            if source_counts[source] >= source_cap:
                continue
            selected.append(row)
            selected_ids.add(str(row["candidate_id"]))
            source_counts[source] += 1
            type_counts[question_type] += 1
            chosen += 1
        if chosen < target:
            deficits[question_type] = target - chosen
    return selected, deficits


def select_final_datasets(config: PipelineConfig, *, allow_incomplete: bool = False) -> dict[str, Any]:
    pools, global_duplicates = _global_deduplicate(config)
    write_jsonl(config.interim("rejections_global_dedup", "all"), global_duplicates)
    outputs = {
        "train": config.path("train_output"),
        "validation": config.path("benchmark_root") / "validation.jsonl",
        "test": config.path("benchmark_root") / "test.jsonl",
        "challenge": config.path("benchmark_root") / "challenge.jsonl",
    }
    result: dict[str, Any] = {}
    all_selected: dict[str, list[dict[str, Any]]] = {}
    for split in SPLITS:
        quotas = split_quotas(config, split, initial=False)
        manifest_sources = load_split_manifest(config)["splits"][split]
        floor = int(config.raw.get("final_source_floors", {}).get(split, 0))
        selected, deficits = _select_with_quotas(
            pools[split],
            quotas,
            int(config.raw["final_source_caps"][split]),
            source_floor=floor,
            required_sources=manifest_sources,
        )
        source_counts = Counter(str(row["source_file"]) for row in selected)
        floor_failed = sorted(
            source
            for source in manifest_sources
            if source_counts[source] < floor
        )
        if floor_failed:
            deficits["source_floor"] = len(floor_failed)
        final_rows = [
            {
                **row,
                "id": fingerprint(f"{config.raw['sft_version']}:{row['candidate_id']}"),
                "messages": [
                    {"role": "user", "content": row["question"]},
                    {"role": "assistant", "content": row["answer"]},
                ],
                "dataset_version": config.raw["sft_version"] if split == "train" else config.raw["benchmark_version"],
                "selected_at": utc_now(),
            }
            for row in selected
        ]
        if deficits and not allow_incomplete:
            raise RuntimeError(f"Selection deficits for {split}: {deficits}")
        write_jsonl(outputs[split], final_rows)
        all_selected[split] = final_rows
        result[split] = {
            "rows": len(final_rows),
            "target": sum(quotas.values()),
            "deficits": deficits,
            "types": dict(sorted(Counter(row["question_type"] for row in final_rows).items())),
            "sources": dict(sorted(source_counts.items())),
        }
    test_path = outputs["test"]
    test_seal = {
        "sealed": not result["test"]["deficits"],
        "benchmark_grade": "silver",
        "dataset_version": config.raw["benchmark_version"],
        "rows": result["test"]["rows"],
        "sha256": sha256_file(test_path),
        "sealed_at": utc_now(),
        "config_hash": config.config_hash,
    }
    write_json(config.path("test_manifest"), test_seal)
    result["global_duplicate_rows"] = len(global_duplicates)
    result["test_seal"] = test_seal
    return result


def audit_pipeline(config: PipelineConfig) -> dict[str, Any]:
    manifest = load_split_manifest(config)
    output_paths = {
        "train": config.path("train_output"),
        "validation": config.path("benchmark_root") / "validation.jsonl",
        "test": config.path("benchmark_root") / "test.jsonl",
        "challenge": config.path("benchmark_root") / "challenge.jsonl",
    }
    rows = {
        split: [row for _, row in read_jsonl(path)] if path.is_file() else []
        for split, path in output_paths.items()
    }
    expected = {split: sum(split_quotas(config, split, initial=False).values()) for split in SPLITS}
    sources = {split: {str(row["source_file"]) for row in values} for split, values in rows.items()}
    question_ids = [fingerprint(row["question"]) for values in rows.values() for row in values]
    answer_ids = [fingerprint(row["answer"]) for values in rows.values() for row in values]
    provenance_valid = all(
        row.get("contexts")
        and row.get("config_hash") == config.config_hash
        and all(span.get("end", 0) > span.get("start", 0) for span in row.get("evidence_spans", []))
        for values in rows.values()
        for row in values
    )
    quotas_met = all(
        Counter(row["question_type"] for row in rows[split]) == Counter(split_quotas(config, split, initial=False))
        for split in SPLITS
    )
    seal = (
        json.loads(config.path("test_manifest").read_text(encoding="utf-8"))
        if config.path("test_manifest").is_file()
        else {}
    )
    checks = {
        "target_counts": all(len(rows[split]) == expected[split] for split in SPLITS),
        "type_quotas": quotas_met,
        "train_validation_sources_disjoint": not (sources["train"] & sources["validation"]),
        "train_test_sources_disjoint": not (sources["train"] & sources["test"]),
        "validation_test_sources_disjoint": not (sources["validation"] & sources["test"]),
        "global_exact_questions_unique": len(question_ids) == len(set(question_ids)),
        "global_exact_answers_unique": len(answer_ids) == len(set(answer_ids)),
        "provenance_and_offsets": provenance_valid,
        "dual_judge_present": all(
            set(row.get("judge_results", {})) == {"rubric", "adversarial"}
            for values in rows.values()
            for row in values
        ),
        "silver_benchmark_labels": all(
            row.get("benchmark_grade") == "silver"
            for split in ("validation", "test", "challenge")
            for row in rows[split]
        ),
        "test_sealed": bool(seal.get("sealed")) and seal.get("sha256") == sha256_file(output_paths["test"]),
        "split_manifest_valid": all(manifest["checks"].values()),
    }
    audit = {
        "pipeline_version": config.pipeline_version,
        "created_at": utc_now(),
        "config_hash": config.config_hash,
        "benchmark_grade": "silver",
        "known_limitations": [
            "No human review was performed.",
            "Anonymous source files may originate from the same underlying work.",
            "Generator and judge are different sizes from the same Gemma model family.",
        ],
        "counts": {split: len(values) for split, values in rows.items()},
        "question_types": {
            split: dict(sorted(Counter(row["question_type"] for row in values).items()))
            for split, values in rows.items()
        },
        "source_counts": {split: len(values) for split, values in sources.items()},
        "artifact_sha256": {
            split: sha256_file(path) for split, path in output_paths.items() if path.is_file()
        },
        "checks": checks,
    }
    write_json(config.path("audit_file"), audit)
    if not all(checks.values()):
        raise RuntimeError(f"Pipeline v2 audit failed: {checks}")
    return audit


def file_row_count(path: Path) -> int:
    return sum(1 for _ in read_jsonl(path)) if path.is_file() else 0


def pipeline_status(config: PipelineConfig) -> dict[str, Any]:
    status: dict[str, Any] = {
        "pipeline_version": config.pipeline_version,
        "config_hash": config.config_hash,
        "clean_corpus_rows": file_row_count(config.path("clean_corpus")),
        "split_manifest_ready": config.path("split_manifest").is_file(),
        "splits": {},
    }
    for split in SPLITS:
        quotas = split_quotas(config, split, initial=False)
        final_path = (
            config.path("train_output")
            if split == "train"
            else config.path("benchmark_root") / f"{split}.jsonl"
        )
        status["splits"][split] = {
            "queue": file_row_count(config.interim("queues", split)),
            "generated": file_row_count(config.interim("generations", split)),
            "hard_filtered": file_row_count(config.interim("hard_filtered", split)),
            "deduplicated": file_row_count(config.interim("deduplicated", split)),
            "judge_rubric": file_row_count(config.interim("judge_rubric", split)),
            "judge_adversarial": file_row_count(config.interim("judge_adversarial", split)),
            "consensus": file_row_count(config.interim("consensus", split)),
            "final": file_row_count(final_path),
            "target": sum(quotas.values()),
        }
    return status
