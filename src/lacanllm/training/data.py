"""Dataset loading, rendering, masking, and deterministic screening samples."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_messages(rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows, 1):
        messages = row.get("messages")
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError(f"Row {index} must contain one user and one assistant message")
        if [message.get("role") for message in messages] != ["user", "assistant"]:
            raise ValueError(f"Row {index} has an invalid role sequence")
        if any(not str(message.get("content", "")).strip() for message in messages):
            raise ValueError(f"Row {index} contains an empty message")


def render_rows(rows: list[dict[str, Any]], processor: Any, *, thinking: bool = False) -> list[dict[str, Any]]:
    rendered = []
    for row in rows:
        text = processor.apply_chat_template(
            multimodal_messages(row["messages"]),
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=thinking,
        ).removeprefix("<bos>")
        rendered.append({"id": row["id"], "text": text})
    return rendered


def mask_before_response(input_ids: list[int], response_pattern: list[int], *, ignore_index: int = -100) -> list[int]:
    if not response_pattern:
        raise ValueError("response_pattern cannot be empty")
    start = next(
        (
            index + len(response_pattern)
            for index in range(len(input_ids) - len(response_pattern) + 1)
            if input_ids[index : index + len(response_pattern)] == response_pattern
        ),
        None,
    )
    if start is None or start >= len(input_ids):
        raise ValueError("Could not find a non-empty assistant response in the token sequence")
    return [ignore_index] * start + input_ids[start:]


def stratified_sample(rows: list[dict[str, Any]], per_type: int, *, seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["question_type"])].append(row)
    selected: list[dict[str, Any]] = []
    for question_type in sorted(grouped):
        candidates = sorted(grouped[question_type], key=lambda row: str(row["id"]))
        if len(candidates) < per_type:
            raise ValueError(f"Not enough {question_type} rows for a {per_type}-row screen")
        rng = random.Random(f"{seed}:{question_type}")
        selected.extend(rng.sample(candidates, per_type))
    return sorted(selected, key=lambda row: str(row["id"]))


def multimodal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt text-only messages to the Gemma 4 multimodal processor schema."""
    return [
        {
            "role": str(message["role"]),
            "content": (
                message["content"]
                if isinstance(message["content"], list)
                else [{"type": "text", "text": str(message["content"])}]
            ),
        }
        for message in messages
    ]


def question_prompt(row: dict[str, Any]) -> list[dict[str, Any]]:
    return multimodal_messages([{"role": "user", "content": str(row["question"])}])
