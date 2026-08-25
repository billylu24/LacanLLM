from __future__ import annotations

import json
from typing import Any


def generation_prompt(candidate: dict[str, Any], repair_error: str | None = None) -> str:
    contexts = "\n\n".join(
        f"<context id={json.dumps(item['context_id'])}>\n{item['text']}\n</context>" for item in candidate["contexts"]
    )
    repair = ""
    if repair_error:
        repair = f"\nYour prior response was invalid: {repair_error}. Return a corrected JSON object only."
    return f"""You create one source-grounded QA training record. Thinking is disabled. Use only the supplied context.
Return exactly one JSON object with keys: question, reference_answer, evidence, question_type.
- question: standalone and understandable without seeing this instruction.
- reference_answer: 2 to 5 complete sentences. Do not add external facts.
- evidence: a non-empty array of objects with context_id and quote.
  Every quote must be copied character-for-character as one continuous span
  from its named context.
- copy context_id exactly from the supplied short ID (`context_1` or
  `context_2`); never invent, shorten, or replace it.
- when two contexts are supplied, synthesize them and include at least one
  evidence item from each context.
- if the source is insufficient or ambiguous, the response should say so and
  quote the source wording that demonstrates the insufficiency.
- question_type is optional diagnostic metadata and never controls acceptance.
No Markdown fences, commentary, or keys beyond those listed.

{contexts}{repair}"""


def judgment_prompt(record: dict[str, Any], repair_error: str | None = None) -> str:
    contexts = "\n\n".join(
        f"<context id={json.dumps(item['context_id'])}>\n{item['text']}\n</context>" for item in record["contexts"]
    )
    repair = ""
    if repair_error:
        repair = f"\nYour prior response was invalid: {repair_error}. Return a corrected JSON object only."
    return f"""You are the single semantic quality judge for a source-grounded QA record. Thinking is disabled.
Judge only the supplied source, question, response, and evidence. Ignore question_type completely.
Return exactly one JSON object with these keys and types:
- question_answerability: one of "answerable", "insufficient", "ambiguous"
- response_appropriate, faithful, evidence_supports_response, self_contained, overclaim, contradiction: booleans
- answerability_score, faithfulness_score, evidence_score, self_containment_score: integer 1 through 5
- reason: one concise string
For an insufficient or ambiguous question, response_appropriate is true only
if the response appropriately states the limitation. No Markdown fences or
commentary.

{contexts}

Question: {record["question"]}
Reference response: {record["reference_answer"]}
Evidence: {json.dumps(record["evidence"], ensure_ascii=False)}{repair}"""
