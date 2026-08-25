"""Blind paired judging and deterministic evaluation metrics."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from typing import Any

PAIR_WINNERS = {"A", "B", "tie"}


def judge_prompt(row: dict[str, Any], prediction_a: str, prediction_b: str) -> str:
    contexts = "\n\n".join(f"<context_{i}>{item['text']}</context_{i}>" for i, item in enumerate(row["contexts"], 1))
    return f"""You are a strict blind evaluator of two answers to a scholarly question about Jacques Lacan.

Use only the hidden contexts and reference answer as evidence. Do not reward shared wording by itself.
Compare A and B on correctness, faithfulness, coverage, correction of false premises or justified insufficiency,
contradictions,
and unsupported strengthening. A and B are anonymous and their order is randomized.

Return only strict JSON:
{{
  "winner": "A",
  "a_scores": {{"correctness": 1, "faithfulness": 1, "coverage": 1}},
  "b_scores": {{"correctness": 1, "faithfulness": 1, "coverage": 1}},
  "a_contradiction": false,
  "b_contradiction": false,
  "a_overclaim": false,
  "b_overclaim": false,
  "challenge_a_valid": true,
  "challenge_b_valid": true,
  "reason": "..."
}}

winner must be A, B, or tie. Scores must be integers 1-5.
reason must be one concise sentence of at most 40 words.

<question_type>{row['question_type']}</question_type>
<question>{row['question']}</question>
<reference>{row['answer']}</reference>
<answer_A>{prediction_a}</answer_A>
<answer_B>{prediction_b}</answer_B>
{contexts}
"""


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        raise ValueError("Judge output does not contain a JSON object")
    candidate = text[start : end + 1] if end > start else text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_truncated_final_reason(text[start:])
        if repaired is not None:
            return repaired
        raise


def _repair_truncated_final_reason(candidate: str) -> dict[str, Any] | None:
    """Recover a schema-complete judgment truncated inside its final rationale."""
    reason = re.search(r'"reason"\s*:\s*"', candidate)
    if reason is None:
        return None
    prefix = candidate[: reason.start()]
    repaired_text = prefix + '"reason": "[truncated at generation limit]"}'
    try:
        repaired = json.loads(repaired_text)
    except json.JSONDecodeError:
        return None
    repaired["_reason_truncated"] = True
    return repaired


def validate_judgment(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("winner") not in PAIR_WINNERS:
        raise ValueError("Judge winner must be A, B, or tie")
    for side in ("a", "b"):
        scores = value.get(f"{side}_scores")
        if not isinstance(scores, dict) or set(scores) != {"correctness", "faithfulness", "coverage"}:
            raise ValueError(f"Judge {side}_scores has an invalid shape")
        if any(type(score) is not int or not 1 <= score <= 5 for score in scores.values()):
            raise ValueError("Judge scores must be integers from one to five")
        for suffix in ("contradiction", "overclaim"):
            if type(value.get(f"{side}_{suffix}")) is not bool:
                raise ValueError(f"Judge {side}_{suffix} must be boolean")
        if type(value.get(f"challenge_{side}_valid")) is not bool:
            raise ValueError(f"Judge challenge_{side}_valid must be boolean")
    return value


def normalize_score_floor(value: dict[str, Any]) -> dict[str, Any]:
    """Map a judge's integer zero to the documented 1-5 scale, with provenance."""
    normalized = {**value}
    changed = False
    for side in ("a", "b"):
        key = f"{side}_scores"
        scores = value.get(key)
        if not isinstance(scores, dict):
            continue
        normalized[key] = dict(scores)
        for metric, score in scores.items():
            if type(score) is int and score == 0:
                normalized[key][metric] = 1
                changed = True
    if changed:
        normalized["_score_floor_normalized"] = True
    return normalized


def normalize_swapped(value: dict[str, Any]) -> dict[str, Any]:
    winner = value["winner"]
    normalized = {**value, "winner": {"A": "B", "B": "A", "tie": "tie"}[winner]}
    for suffix in ("scores", "contradiction", "overclaim"):
        normalized[f"a_{suffix}"], normalized[f"b_{suffix}"] = value[f"b_{suffix}"], value[f"a_{suffix}"]
    normalized["challenge_a_valid"], normalized["challenge_b_valid"] = (
        value["challenge_b_valid"],
        value["challenge_a_valid"],
    )
    return normalized


def aggregate_pairs(records: list[dict[str, Any]]) -> dict[str, Any]:
    consensus = [
        record
        for record in records
        if record.get("first", {}).get("winner") == record.get("second", {}).get("winner")
    ]
    counts = Counter(record["first"]["winner"] for record in consensus)
    score = (counts["A"] + 0.5 * counts["tie"]) / len(consensus) if consensus else 0.0
    contradictions = sum(int(record["first"]["a_contradiction"]) for record in consensus)
    overclaims = sum(int(record["first"]["a_overclaim"]) for record in consensus)
    challenge_types = {"unanswerable", "concept_confusion", "cross_concept"}
    challenges = [record for record in consensus if record.get("question_type") in challenge_types]
    return {
        "cases": len(records),
        "consensus_cases": len(consensus),
        "consensus_rate": len(consensus) / len(records) if records else 0.0,
        "wins": counts["A"],
        "ties": counts["tie"],
        "losses": counts["B"],
        "paired_score": score,
        "candidate_contradiction_rate": contradictions / len(consensus) if consensus else 1.0,
        "candidate_overclaim_rate": overclaims / len(consensus) if consensus else 1.0,
        "challenge_candidate_accuracy": (
            sum(int(record["first"]["challenge_a_valid"]) for record in challenges) / len(challenges)
            if challenges
            else None
        ),
        "challenge_baseline_accuracy": (
            sum(int(record["first"]["challenge_b_valid"]) for record in challenges) / len(challenges)
            if challenges
            else None
        ),
        "by_type": _by_type(consensus),
    }


def _by_type(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["question_type"])].append(record)
    result = {}
    for question_type, rows in sorted(grouped.items()):
        wins = sum(record["first"]["winner"] == "A" for record in rows)
        ties = sum(record["first"]["winner"] == "tie" for record in rows)
        result[question_type] = {"cases": len(rows), "paired_score": (wins + 0.5 * ties) / len(rows)}
    return result


def bootstrap_interval(records: list[dict[str, Any]], *, samples: int, seed: int) -> tuple[float, float]:
    values = [
        1.0 if record["first"]["winner"] == "A" else 0.5 if record["first"]["winner"] == "tie" else 0.0
        for record in records
        if record.get("first", {}).get("winner") == record.get("second", {}).get("winner")
    ]
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples))
    return means[int(samples * 0.025)], means[min(samples - 1, int(samples * 0.975))]
