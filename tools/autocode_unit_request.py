"""Prepare a unit request with invocation-local authorization and checked guidance."""

try:
    from . import autocode_stuck_job as stuck
except ImportError:
    import autocode_stuck_job as stuck


def prepare(state, stage, state_path, schema_dir, *, select_unit, retry_authorization=None):
    options = {"retry_authorization": retry_authorization} if stage == "astra_resolve" else {}
    request = select_unit(stage).prepare(state, stage, state_path, schema_dir, **options)
    return stuck.with_guidance(state, stage, request)
