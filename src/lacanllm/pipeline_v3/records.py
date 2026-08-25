from __future__ import annotations

import re
from typing import Any

JUDGE_BOOLEAN_FIELDS = (
    "response_appropriate",
    "faithful",
    "context_supports_answer",
    "self_contained",
    "overclaim",
    "contradiction",
)
JUDGE_SCORE_FIELDS = ("answerability_score", "faithfulness_score", "context_support_score", "self_containment_score")


def validate_generation(payload: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("generation must be a JSON object")
    if set(payload) != {"question", "answer"}:
        raise ValueError("generation must contain exactly question and answer")
    question = payload.get("question")
    answer = payload.get("answer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be non-empty")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("answer must be non-empty")
    return {
        "question": question.strip(),
        "answer": answer.strip(),
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
    question, answer = record["question"], record["answer"]
    if not 8 <= len(question) <= 500 or not question.endswith("?"):
        reasons.append("question_length_or_form")
    if not 2 <= sentence_count(answer) <= 5:
        reasons.append("answer_sentence_count")
    if "```" in question or "```" in answer:
        reasons.append("meta_or_fenced_output")
    return sorted(set(reasons))


def judgment_passes(judgment: dict[str, Any]) -> bool:
    return (
        judgment["response_appropriate"]
        and judgment["faithful"]
        and judgment["context_supports_answer"]
        and judgment["self_contained"]
        and not judgment["overclaim"]
        and not judgment["contradiction"]
        and min(judgment[field] for field in JUDGE_SCORE_FIELDS) >= 4
    )
