"""Question text a client shows a person, without the CLI machinery the runner writes into it (#3145).

AutoResolver composes one request text for both audiences: a sentence for the person ("Provide
corrective information or leave the run paused.") followed by advice for an operator at a shell
("... with --resolver-request ID --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN) ..., then autocode
resume."). The saved request and the run's stop reason keep that advice for the terminal. A client
renders the status view's question verbatim, and a person there never types a flag, token or command:
the view names the action as an argument array. So the view shows the person's sentences only.

Pure text functions; imports nothing from AutoCode.
"""

from __future__ import annotations

import re

# A CLI flag (`--resume-paused`), an environment variable that carries a token, or an `autocode`
# command line. Prose dashes ("a -- b"), hyphenated words and model names do not match.
MACHINERY = re.compile(
    r"(?<![\w/.-])--[a-z][a-z0-9]*(?:-[a-z0-9]+)*|\bAUTOCODE_[A-Z0-9_]+\b|(?<![\w/.-])autocode\s+(?:resume\b|--)"
)
# A sentence ends at ., ! or ? followed by whitespace and a capital letter or an opening parenthesis.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
# An option's trailing "with <command>" clause: "Allow more plan reviews with --planning-review-call-limit N".
OPTION_CLAUSE = re.compile(r"\s+(?:with|using|via)\s+(?:autocode\s+\S+\s+)?--\S.*$")
# Shown when every sentence of a request was operator advice, so the person still reads what is asked.
FALLBACK = "AutoCode needs your information before this run can continue."


def machinery(text) -> bool:
    """Whether ``text`` names a CLI flag, a token's environment variable or an `autocode` command."""
    return bool(MACHINERY.search(str(text or "")))


def plain(text):
    """``text`` without its sentences that tell a person what to type at a shell.

    Line breaks are kept (a criterion revision lists one correction per line), and a line that was
    only machinery is dropped. ``None`` and empty text pass through unchanged."""
    if not text or not machinery(text):
        return text
    lines = []
    for line in str(text).split("\n"):
        kept = [sentence for sentence in SENTENCE_END.split(line) if not machinery(sentence)]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    shown = "\n".join(lines).strip()
    return shown or FALLBACK


def plain_option(option):
    """An option label without its "with --flag ..." clause; a label that is all machinery is dropped (None)."""
    if not isinstance(option, str) or not machinery(option):
        return option
    label = OPTION_CLAUSE.sub("", option).strip()
    return label if label and not machinery(label) else None


def question(row: dict) -> dict:
    """A status-view question row with its person-facing text made plain; other fields unchanged."""
    shown = dict(row)
    for field in ("question", "why"):
        if field in shown:
            shown[field] = plain(shown[field])
    if isinstance(shown.get("options"), list):
        shown["options"] = [label for label in map(plain_option, shown["options"]) if label is not None]
    return shown
