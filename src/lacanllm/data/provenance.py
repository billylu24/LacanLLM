"""Recover QA provenance by matching answers to cleaned source paragraphs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lacanllm.data.io import Row, read_jsonl
from lacanllm.data.text import (
    common_prefix_ratio,
    comparison_key,
    left_coverage_ratio,
    prefix_signature,
    word_tokens,
)


@dataclass(frozen=True)
class ProvenanceMatch:
    source_file: str
    paragraph_index: int
    evidence: str
    score: float
    method: str


class ParagraphIndex:
    """Memory-resident index for deterministic exact and prefix matching."""

    def __init__(self, paragraphs_file: Path, signature_tokens: int = 12):
        self.signature_tokens = signature_tokens
        self.exact: dict[str, list[Row]] = {}
        self.prefix: dict[str, list[Row]] = {}
        self.by_location: dict[tuple[str, int], Row] = {}
        self.paragraph_count = 0
        for _, row in read_jsonl(paragraphs_file):
            text = str(row.get("text", "")).strip()
            source_file = row.get("source_file")
            paragraph_index = row.get("paragraph_index")
            if not text or not source_file or paragraph_index is None:
                continue
            normalized = comparison_key(text)
            if not normalized:
                continue
            indexed = {
                "text": text,
                "source_file": str(source_file),
                "paragraph_index": int(paragraph_index),
            }
            self.exact.setdefault(normalized, []).append(indexed)
            signature = prefix_signature(text, signature_tokens)
            self.prefix.setdefault(signature, []).append(indexed)
            self.by_location[(indexed["source_file"], indexed["paragraph_index"])] = indexed
            self.paragraph_count += 1

    def match(self, answer: str) -> ProvenanceMatch | None:
        normalized = comparison_key(answer)
        exact_matches = self.exact.get(normalized, [])
        if len(exact_matches) == 1:
            row = exact_matches[0]
            return self._result(row, 1.0, "exact")

        signature = prefix_signature(answer, self.signature_tokens)
        candidates = self.prefix.get(signature, [])
        if not candidates:
            return None

        scored: list[tuple[float, int, Row]] = []
        answer_token_count = len(word_tokens(answer))
        for row in candidates:
            score = common_prefix_ratio(answer, row["text"])
            evidence_token_count = len(word_tokens(row["text"]))
            length_similarity = min(answer_token_count, evidence_token_count) / max(
                answer_token_count, evidence_token_count
            )
            scored.append((score, int(length_similarity * 10_000), row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, _, best = scored[0]
        evidence = self._expand_evidence(best, answer)
        coverage = left_coverage_ratio(answer, evidence)
        method = "consecutive_prefix" if evidence != best["text"] else "normalized_prefix"
        return ProvenanceMatch(
            source_file=str(best["source_file"]),
            paragraph_index=int(best["paragraph_index"]),
            evidence=evidence,
            score=round(coverage, 4),
            method=method,
        )

    def _expand_evidence(self, first: Row, answer: str, max_paragraphs: int = 4) -> str:
        """Join consecutive paragraphs when legacy QA spans paragraph boundaries."""

        source_file = str(first["source_file"])
        first_index = int(first["paragraph_index"])
        paragraphs = [str(first["text"])]
        best_evidence = paragraphs[0]
        best_coverage = left_coverage_ratio(answer, best_evidence)
        for offset in range(1, max_paragraphs):
            following = self.by_location.get((source_file, first_index + offset))
            if following is None:
                break
            paragraphs.append(str(following["text"]))
            candidate = " ".join(paragraphs)
            coverage = left_coverage_ratio(answer, candidate)
            if coverage >= best_coverage:
                best_evidence = candidate
                best_coverage = coverage
            if coverage == 1.0:
                break
        return best_evidence

    @staticmethod
    def _result(row: Row, score: float, method: str) -> ProvenanceMatch:
        return ProvenanceMatch(
            source_file=str(row["source_file"]),
            paragraph_index=int(row["paragraph_index"]),
            evidence=str(row["text"]),
            score=round(score, 4),
            method=method,
        )
