"""The human request remains authoritative throughout a diagnosed bug fix."""

INSTRUCTION = """
HUMAN TASK COVERAGE: task and saved human answers define the requested outcome.
A diagnosis, proposed fix plan, or approval of a model-written brief does not
authorize omitting an applicable human obligation. Compare the original request
with the criteria, implementation, and executed evidence separately from the
diagnosis. Carry every requested behavior, invariant, preservation constraint,
and observable public contract into explicit acceptance and verification.
A named test proves its actual assertions, not every sentence in its criterion.
Inspect those assertions. A single special boundary does not establish a general
condition: check representative inputs and intermediate boundary conditions,
and verify public observations as well as internal implementation behavior.
An assertion about a newly added private field does not establish the requested
public behavior. Passing the existing suite does not establish an omitted check.
At a milestone checkpoint, distinguish obligations due now from those explicitly
assigned to approved later milestones; pending later work is not a defect in the
current milestone. Final completion requires every applicable original obligation
to be independently evidenced, or an explicit human-authorized limitation.
For an in-scope implementation defect, return a blocking finding with the exact
task obligation and concrete source or executed evidence. A missing approved
criterion requires contract correction through the existing blocker/re-approval
path; approval of an incomplete draft is not an implicit waiver. Do not invent
new requirements or ask the user to repeat an already explicit outcome.
"""


def instruction(investigation: dict | None) -> str:
    """Apply the same authority policy to planning and judging a reproduced bug."""
    return INSTRUCTION if (investigation or {}).get("outcome") == "reproduced" else ""
