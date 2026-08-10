from lacanllm.evaluation import reference_overlap


def test_reference_overlap_reports_precision_recall_and_f1() -> None:
    metrics = reference_overlap("desire structures the subject", "the subject has desire")

    assert metrics == {
        "lexical_precision": 0.75,
        "lexical_recall": 0.75,
        "lexical_f1": 0.75,
    }

