"""Deterministic text normalization and duplicate fingerprints."""

from __future__ import annotations

import hashlib
import re
import unicodedata

WORD_PATTERN = re.compile(r"[a-z0-9]+(?:['’][a-z0-9]+)?", re.IGNORECASE)


def clean_human_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\ufeff", "")
    text = re.sub(r"[\t\f\v ]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_tokens(value: object) -> list[str]:
    return [match.group(0).replace("’", "'").casefold() for match in WORD_PATTERN.finditer(clean_human_text(value))]


def comparison_key(value: object) -> str:
    return " ".join(word_tokens(value))


def fingerprint(value: object) -> str:
    return hashlib.sha256(comparison_key(value).encode("utf-8")).hexdigest()


def simhash64(value: object, shingle_size: int = 3) -> int:
    """Return a deterministic 64-bit SimHash for near-duplicate detection."""

    tokens = word_tokens(value)
    if not tokens:
        return 0
    if len(tokens) < shingle_size:
        features = tokens
    else:
        features = [" ".join(tokens[index : index + shingle_size]) for index in range(len(tokens) - shingle_size + 1)]
    vector = [0] * 64
    for feature in features:
        feature_hash = int.from_bytes(hashlib.sha256(feature.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            vector[bit] += 1 if feature_hash & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            result |= 1 << bit
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def prefix_signature(value: object, token_count: int = 12) -> str:
    return " ".join(word_tokens(value)[:token_count])


def common_prefix_ratio(left: object, right: object) -> float:
    left_tokens = word_tokens(left)
    right_tokens = word_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = 0
    for left_token, right_token in zip(left_tokens, right_tokens, strict=False):
        if left_token != right_token:
            break
        shared += 1
    shorter = min(len(left_tokens), len(right_tokens))
    return shared / shorter


def left_coverage_ratio(left: object, right: object) -> float:
    """Return how much of ``left`` is covered by the start of ``right``."""

    left_tokens = word_tokens(left)
    right_tokens = word_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = 0
    for left_token, right_token in zip(left_tokens, right_tokens, strict=False):
        if left_token != right_token:
            break
        shared += 1
    return shared / len(left_tokens)
