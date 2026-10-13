"""Route a scripted scenario's live stage through a provider and model named on the command line.

A scenario with ``[fake] live_investigator = true`` scripts every stage but one, and names that
stage's model in its ``[fake] flags`` (``--investigator-model openai/gpt-6-sol``). A campaign that
may only spend on another tool's models (the Claude provider, ``examples/claude-provider``) needs
to point that one stage elsewhere without editing the catalog entry, so ``run --fake`` accepts
``--provider NAME`` (the tool the live stage runs through) and ``--live-model MODEL`` (replaces the
model value of the scenario's live-stage flag). Without either the computed argv is unchanged.
"""

from __future__ import annotations


class NoLiveStage(ValueError):
    """The scenario names no live stage, so there is nothing to route."""


def model_option(scenario_flags: list[str]) -> str:
    """The option in a scenario's ``[fake] flags`` that names its live stage's model (``--*-model``)."""
    options = [flag for flag in scenario_flags if flag.startswith("--") and flag.endswith("-model")]
    if len(options) != 1:
        raise NoLiveStage(
            "no live-stage model flag in [fake] flags"
            if not options
            else f"[fake] flags name more than one model: {', '.join(options)}"
        )
    return options[0]


def check(scenario) -> tuple[str, str]:
    """The scenario's live-stage model flag and its catalog value; raises NoLiveStage without one."""
    if not getattr(scenario, "fake_live_calls", False):
        raise NoLiveStage("the scenario scripts every stage; there is no live stage to route")
    flags = list(scenario.fake_flags)
    option = model_option(flags)
    if flags.index(option) + 1 >= len(flags):
        raise NoLiveStage(f"{option} names no model in [fake] flags")
    return option, flags[flags.index(option) + 1]


def route(flags: list[str], scenario, *, provider: str | None = None, model: str | None = None) -> list[str]:
    """``flags`` (the fake setup's, scenario flags included) with the live stage routed as asked.

    ``model`` replaces the value after the scenario's live-stage model flag; ``provider`` is passed
    to AutoCode as ``--provider``. Raises NoLiveStage when the scenario has no live stage to route.
    """
    routed = list(flags)
    if not provider and not model:
        return routed
    option, _ = check(scenario)
    if model:
        index = len(routed) - 1 - routed[::-1].index(option)
        routed[index + 1] = model
    if provider:
        routed += ["--provider", provider]
    return routed


def record(scenario, *, provider: str | None = None, model: str | None = None) -> dict | None:
    """What result.json keeps about the routed live stage, or None when nothing was overridden."""
    if not provider and not model:
        return None
    option, default = check(scenario)
    return {"flag": option, "model": model or default, "provider": provider}
