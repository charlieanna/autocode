"""A final message of leaked transport markup is a known provider defect, never a report.

Issue #3310: through kilocode/opencode, a model can end a stage with transport markup as its
final message instead of the JSON report, in two shapes — a shell tool call written as text,
``<tool_call><function=bash><parameter=command>…</parameter></function></tool_call>``, which the
transport never executes; and context-compaction notices, ``<system_warning>Long conversation
exceeded 128k tokens: …</system_warning>``, wrapped around commentary with no report anywhere
in the message. The runner used to reject both as the generic invalid report ("OpenCode final
message is not a JSON report"), so the failure ledger did not name the defect and the repair
instruction could not say what had happened — the same problem ``autocode_cmd_only_report``
fixed for the ``{"cmd": ...}`` spelling (#512).

``refuse`` recognizes both shapes on the raw final message, before report processing, and
raises ``LeakedMarkupReport``: the stage is rejected, a tool call in the message is never run
and never read as report content. Its exception class names the defect in the failure ledger
(autocode_failures), so repeats count as one failure, and the repair route carries a targeted
instruction (``CORRECTION`` for the one same-session correction, ``REPAIR_INSTRUCTION`` for a
full report repair). A message that also contains a report is never refused: only one whose
text outside the markup holds no object at all.

Pure module: no runner imports.
"""

from __future__ import annotations

import re

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

ERROR_PREFIX = "Provider final message is leaked transport markup"
TOOL_CALL_ERROR = (
    ERROR_PREFIX + ": a tool call written as text (<tool_call>...) instead of the JSON report; "
    "AutoCode never runs it (a known provider defect, docs/providers.md: if it repeats, "
    "run this role on another model)"
)
WARNING_ERROR = (
    ERROR_PREFIX + ": context-compaction notices (<system_warning>...) instead of the JSON report "
    "(a known provider defect, docs/providers.md: if it repeats, run this role on another model)"
)
WARNING = re.compile(r"<system_warning>.*?</system_warning>", re.DOTALL)

CORRECTION = prompts.get("fragments/leaked-markup-report/correction.md")
REPAIR_INSTRUCTION = prompts.get("fragments/leaked-markup-report/repair-instruction.md")


class LeakedMarkupReport(RuntimeError):
    """A stage's final message was leaked transport markup; the class names the defect in the failure ledger."""


def is_tool_call_text(text: str) -> bool:
    """Whether the whole message is one tool call written as text, so it holds no report content."""
    stripped = text.strip()
    return (
        stripped.startswith("<tool_call")
        and stripped.endswith("</tool_call>")
        and stripped.count("<tool_call") == 1
        and stripped.count("</tool_call>") == 1
    )


def is_compaction_notice(text: str) -> bool:
    """Whether the message carries compaction notices and no object outside them, so no report can be salvaged."""
    if not WARNING.search(text):
        return False
    return "{" not in WARNING.sub("", text)


def refuse(text: str) -> str:
    """Return ``text`` unchanged, or raise LeakedMarkupReport when it is only leaked markup."""
    if not isinstance(text, str):
        return text
    if is_tool_call_text(text):
        raise LeakedMarkupReport(TOOL_CALL_ERROR)
    if is_compaction_notice(text):
        raise LeakedMarkupReport(WARNING_ERROR)
    return text


def matches(error) -> bool:
    """Whether a recorded rejection is this defect."""
    return str(error or "").startswith(ERROR_PREFIX)
