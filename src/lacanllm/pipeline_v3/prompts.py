from __future__ import annotations

import json
from typing import Any


def generation_prompt(candidate: dict[str, Any]) -> str:
    contexts = "\n\n".join(
        f"<context id={json.dumps(item['context_id'])}>\n{item['text']}\n</context>" for item in candidate["contexts"]
    )
    return f"""Create one source-grounded QA training record. Thinking is disabled. Use only the supplied context.
Return exactly one JSON object with exactly two keys: question and answer.
- question: standalone and understandable without seeing this instruction.
- answer: 2 to 5 complete sentences supported only by the context.
- when two contexts are supplied, use both only when they support a coherent answer.
- if the source is insufficient or ambiguous, the answer should state that limitation.
No evidence extraction, offsets, Markdown fences, commentary, or additional keys.

{contexts}"""


def judgment_prompt(record: dict[str, Any]) -> str:
    contexts = "\n\n".join(
        f"<context id={json.dumps(item['context_id'])}>\n{item['text']}\n</context>" for item in record["contexts"]
    )
    return f"""You are the single semantic quality judge for a source-grounded QA record. Thinking is disabled.
Judge only the supplied context, question, and answer.
Return exactly one JSON object with these keys and types:
- question_answerability: one of "answerable", "insufficient", "ambiguous"
- response_appropriate, faithful, context_supports_answer, self_contained, overclaim, contradiction: booleans
- answerability_score, faithfulness_score, context_support_score, self_containment_score: integer 1 through 5
- reason: one concise string
For an insufficient or ambiguous question, response_appropriate is true only
if the response appropriately states the limitation. No Markdown fences or
commentary.

{contexts}

Question: {record["question"]}
Answer: {record["answer"]}"""
