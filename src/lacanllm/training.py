"""Small, testable helpers for supervised fine-tuning."""

from __future__ import annotations

IGNORE_INDEX = -100


def common_prefix_length(left: list[int], right: list[int]) -> int:
    """Return the number of identical leading token ids."""

    length = 0
    for left_token, right_token in zip(left, right):
        if left_token != right_token:
            break
        length += 1
    return length


def assistant_only_labels(
    full_input_ids: list[int],
    prompt_input_ids: list[int],
) -> list[int]:
    """Mask the prompt so SFT loss is computed only on assistant tokens.

    Chat templates can append slightly different terminal tokens when an
    assistant answer is present.  Using their common prefix is more robust than
    assuming that the two encoded lengths always match exactly.
    """

    if not full_input_ids:
        raise ValueError("full_input_ids cannot be empty.")
    prompt_length = common_prefix_length(full_input_ids, prompt_input_ids)
    if prompt_length == 0:
        raise ValueError("Prompt tokens are not a prefix of the full conversation.")
    if prompt_length >= len(full_input_ids):
        raise ValueError("No assistant tokens remain after prompt masking.")
    return [IGNORE_INDEX] * prompt_length + full_input_ids[prompt_length:]

