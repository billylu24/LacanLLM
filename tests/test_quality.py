from lacanllm.data.quality import assess_quality, repeated_ngram


def assess(question: str, answer: str):
    return assess_quality(
        question,
        answer,
        min_question_chars=20,
        max_question_chars=280,
        min_answer_chars=80,
        max_answer_chars=1800,
    )


def test_rejects_meta_question() -> None:
    result = assess(
        "What specific aspects of Lacan are you interested in exploring?",
        "Lacan develops an account of the subject through language and the symbolic order. " * 2,
    )
    assert "meta_question" in result.rejection_reasons


def test_detects_repeated_ngrams() -> None:
    phrase = "the subject enters language through the field of the other"
    assert repeated_ngram(" ".join([phrase] * 3))


def test_accepts_well_formed_candidate() -> None:
    result = assess(
        "How does Lacan relate desire to the symbolic order?",
        "Lacan treats desire as structured through language rather than as a direct expression of biological need. "
        "Its articulation therefore depends on the symbolic relations in which the subject is situated.",
    )
    assert result.accepted
