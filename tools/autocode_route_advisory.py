"""Report newly shared producer/consumer routes after an operator's saved change.

These are advisory relationships, separate from mandatory verifier independence.
The selected routes describe future dispatch, not who authored existing artifacts.
"""

from __future__ import annotations

import sys

try:
    from . import autocode_model_identity as identity
    from . import autocode_roles as roles
except ImportError:
    import autocode_model_identity as identity
    import autocode_roles as roles


def _chains(settings):
    if settings.get("planning_flow") == "v2":
        return (
            ("requirements", "requirements", "glm", "plan"),
            ("requirements", "requirements", "terra", "terra"),
            ("glm", "plan", "terra", "terra"),
        )
    if settings.get("joint_planning"):
        return (
            ("requirements", "requirements_gather", "astra", "astra_plan"),
            ("requirements", "requirements_gather", "terra", "terra"),
            ("glm", "glm_revise", "terra", "terra"),
            ("astra", "astra_plan", "terra", "terra"),
        )
    return (("astra", "astra_plan", "terra", "terra"),)


def warnings(previous, selected):
    """Messages for newly shared links involving an actual model change; no mutation."""
    if not previous or not selected:
        return []  # A new run has no operator route change to report.
    before = previous.get("roles") or {}
    after = (selected or {}).get("roles") or {}
    messages = []
    for producer, producer_stage, consumer, consumer_stage in _chains(selected or {}):
        old_p = (before.get(producer) or {}).get("model")
        old_c = (before.get(consumer) or {}).get("model")
        new_p = (after.get(producer) or {}).get("model")
        new_c = (after.get(consumer) or {}).get("model")
        if (old_p, old_c) == (new_p, new_c):
            continue
        if identity.shared_family(new_p, new_c) and not identity.shared_family(old_p, old_c):
            producer_job = roles.screen_name(producer_stage)
            consumer_job = roles.screen_name(consumer_stage)
            messages.append(
                f"Model-route advisory: selected {producer_job} -> {consumer_job} routes now share a model family "
                f"({new_p} / {new_c}; --{producer}-model / --{consumer}-model). "
                "This producer/consumer link has no independence blocker; "
                "existing verifier checks still apply. Saved artifacts retain their original authorship."
            )
    return messages


def report_changes(previous, selected):
    """Call only after the corresponding settings or user action has been saved."""
    for message in warnings(previous, selected):
        print(message, file=sys.stderr)
