"""Word statistics."""
from collections import Counter


def words(text: str) -> list:
    return [word.lower().strip(".,;:!?\"'") for word in text.strip().split(" ")]


def count_words(text: str) -> int:
    return len(words(text))


def top_words(text: str, n: int) -> list:
    return Counter(words(text)).most_common(n)
