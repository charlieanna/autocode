"""Replies that only steer a question the runner wrote, and so state no requirement (#1630).

A person's answer to a runner-written question is saved as a human source (autocode_source_log), and
every criterion set must quote every sentence of every source. Some answers are not requirements:
"plan" to a Requirements review starts planning; "Keep the approved wording" declines a rewording.
Quoting them cost a Planner reasoning, and a plan that rightly left them uncited failed the Plan
Reviewer's seal. The registry below is runner-owned and keyed by the runner's own question ids; a
question the model wrote can never join it, and any other reply to these questions (a correction)
is still a source. The answer stays in the log and the ledger, so decision provenance can cite it
and contracts that already quote it keep verifying. Imports only the modules that write the questions.
"""

from __future__ import annotations

try:
    from . import autocode_requirements_review as requirements_review
    from . import autocode_rewording_question as rewording
except ImportError:
    import autocode_requirements_review as requirements_review
    import autocode_rewording_question as rewording

REPLIES = {
    requirements_review.PREFIX: (requirements_review.APPROVE,),
    rewording.PREFIX: (rewording.ACCEPT, rewording.KEEP),
}


def _bare(text) -> str:
    return " ".join(str(text or "").split()).lower()


def control_record(record) -> bool:
    """Whether a human-source record is one of the registered replies to a runner-written question."""
    if not isinstance(record, dict) or record.get("kind") != "user_answer":
        return False
    ident = str(record.get("id") or "").removeprefix("answer:")
    return any(
        ident.startswith(prefix) and _bare(record.get("text")) in {_bare(reply) for reply in replies}
        for prefix, replies in REPLIES.items()
    )
