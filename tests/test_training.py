import pytest

from lacanllm.training import IGNORE_INDEX, assistant_only_labels, common_prefix_length


def test_common_prefix_length() -> None:
    assert common_prefix_length([1, 2, 3], [1, 2, 9]) == 2


def test_assistant_only_labels_mask_prompt() -> None:
    labels = assistant_only_labels([10, 20, 30, 40, 50], [10, 20, 30])

    assert labels == [IGNORE_INDEX, IGNORE_INDEX, IGNORE_INDEX, 40, 50]


def test_assistant_only_labels_requires_answer_tokens() -> None:
    with pytest.raises(ValueError, match="No assistant tokens"):
        assistant_only_labels([1, 2], [1, 2])

