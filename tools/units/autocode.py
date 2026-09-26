"""Autocode owns Builder implementation and parallel scheduling/integration."""
try:
    from .. import autocode_dispatch as scheduler
except ImportError:
    import autocode_dispatch as scheduler
from .common import execution_request


def prepare(state, stage, state_path, schema_dir):
    if stage not in ("astra_plan", "terra"):
        raise ValueError(f"Autocode cannot run {stage}")
    return execution_request(state, stage, state_path, schema_dir)


def dispatch(state, workspace, run_dir):
    return scheduler.dispatch(state, workspace, run_dir)
