import json
from pathlib import Path

from lacanllm.data.sft import SFTConfig, SimHashIndex, content_overlap, parse_generated_json, validate_generation


def config(tmp_path: Path) -> SFTConfig:
    return SFTConfig(
        dataset_version="test",
        paragraphs_file=tmp_path / "paragraphs.jsonl",
        evaluation_audit_file=tmp_path / "eval.json",
        queue_file=tmp_path / "queue.jsonl",
        generations_file=tmp_path / "generations.jsonl",
        train_file=tmp_path / "train.jsonl",
        audit_file=tmp_path / "audit.json",
        model_id="test/model",
        seed=3407,
        queue_size=1,
        target_train_size=1,
        human_review_sample_size=1,
        max_paragraphs_per_source=10,
        min_evidence_chars=80,
        max_evidence_chars=1800,
        min_question_chars=20,
        max_question_chars=280,
        min_answer_chars=80,
        max_answer_chars=900,
        min_evidence_quote_chars=20,
        max_evidence_quote_chars=400,
        min_answer_evidence_overlap=0.25,
        max_new_tokens=100,
        generation_batch_size=2,
        temperature=0.2,
        top_p=0.9,
        load_in_4bit=True,
    )


def test_parse_generated_json_ignores_surrounding_text() -> None:
    payload = {
        "question": "How does language structure the unconscious?",
        "answer": "The unconscious appears through formations governed by relations among signifiers.",
        "evidence_quote": "the unconscious is structured like a language",
        "question_type": "explanation",
    }
    parsed = parse_generated_json("result:\n```json\n" + json.dumps(payload) + "\n```")
    assert parsed == payload


def test_parse_generated_json_infers_missing_question_type() -> None:
    payload = {
        "question": "How does language structure the unconscious?",
        "answer": "The unconscious appears through formations governed by relations among signifiers.",
        "evidence_quote": "the unconscious is structured like a language",
    }
    parsed = parse_generated_json(json.dumps(payload))
    assert parsed["question_type"] == "explanation"


def test_parse_generated_tags() -> None:
    raw = """<question>What is the function of the signifier?</question>
<answer>The signifier represents the subject for another signifier.</answer>
<evidence_quote>a signifier represents the subject for another signifier</evidence_quote>
<question_type>definition</question_type>"""
    parsed = parse_generated_json(raw)
    assert parsed["question"] == "What is the function of the signifier?"
    assert parsed["question_type"] == "definition"


def test_content_overlap_ignores_common_function_words() -> None:
    overlap = content_overlap(
        "Desire is articulated through signifiers in the symbolic order.",
        "The symbolic order articulates the subject's desire by means of signifiers.",
    )
    assert overlap > 0.5


def test_validate_generation_requires_exact_quote(tmp_path: Path) -> None:
    row = {
        "generation_status": "parsed",
        "question": "How does Lacan describe the relation between desire and language?",
        "answer": (
            "Desire is not treated as a purely biological need. It is articulated through signifiers and therefore "
            "depends on the symbolic relations in which the subject speaks."
        ),
        "evidence": (
            "Lacan distinguishes desire from biological need and argues that desire is articulated through signifiers "
            "within the symbolic relations of speech."
        ),
        "evidence_quote": "This sentence does not occur in the evidence at all.",
    }
    reasons, _ = validate_generation(row, config(tmp_path))
    assert "evidence_quote_not_exact" in reasons


def test_validate_generation_rejects_generic_source_reference(tmp_path: Path) -> None:
    evidence = (
        "Lacan distinguishes desire from biological need and argues that desire is articulated through signifiers "
        "within the symbolic relations of speech."
    )
    row = {
        "generation_status": "parsed",
        "question": "According to the text, how does Lacan distinguish desire from need?",
        "answer": (
            "The text suggests that desire is articulated through signifiers rather than reducible to biological "
            "need, and that it depends on symbolic relations."
        ),
        "evidence": evidence,
        "evidence_quote": "desire is articulated through signifiers within the symbolic relations of speech",
    }
    reasons, _ = validate_generation(row, config(tmp_path))
    assert "generic_source_reference" in reasons


def test_validate_generation_rejects_ambiguous_question_reference(tmp_path: Path) -> None:
    evidence = (
        "The master signifier organizes knowledge in the university discourse and can hold the speaking subject in "
        "a fixed position."
    )
    row = {
        "generation_status": "parsed",
        "question": "What concept is the author suggesting should be held onto?",
        "answer": (
            "The master signifier organizes knowledge in university discourse and can hold the speaking subject in "
            "a fixed position."
        ),
        "evidence": evidence,
        "evidence_quote": "The master signifier organizes knowledge in the university discourse",
    }
    reasons, _ = validate_generation(row, config(tmp_path))
    assert "ambiguous_question_reference" in reasons


def test_simhash_index_detects_near_duplicate() -> None:
    index = SimHashIndex(max_distance=14)
    index.add("How does Lacan distinguish desire from biological need?")
    assert index.is_near_duplicate("How does Lacan distinguish desire from bodily need?")
    assert not index.is_near_duplicate("What role does the mirror stage play in ego formation?")
