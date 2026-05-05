import argparse
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "lacan_source_texts"
DEFAULT_CORPUS_FILE = PROJECT_ROOT / "data" / "lacan_full_corpus.txt"
DEFAULT_DATASET_FILE = PROJECT_ROOT / "data" / "lacan_dataset.jsonl"

MIN_PARAGRAPH_CHARS = 120
MAX_PARAGRAPH_CHARS = 6000

MOJIBAKE_MARKERS = ("鈥", "鈩", "漏", "禄", "脡", "锛", "绋", "馃")


def normalize_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"[ ]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def reconstruct_paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", text)
    paragraphs: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        merged = " ".join(lines)
        merged = re.sub(r"\s+", " ", merged).strip()
        if is_useful_paragraph(merged):
            paragraphs.append(merged)

    return paragraphs


def is_useful_paragraph(text: str) -> bool:
    if len(text) < MIN_PARAGRAPH_CHARS or len(text) > MAX_PARAGRAPH_CHARS:
        return False
    if text.count(" ") < 10:
        return False
    if re.fullmatch(r"[\W\d_]+", text):
        return False
    lowered = text.lower()
    boilerplate = (
        "contents",
        "isbn",
        "all rights reserved",
        "library of congress",
        "british library cataloguing",
        "printed in",
        "translation of:",
        "translated by",
        "published by",
        "copyright",
        "www.",
    )
    if any(term in lowered for term in boilerplate):
        return False
    if re.search(r"\b(london|new york|paris):\s+[a-z]", lowered):
        return False
    if re.search(r"\b\d{4}[;,.]\s+[A-Z][A-Za-z]+", text):
        return False
    if re.match(r"^\d+\s*\[", text):
        return False
    if "|" in text and len(text.split()) < 35:
        return False
    return True


def mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)


def process_files(input_dir: Path, corpus_file: Path, dataset_file: Path) -> None:
    source_files = sorted(input_dir.glob("*.txt"))
    if not source_files:
        raise FileNotFoundError(f"No .txt source files found in {input_dir}")

    all_paragraphs: list[dict[str, str | int]] = []
    mojibake_hits = 0

    for file_path in source_files:
        raw_content = file_path.read_text(encoding="utf-8", errors="replace")
        cleaned = normalize_text(raw_content)
        paragraphs = reconstruct_paragraphs(cleaned)

        for index, paragraph in enumerate(paragraphs):
            score = mojibake_score(paragraph)
            mojibake_hits += int(score > 0)
            all_paragraphs.append(
                {
                    "text": paragraph,
                    "source_file": file_path.name,
                    "paragraph_index": index,
                    "char_count": len(paragraph),
                    "mojibake_score": score,
                }
            )

        print(f"{file_path.name}: kept {len(paragraphs)} paragraphs")

    corpus_file.parent.mkdir(parents=True, exist_ok=True)
    corpus_file.write_text(
        "\n\n".join(item["text"] for item in all_paragraphs),
        encoding="utf-8",
    )

    with dataset_file.open("w", encoding="utf-8") as output:
        for item in all_paragraphs:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Total paragraphs kept: {len(all_paragraphs)}")
    print(f"Paragraphs with mojibake markers: {mojibake_hits}")
    print(f"Saved corpus: {corpus_file}")
    print(f"Saved dataset: {dataset_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean Lacan source txt files into JSONL.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--corpus-file", type=Path, default=DEFAULT_CORPUS_FILE)
    parser.add_argument("--dataset-file", type=Path, default=DEFAULT_DATASET_FILE)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    process_files(args.input_dir, args.corpus_file, args.dataset_file)
