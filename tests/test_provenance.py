import json
from pathlib import Path

from lacanllm.data.provenance import ParagraphIndex


def test_prefix_match_recovers_source(tmp_path: Path) -> None:
    paragraph = "The unconscious is structured like a language and appears through formations of speech."
    path = tmp_path / "paragraphs.jsonl"
    path.write_text(
        json.dumps({"text": paragraph, "source_file": "book.txt", "paragraph_index": 7}) + "\n",
        encoding="utf-8",
    )
    match = ParagraphIndex(path).match(paragraph + " A second sentence extends the source excerpt.")
    assert match is not None
    assert match.source_file == "book.txt"
    assert match.paragraph_index == 7
    assert 0.6 < match.score < 0.7


def test_match_expands_consecutive_paragraphs(tmp_path: Path) -> None:
    first = "The unconscious is structured like a language and appears through formations of speech."
    second = "Its effects can be read in slips, dreams, symptoms, and other formations."
    path = tmp_path / "paragraphs.jsonl"
    rows = [
        {"text": first, "source_file": "book.txt", "paragraph_index": 7},
        {"text": second, "source_file": "book.txt", "paragraph_index": 8},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    match = ParagraphIndex(path).match(first + " " + second)
    assert match is not None
    assert match.method == "consecutive_prefix"
    assert match.evidence == first + " " + second
    assert match.score == 1.0
