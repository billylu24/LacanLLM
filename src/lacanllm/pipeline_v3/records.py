from __future__ import annotations

import re
from typing import Any

JUDGE_BOOLEAN_FIELDS = (
    "response_appropriate",
    "faithful",
    "evidence_supports_response",
    "self_contained",
    "overclaim",
    "contradiction",
)
JUDGE_SCORE_FIELDS = ("answerability_score", "faithfulness_score", "evidence_score", "self_containment_score")


def validate_generation(payload: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("generation must be a JSON object")
    question = payload.get("question")
    answer = payload.get("reference_answer")
    evidence = payload.get("evidence")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be non-empty")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("reference_answer must be non-empty")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence must be a non-empty list")
    context_by_id = {context["context_id"]: context for context in candidate["contexts"]}
    normalized: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict) or set(("context_id", "quote")) - item.keys():
            raise ValueError("each evidence item requires context_id and quote")
        context_id, quote = item["context_id"], item["quote"]
        if context_id not in context_by_id or not isinstance(quote, str) or not quote:
            raise ValueError("evidence references an unknown context or empty quote")
        text = context_by_id[context_id]["text"]
        start = text.find(quote)
        if start < 0:
            raise ValueError("evidence quote is not a continuous exact source span")
        normalized.append({"context_id": context_id, "quote": quote, "start": start, "end": start + len(quote)})
    return {
        "question": question.strip(),
        "reference_answer": answer.strip(),
        "evidence": normalized,
        "question_type": payload.get("question_type") if isinstance(payload.get("question_type"), str) else None,
    }


def validate_judgment(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("judgment must be a JSON object")
    answerability = payload.get("question_answerability")
    if answerability not in {"answerable", "insufficient", "ambiguous"}:
        raise ValueError("invalid question_answerability")
    for field in JUDGE_BOOLEAN_FIELDS:
        if type(payload.get(field)) is not bool:
            raise ValueError(f"{field} must be boolean")
    for field in JUDGE_SCORE_FIELDS:
        if type(payload.get(field)) is not int or not 1 <= payload[field] <= 5:
            raise ValueError(f"{field} must be an integer from 1 to 5")
    if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
        raise ValueError("reason must be a non-empty string")
    return {
        key: payload[key] for key in ("question_answerability", *JUDGE_BOOLEAN_FIELDS, *JUDGE_SCORE_FIELDS, "reason")
    }


def sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])(?:[\"'’”)]*)\s+", text.strip()) if part.strip()])


def hard_filter(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    question, answer = record["question"], record["reference_answer"]
    if not 8 <= len(question) <= 500 or not question.endswith("?"):
        reasons.append("question_length_or_form")
    if not 2 <= sentence_count(answer) <= 5:
        reasons.append("answer_sentence_count")
    contexts = {item["context_id"]: item for item in record["contexts"]}
    covered: set[str] = set()
    for item in record["evidence"]:
        context = contexts.get(item["context_id"])
        if context is None:
            reasons.append("unknown_context")
            continue
        quote, start, end = item["quote"], item["start"], item["end"]
        if context["text"][start:end] != quote:
            reasons.append("evidence_offset_mismatch")
        covered.add(item["context_id"])
    if len(contexts) == 2 and covered != set(contexts):
        reasons.append("paired_context_missing_evidence")
    if "```" in question or "```" in answer:
        reasons.append("meta_or_fenced_output")
    return sorted(set(reasons))


def judgment_passes(judgment: dict[str, Any]) -> bool:
    return (
        judgment["response_appropriate"]
        and judgment["faithful"]
        and judgment["evidence_supports_response"]
        and judgment["self_contained"]
        and not judgment["overclaim"]
        and not judgment["contradiction"]
        and min(judgment[field] for field in JUDGE_SCORE_FIELDS) >= 4
    )
