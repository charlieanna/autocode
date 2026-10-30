"""The one ordered recovery chain every dispatch path shares.

The live loop, serial unit dispatch and explicit resume each hand-wrote their own
``try each recoverer in turn`` list, and the copies drifted (#1071): serial dispatch had no
capacity recovery, resume had no startup or truncated-review recovery. The order below is
the live loop's, exactly, and every site routes through it; the row name a site gets back
keys its own follow-up (the capacity notice, the stage-timeout budget top-up).

Imports only lower-level modules; the runner is passed in, so this stays below the
controller and out of the import cycles. Recoverers are resolved at call time so tests
can patch the module they live in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_job_failure as job_failure
    from . import autocode_network_retry as network_retry
    from . import autocode_provider_recovery as provider_recovery
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_route_recovery as route_recovery
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_network_retry as network_retry
    import autocode_provider_recovery as provider_recovery
    import autocode_resolver_recovery as resolver_recovery
    import autocode_route_recovery as route_recovery


def _runtime_first(module, name):
    def invoke(runtime, state: RunState, run_dir, workspace, error):
        return getattr(module, name)(runtime, state, run_dir, workspace, error)

    return invoke


def _on_runtime(name):
    # The stage recoverers live in autocode_stage_recovery, which imports the planning
    # cycle; reaching them through the runner keeps this module below it.
    def invoke(runtime, state: RunState, run_dir, workspace, error):
        return getattr(runtime, name)(state, run_dir, workspace, error)

    return invoke


# (row name, recoverer) in the live loop's order. The names are the contract call sites key
# their follow-up effects on; a row's recoverer owns its own bound and counter.
RECOVERY_CHAIN = (
    ("job_failure", _runtime_first(job_failure, "recover")),
    ("network", _runtime_first(network_retry, "recover")),
    ("startup", _runtime_first(provider_recovery, "recover_startup")),
    # A permitted-set run switches a stopped model on its own (#1426 step 9); every
    # other quota stop falls through to the pause that asks the person (#184).
    ("route", _runtime_first(route_recovery, "recover")),
    ("truncated_review", _on_runtime("automatically_recover_truncated_review")),
    ("capacity", _on_runtime("automatically_recover_capacity_stage")),
    ("timeout", _on_runtime("automatically_recover_timed_out_stage")),
    ("external_directory_denial", _on_runtime("automatically_recover_external_directory_denial")),
    # A repeated incident's no-progress hold takes AutoCode's own one scoped retry, then ends the
    # run with its outcome recorded (#2485).
    ("no_progress", _runtime_first(resolver_recovery, "recover_no_progress")),
)


def recover(runtime, state: RunState, run_dir, workspace, error) -> str | None:
    """Run the chain in order; the first recoverer that fires wins.

    Returns its row name, or None when nothing recovered and the pause stands.
    """
    for name, invoke in RECOVERY_CHAIN:
        if invoke(runtime, state, run_dir, workspace, error):
            return name
    return None
