"""Bounded planning description of the saved repository proof reference.

This projection neither prepares a baseline nor accepts or reuses evidence.
The regression runner owns the effective tree, including any approved patch.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def context(state: RunState) -> dict:
    saved = state.get("base_commit")
    commit = saved if isinstance(saved, str) and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", saved) else None
    return {
        "repository_base_commit": commit,
        "scope": "saved_run_start" if commit else "unknown",
        "preparation": "The runner may apply an approved reviewed-change or operator patch over this repository commit.",
    }
