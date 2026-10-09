"""Word statistics."""
from collections import Counter


def words(text: str) -> list:
    """Words split on any whitespace; punctuation-only tokens are not words."""
    cleaned = (word.lower().strip(".,;:!?\"'") for word in text.split())
    return list(cleaned)


def count_words(text: str) -> int:
    return len(words(text))


def top_words(text: str, n: int) -> list:
    return Counter(words(text)).most_common(n)
