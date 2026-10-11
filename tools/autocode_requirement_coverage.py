"""Match verified requirement quotes and explicitly ignored whole statements."""

import re


def requirement_coverage_text(text):
    """Ignore Markdown list markers when comparing already verified quotes."""
    return re.sub(r"(?m)^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", "", str(text)).strip()


def missing_sentences(sentences, verified_quotes, ignored_statements):
    """An ignored fragment cannot discharge a longer requirement sentence."""
    quotes = [requirement_coverage_text(text) for text in verified_quotes]
    ignored = [requirement_coverage_text(text) for text in ignored_statements]
    missing = []
    for sentence in sentences:
        normalized = requirement_coverage_text(sentence)
        if any(quote and (quote in normalized or normalized in quote) for quote in quotes):
            continue
        # A whole ignored statement may include an explanation after its text.
        # The scanner adds a final period to substantive Markdown headings.
        if any(text and (normalized in text or normalized.removesuffix(".") == text) for text in ignored):
            continue
        missing.append(sentence)
    return missing
