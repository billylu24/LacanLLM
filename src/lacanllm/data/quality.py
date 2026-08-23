"""Transparent quality gates for legacy synthetic QA candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lacanllm.data.text import clean_human_text, word_tokens

BOILERPLATE = (
    "all rights reserved",
    "library of congress",
    "isbn",
    "table of contents",
    "printed in",
    "www.",
)
META_QUESTION_PATTERNS = (
    "what specific aspects",
    "are you interested in exploring",
    "could you clarify",
    "could you please specify",
    "what would you like to know",
)
BAD_ANSWER_PATTERNS = (
    "as an ai",
    "i cannot answer",
    "the provided passage does not",
    "context is missing",
)
MOJIBAKE_MARKERS = ("�", "鈥", "禄", "漏", "脡", "锛", "绔", "骃")


@dataclass(frozen=True)
class QualityResult:
    question: str
    answer: str
    question_type: str
    score: float
    rejection_reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons


def classify_question(question: str) -> str:
    lowered = question.casefold()
    if any(term in lowered for term in ("difference", "distinction", "compare", "differ", "relationship between")):
        return "comparison"
    if any(term in lowered for term in ("clinical", "patient", "analyst", "analysis", "psychosis", "neurosis")):
        return "clinical"
    if lowered.startswith(("what is ", "what are ", "how does lacan define", "what does lacan mean")):
        return "definition"
    if any(term in lowered for term in ("why", "how does", "in what way", "role", "function")):
        return "explanation"
    if any(term in lowered for term in ("passage", "text", "seminar", "écrits", "ecrits")):
        return "textual_interpretation"
    return "other"


def repeated_ngram(text: str, n: int = 8, repetitions: int = 3) -> bool:
    tokens = word_tokens(text)
    if len(tokens) < n * repetitions:
        return False
    counts: dict[tuple[str, ...], int] = {}
    for index in range(len(tokens) - n + 1):
        ngram = tuple(tokens[index : index + n])
        counts[ngram] = counts.get(ngram, 0) + 1
        if counts[ngram] >= repetitions:
            return True
    return False


def assess_quality(
    question_value: object,
    answer_value: object,
    *,
    min_question_chars: int,
    max_question_chars: int,
    min_answer_chars: int,
    max_answer_chars: int,
) -> QualityResult:
    question = clean_human_text(question_value)
    answer = clean_human_text(answer_value)
    lowered_question = question.casefold()
    lowered_answer = answer.casefold()
    reasons: list[str] = []

    if not min_question_chars <= len(question) <= max_question_chars:
        reasons.append("question_length")
    if not question.endswith("?"):
        reasons.append("question_not_interrogative")
    if any(pattern in lowered_question for pattern in META_QUESTION_PATTERNS):
        reasons.append("meta_question")
    if not min_answer_chars <= len(answer) <= max_answer_chars:
        reasons.append("answer_length")
    if any(pattern in lowered_answer for pattern in BAD_ANSWER_PATTERNS):
        reasons.append("placeholder_answer")
    if any(term in lowered_answer for term in BOILERPLATE):
        reasons.append("boilerplate")
    if any(marker in answer for marker in MOJIBAKE_MARKERS):
        reasons.append("mojibake")
    if repeated_ngram(answer):
        reasons.append("repetitive_answer")

    alphabetical = len(re.findall(r"[A-Za-z]", answer))
    if answer and alphabetical / len(answer) < 0.55:
        reasons.append("low_alphabetic_ratio")

    score = 1.0
    score -= min(abs(len(answer) - 650) / 5000, 0.15)
    score -= min(abs(len(question) - 110) / 2000, 0.08)
    score -= 0.08 * len(reasons)
    return QualityResult(
        question=question,
        answer=answer,
        question_type=classify_question(question),
        score=round(max(score, 0.0), 4),
        rejection_reasons=tuple(dict.fromkeys(reasons)),
    )
