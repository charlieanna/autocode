"""Verifier independence: the one rule for which model may check which model's work.

Pure domain logic over route dicts; imports only autocode_model_identity and the screen
names in autocode_roles. A verifier never runs the model that produced the work it judges:
one model is always a clash, however its route is spelled (a gateway in front of its vendor,
or the native Codex and OpenCode spellings of one GPT tier), and GLM, MiMo, Claude and OpenAI's GPT models are
families, so GLM checks MiMo work and MiMo checks GLM work (user 2026-09-26), and no Claude model
checks another's. The built-in Claude Code engine reaches only Claude models, so a run on it keeps
Claude checking Claude, never one model checking itself (``same_vendor_engine``).

Asked by the dispatch guard before a launch (autocode_dispatch, which a human
``--answer route-<role>=MODEL`` also re-checks through), the Builder's stronger attempt
deciding which checkers to move (autocode_builder_policy), a new run's retry policy refusing
a strong model that would be checked by its own family, a tool's [builder_retry] table
(providers/command.py), the model catalogue's suggestions, and a new run's check of the
checker model its stronger attempt would move checkers to (autocode_route_ladder.serve) (#612).
"""

from __future__ import annotations

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_roles as roles
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_roles as roles


def _label(producer_job, verifier_job):
    return f"{roles.SCREEN[producer_job]}/{roles.SCREEN[verifier_job]}"


# (producer, verifier, label): the roles whose models must stay independent, labelled with
# the jobs' screen names.
PAIRS = (
    ("terra", "sol", _label("builder", "tester")),
    ("terra", "completion", _label("builder", "completion")),
    ("glm", "plan_reviewer", _label("planner", "plan_reviewer")),
)

# Workflows in which no producer role of PAIRS runs. The review workflow's only stage
# (review_change, on the Tester's route) judges a change written outside the run, and a
# follow-up turn that builds starts a new workflow, so no route there grades its own work.
# The discuss workflow's only stage (answer_question, the Analyst) answers and builds
# nothing; its runner rejects any workspace change.
WITHOUT_PRODUCERS = frozenset({"review", "discuss"})


def verifiers(producer):
    """The roles that check the given producer role's work."""
    return tuple(verifier for role, verifier, _label in PAIRS if role == producer)


def shares(producer_model, verifier_model, *, vendor_engine=False):
    """Whether a verifier on ``verifier_model`` would be checking its own model's work.

    ``vendor_engine`` is an engine that reaches only one vendor's models (``same_vendor_engine``):
    there, two different Claude models are not one model, only a model checking itself is.
    """
    if not model_identity.shared_family(producer_model, verifier_model):
        return False
    if vendor_engine and model_identity.family(producer_model) in ("claude", "openai"):
        return model_identity.identity(producer_model) == model_identity.identity(verifier_model)
    return True


def may_share(producer_model, verifier_model, *, vendor_engine=False):
    """``shares``, or the two might name one model: any ``openai/X`` and a bare ``X``.

    For a remedy that moves a checker, where moving one too many is harmless; the guard
    refuses only what ``shares`` proves (another provider's ``openai/`` name is not a GPT alias).
    """
    if shares(producer_model, verifier_model, vendor_engine=vendor_engine):
        return True
    return (
        isinstance(producer_model, str)
        and isinstance(verifier_model, str)
        and bool(producer_model)
        and producer_model.removeprefix("openai/") == verifier_model.removeprefix("openai/")
    )


def clash(producer_model, verifier_model, *, vendor_engine=False):
    """Why one model cannot check the other's work, or None."""
    if not shares(producer_model, verifier_model, vendor_engine=vendor_engine):
        return None
    if model_identity.identity(producer_model) == model_identity.identity(verifier_model):
        return f"identical model {producer_model}"
    return f"same family {model_identity.family(producer_model)} ({producer_model} / {verifier_model})"


def same_vendor_engine(settings):
    """Whether the run's engine can reach only one vendor's models: the built-in Claude Code provider or Codex."""
    settings = settings or {}
    return settings.get("provider") == "claude" or settings.get("engine") == "codex"


def clashes(settings, workflow=None):
    """Every broken producer/verifier pair as structured rows for a model question.

    Each row names the verifier role a person can reroute so it stops grading its
    own producer's work. Empty in single-model mode and in a ``workflow`` in which
    no producer runs (WITHOUT_PRODUCERS).
    """
    settings = settings or {}
    if settings.get("single_model_mode") or workflow in WITHOUT_PRODUCERS:
        return []
    roles = settings.get("roles") or {}
    found = []
    for producer, verifier, label in PAIRS:
        producer_model = (roles.get(producer) or {}).get("model")
        verifier_model = (roles.get(verifier) or {}).get("model")
        if not producer_model or not verifier_model:
            continue
        problem = clash(producer_model, verifier_model, vendor_engine=same_vendor_engine(settings))
        if problem:
            found.append(
                {
                    "producer": producer,
                    "verifier": verifier,
                    "label": label,
                    "problem": problem,
                    "producer_model": producer_model,
                    "verifier_model": verifier_model,
                }
            )
    return found


def violations(settings, workflow=None):
    """Every producer/verifier pair of a run's routes that breaks independence.

    Empty in single-model mode, which the user chose knowing one model checks itself,
    and in a ``workflow`` in which no producer runs.
    """
    return [f"{row['label']}: {row['problem']}" for row in clashes(settings, workflow)]
