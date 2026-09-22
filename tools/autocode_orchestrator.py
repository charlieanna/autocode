"""Shared durable stage loop used by code and Figma workflows."""
from __future__ import annotations

SKIP = object()


class LoopExit(Exception):
    """Stop orchestration at a deliberate CLI boundary."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def drive(state, dispatch, *, apply=None, before=None, after=None, persist=None,
          active=lambda value: value.get('status') == 'RUNNING'):
    """Run the saved state's next stage until its workflow reaches a boundary.

    Target adapters own prompts, schemas and transitions. This function owns the
    common sequence: guard -> select saved stage -> dispatch -> apply -> persist.
    Returning SKIP from a hook restarts from the newly saved state without
    pretending a stage completed.
    """
    while active(state):
        if before and before(state) is SKIP:
            continue
        stage = state.get('next_stage')
        if not isinstance(stage, str) or not stage:
            raise ValueError('Running orchestration has no next stage')
        outcome = dispatch(state, stage)
        if outcome is SKIP:
            continue
        if apply:
            applied = apply(state, stage, outcome)
            if applied is SKIP:
                continue
        if persist:
            persist(state)
        if after:
            after(state, stage, outcome)
    return state
