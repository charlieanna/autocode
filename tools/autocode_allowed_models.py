"""The models a person allows AutoCode to use, per backend: one list every model choice reads.

Owner request 2026-11-17: an account can serve hundreds of models (a gateway lists about 500), and
AutoCode offered every one of them at setup, measured from them and fell back to them. The person
now says once which models AutoCode may use on a backend. From then on:

- every stage list (``autocode setup``, ``autocode stages``, Stage models in VS Code) offers only
  those models, and a stage choice outside them is refused (``autocode_stage_choices.offers``);
- measuring a stage (``autocode_stage_probe``) ticks and offers only those models;
- automatic selection and model-stop recovery choose only among them (``autocode_configure``,
  ``autocode_route_pool``, through ``offers``);
- an escalation never climbs to a model outside them: the Builder's stronger attempt and the
  route ladders keep only rungs on allowed models (``restrict_retry``, ``autocode_model_catalogue``).

A model a person types for one run (``--builder-model``) still runs: a typed flag stays exact.

No list means every model the backend's accounts serve, as before. The list lives in
``stages.toml``, beside the stage choices it limits, as one ``[allowed]`` table::

    [allowed]
    kilocode = ["anthropic/claude-sonnet-4-5", "openai/gpt-5.6-sol"]

Pure: tables in, tables out. ``autocode_stage_choices`` reads and writes the file.
"""

from __future__ import annotations

import json
import re

# The table name in stages.toml. No backend takes this name (autocode_stage_choices.BACKEND_LABELS).
TABLE = "allowed"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,199}$")
_BACKEND = re.compile(r"[a-z][a-z0-9_]{0,62}$")
# The Builder's stronger attempt names a model outside the list: it is not made (autocode_builder_policy).
NOT_ALLOWED = "not_allowed"


def read(table, file) -> dict[str, tuple[str, ...]]:
    """``{backend: (model, ...)}`` from the ``[allowed]`` table; raises ValueError naming ``file``."""
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise ValueError(f"{file}: [{TABLE}] must be a table of backends")
    found: dict[str, tuple[str, ...]] = {}
    for backend, models in table.items():
        if not _BACKEND.fullmatch(str(backend)):
            raise ValueError(f"{file}: [{TABLE}] {backend} is not a backend name")
        if not isinstance(models, list) or not models:
            raise ValueError(f"{file}: [{TABLE}] {backend} must list at least one model")
        for model in models:
            if not isinstance(model, str) or not _MODEL.fullmatch(model):
                raise ValueError(f"{file}: [{TABLE}] {backend} lists {model!r}, which is not a model name")
        if len(set(models)) != len(models):
            raise ValueError(f"{file}: [{TABLE}] {backend} lists a model twice")
        found[str(backend)] = tuple(models)
    return found


def lines(allowed: dict) -> list[str]:
    """The ``[allowed]`` table as TOML lines; none when no backend has a list."""
    if not allowed:
        return []
    return [f"[{TABLE}]", *(f"{backend} = {json.dumps(list(allowed[backend]))}" for backend in sorted(allowed)), ""]


def keep(rows, allowed):
    """``rows`` (dicts with ``model``) whose model is allowed; every row when ``allowed`` is None."""
    if allowed is None:
        return list(rows)
    names = set(allowed)
    return [row for row in rows if row.get("model") in names]


def checked(models, offered, *, label) -> tuple[str, ...]:
    """``models`` as a list to save, in the order given: each one the backend offers, none twice.

    ``offered`` is every model the backend's accounts serve. Raises ValueError otherwise.
    """
    picked = tuple(dict.fromkeys(models))
    if not picked:
        raise ValueError("name at least one model AutoCode may use")
    if len(picked) != len(models):
        raise ValueError("a model is named twice")
    listed = set(offered)
    missing = [model for model in picked if model not in listed]
    if missing:
        raise ValueError(
            f"{label} does not offer {', '.join(missing)} to your accounts; `autocode stages models` lists them"
        )
    return picked


def restrict_retry(config: dict, allowed) -> dict:
    """The Builder's retry policy with no stronger attempt on a model outside ``allowed``.

    The stronger attempt moves the Builder to ``strong_model`` and, when a checker runs that model,
    the checker to ``checker_model``; either outside the list means the attempt is not made, and
    ``not_allowed`` names the model, so the retry-limit pause says why. Updates ``config`` in place.
    """
    if allowed is None or not config.get("strong_model") or config.get("strong_origin") == "user_explicit":
        return config  # a --builder-strong-model the person typed stays exact
    names = set(allowed)
    for key in ("strong_model", "checker_model"):
        model = config.get(key)
        if model and not _listed(model, names):
            config.update(strong_model=None, **{NOT_ALLOWED: model})
            break
    return config


def available(listed, allowed):
    """The models a run may climb to: those its provider lists (None: it cannot list) that ``allowed`` holds.

    With no list from the provider, the allowed models themselves; with no allowed list, the provider's.
    """
    if allowed is None:
        return listed
    if listed is None:
        return list(allowed)
    names = set(allowed)
    return [model for model in listed if _listed(model, names)]


def _listed(model, names) -> bool:
    # A ladder rung or retry model may be provider-qualified ("openai/gpt-6-sol") where the Codex list holds
    # the bare name, as autocode_route_ladder.normalize_model reads them; another vendor's prefix still counts.
    return _qualified(model) in {_qualified(name) for name in names}


def _qualified(model: str) -> str:
    return model if "/" in model else f"openai/{model}"
