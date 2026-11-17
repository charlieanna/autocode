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
# A parenthetical command hint inside an otherwise plain sentence ("... (change it with --max-idle-seconds N)").
PARENTHETICAL = re.compile(r"\s*\([^()]*\)")
# An option's trailing "with <command>" clause: "Allow more plan reviews with --planning-review-call-limit N".
OPTION_CLAUSE = re.compile(r"\s+(?:with|using|via)\s+(?:autocode\s+\S+\s+)?--\S.*$")
# Shown when every sentence of a request was operator advice, so the person still reads what is asked.
FALLBACK = "AutoCode needs your information before this run can continue."

# Internal prompt/handoff wrapper, changed-line gap/enum, and report-repair schema diagnostics (#3733).
_HANDOFF_MACHINERY = re.compile(
    r"provider handoff must contain\b|\bCURRENT HANDOFF DATA\b|\bREPAIR_HANDOFF_BYTES\b"
    r"|\brepair-edits\.schema\.json\b|\bhandoff exceeds\b|\bunlaunchable repair\b",
    re.IGNORECASE,
)
_GAP_ENUM_MACHINERY = re.compile(
    r"\bin the gap_id enum\b|\bleaves other offered changed lines undecided\b"
    r"|\bchanged_code_exceptions\b|\bquotes\s+\".*?\"\s+but binds\b",
    re.IGNORECASE,
)
_SCHEMA_MACHINERY = re.compile(
    r"(?:^|[\s(])\$(?::|\.[A-Za-z_[])|\bReport edits?\b.*?(?:path|enum|schema|apply)\b",
    re.IGNORECASE,
)

HANDOFF_SUMMARY = "AutoCode hit an internal stage handoff format error; your saved work is intact"
GAP_ENUM_SUMMARY = "AutoCode's review report left changed-line check items undecided; your saved work is intact"
SCHEMA_SUMMARY = "AutoCode could not validate a stage's structured report; your saved work is intact"

_LOOPBACK_ERRNO = re.compile(r"\b(?:operation not permitted|errno\s+1\b|eperm)\b", re.IGNORECASE)
_LOOPBACK_TARGET = re.compile(
    r"\b127\.0\.0\.1\b|::1|\blocalhost\b|\bloopback\b|\.bind\s*\(|\bbind\s*\(\s*\(|\.listen\s*\(|\b(?:HTTP|TCP)Server\b|\bsocket\b",
    re.IGNORECASE,
)


def loopback_bind_blocked(text: object) -> bool:
    """True when ``text`` describes a local loopback socket bind denied with EPERM / Operation not permitted."""
    raw = str(text or "")
    return bool(raw and _LOOPBACK_ERRNO.search(raw) and _LOOPBACK_TARGET.search(raw))


def seatbelt_loopback_diagnostic(*, for_person: bool = False) -> str:
    """Explain the macOS Seatbelt stage-vs-runner loopback asymmetry (plain words for clients when ``for_person``)."""
    if for_person:
        return (
            "macOS Seatbelt blocked a local loopback socket bind (127.0.0.1: Operation not permitted) inside the "
            "stage sandbox, while AutoCode's local runner checks run outside Seatbelt. Use a Linux host when "
            "stage tests bind local sockets."
        )
    return (
        "macOS Seatbelt blocked a local loopback socket bind (127.0.0.1: Operation not permitted) inside the "
        "stage sandbox, while AutoCode's local regression_proof and --allow-uncontained-tools "
        "permit loopback socket binds."
    )


def internal_machinery(text) -> bool:
    """Whether ``text`` is internal AutoCode prompt, handoff, gap_id-enum, or report-schema validator prose."""
    raw = str(text or "")
    if not raw:
        return False
    return bool(_HANDOFF_MACHINERY.search(raw) or _GAP_ENUM_MACHINERY.search(raw) or _SCHEMA_MACHINERY.search(raw))


def human_summary(text) -> str | None:
    """Plain-English summary for internal prompt/schema validator prose, or None when ``text`` is not that."""
    raw = str(text or "")
    if not raw:
        return None
    if _HANDOFF_MACHINERY.search(raw):
        return HANDOFF_SUMMARY
    if _GAP_ENUM_MACHINERY.search(raw):
        return GAP_ENUM_SUMMARY
    if _SCHEMA_MACHINERY.search(raw):
        return SCHEMA_SUMMARY
    return None


def machinery(text) -> bool:
    """Whether ``text`` names a CLI flag, a token's environment variable or an `autocode` command."""
    return bool(MACHINERY.search(str(text or "")))


def _unbracket(line: str) -> str:
    return PARENTHETICAL.sub(lambda group: "" if machinery(group.group(0)) else group.group(0), line)


def plain(text):
    """``text`` without its sentences that tell a person what to type at a shell.

    Line breaks are kept (a criterion revision lists one correction per line), and a line that was
    only machinery is dropped. ``None`` and empty text pass through unchanged."""
    if not text or not machinery(text):
        return text
    lines = []
    for line in str(text).split("\n"):
        unbracketed = _unbracket(line)
        kept = [sentence for sentence in SENTENCE_END.split(unbracketed) if sentence and not machinery(sentence)]
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
