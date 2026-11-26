"""Which attempt a scripted Builder call makes, for the fakes that plant a fault in the first one.

A failing regression proof sends the change straight back to the Builder once before any Tester
(autocode_proof_findings.builder_round). That call is not a new attempt: no review has seen the first one, and a
fault planted for the reviewers or AutoResolver to find must survive it. Its handoff says so in next_action
(autocode_proof_findings.BUILDER_NOTE). Each fake records ``proof_round`` on the trace row of every Builder call,
so this one count reads the same answer everywhere. Loaded with runpy beside the fake that uses it.
"""

NOTE = "the Tester has not been started"


def proof_round(stage: str, data: dict) -> bool:
    return stage == "builder" and NOTE in str(data.get("next_action") or "")


def builds(previous: list[dict]) -> int:
    """The Builder attempts already made: every Builder row in the fake's trace but a proof round."""
    return sum(row.get("stage") == "builder" and not row.get("proof_round") for row in previous)


def attempt(stage: str, data: dict, previous: list[dict]) -> int:
    """This Builder call's attempt number: the one before it again for a proof round, else the next."""
    made = builds(previous)
    return made if proof_round(stage, data) and made else made + 1
