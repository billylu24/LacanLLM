from lacanllm.data.text import (
    clean_human_text,
    common_prefix_ratio,
    fingerprint,
    hamming_distance,
    left_coverage_ratio,
    simhash64,
)


def test_clean_human_text_normalizes_whitespace() -> None:
    assert clean_human_text("  desire\n\n and\t language ") == "desire and language"


def test_fingerprint_is_case_and_punctuation_insensitive() -> None:
    assert fingerprint("The Other—speaks.") == fingerprint("the other speaks")


def test_common_prefix_ratio_accepts_extended_answer() -> None:
    paragraph = "The symbolic order structures the subject through language."
    answer = paragraph + " This has consequences for desire."
    assert common_prefix_ratio(paragraph, answer) == 1.0


def test_left_coverage_requires_full_left_text() -> None:
    assert left_coverage_ratio("one two three four", "one two") == 0.5


def test_simhash_places_small_rewording_closer_than_unrelated_text() -> None:
    original = simhash64("How does Lacan distinguish desire from biological need?")
    rewording = simhash64("How does Lacan distinguish desire from bodily need?")
    unrelated = simhash64("What role does the mirror stage play in ego formation?")
    assert hamming_distance(original, rewording) < hamming_distance(original, unrelated)
