"""Read-only admission frontier for a retry of a repeated failure."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_failures as failures
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_stalls as stage_stalls
    from . import autocode_util as util
except ImportError:
    import autocode_failures as failures
    import autocode_source_scope as source_scope
    import autocode_stage_stalls as stage_stalls
    import autocode_util as util


class FailureRetryFrontierRefused(ValueError):
    """A completed observation found that this exact retry is not admissible."""


def failure_retry_frontier(state: RunState, workspace, *, base_snapshot=util.snapshot):
    """One exact source/stage/count answer shared by retry and revalidation."""
    record = next((row for row in reversed(state.get("stages", [])) if row.get("failure_key")), None)
    repeated = (failures.repeated(state, record) or stage_stalls.retry_entry(state, record)) if record else None
    if not repeated:
        raise FailureRetryFrontierRefused("No unchanged repeated failure to authorize; fix the cause, then resume")
    selected = cast(dict, record).get("failure_key")
    revision = source_scope.snapshot(workspace, state, base_snapshot=base_snapshot)["revision"]
    identity = repeated["identity"]
    if (
        (state.get("failure_history") or {}).get(selected) is not repeated
        or failures.key(identity) != selected
        or identity["artifact_hash"] != revision
        or cast(dict, record).get("source_revision") != revision
        or identity["stage"] != (cast(dict, record).get("original_stage") or cast(dict, record).get("stage"))
        or identity["stage"] != state.get("next_stage")
    ):
        raise FailureRetryFrontierRefused(
            "Retry authorization requires the exact current source, stage and failure identity"
        )
    return {
        "failure_key": selected,
        "identity": copy.deepcopy(identity),
        "count": cast(dict, repeated)["count"],
        "source_revision": revision,
    }
