"""The model and reasoning level each stage runs, chosen by the person once and kept for every new task.

Owner decision 2026-10-30: at setup AutoCode asks which model and reasoning level each stage of a
task runs, says what the stage does and what kind of model and level suits it, and remembers the
answer. The person can change it at any time (``autocode stages``); every new task reads it before
it begins, and a task already running keeps the routes it started with.

The choices live in one file AutoCode writes, ``~/.config/autocode/stages.toml``, with one table per
backend, because a Claude model name means nothing to Codex::

    [claude]
    terra = { model = "claude-haiku-4-5-20251001", effort = "medium" }

A saved choice acts as the person typing that stage's ``--ROLE-model`` and
``--ROLE-reasoning-effort`` flags, so every check a typed flag gets still applies. A flag on the
command line and the project's own ``.autocode/config.toml`` win over it; ``--single-model`` leaves
every saved choice out of the run, and a shared ``--reasoning-effort`` leaves out the saved levels.

``offers`` lists what a stage may run on: for OpenCode and Kilo the models their connected accounts
serve, from the tools' own local listings (``autocode_route_discovery``), with the reasoning levels
and family each listing declares; for Claude Code and Codex the models AutoCode runs on them. A
model whose listing says it cannot call tools is left out, because every stage works through tools.
No price is read. An option that would let a checker run the model whose work it checks is marked
``unavailable`` with the reason, and ``choose`` refuses it, as a run's dispatch check would.

Layer: runtime helper over the provider facades and route discovery. It imports nothing from the
CLI, the controller or run configuration; ``autocode_stages_cli`` is its interface.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_providers as providers
    from . import autocode_roles as roles
    from . import autocode_route_discovery as route_discovery
    from . import autocode_route_fit as route_fit
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_providers as providers
    import autocode_roles as roles
    import autocode_route_discovery as route_discovery
    import autocode_route_fit as route_fit
    import autocode_verifier_independence as independence

# The stages a person chooses for, in the order a task runs them: (role, job, what it does, advice).
# The role is the run's route name, so its flags are --ROLE-model and --ROLE-reasoning-effort; the job
# is its screen name in autocode_roles. The Resolver's route starts as a copy of astra (docs/models.md), and
# the workflow jobs copy theirs from the stage that names them (autocode_role_transport.DERIVED_ROLES).
STAGES = (
    (
        "requirements",
        "requirements",
        "Turns your idea into requirements: reads the repository and asks you only about real ambiguity. "
        "It writes no code.",
        "A capable all-round model at medium. Clear questions matter more than deep reasoning here, "
        "and it runs once per task.",
    ),
    (
        "glm",
        "planner",
        "Drafts the plan: the tasks, their order and the test that proves each one, then revises it after review.",
        "A capable model at high. A sound plan saves Builder rounds, and it runs only a few times per task.",
    ),
    (
        "plan_reviewer",
        "plan_reviewer",
        "Challenges the plan before you see it and makes the final planning call. Its model also runs the "
        "Investigator that diagnoses a bug, the Analyst that answers a question, and the Designer and Design Reviewer.",
        "Your strongest judge at high. It never runs the Planner's model, so it catches what the Planner missed.",
    ),
    (
        "terra",
        "builder",
        "Writes the code for one task; several Builders run in parallel.",
        "A fast, cheaper model at medium. It makes the most calls, and the Tester checks everything it "
        "writes; a stuck Builder gets one stronger attempt on its own.",
    ),
    (
        "sol",
        "tester",
        "Checks each Builder's work in a separate session and proves its new tests fail without the change. "
        "Its model also writes the acceptance tests and runs the Code Reviewer when you ask for a review.",
        "A strong model at high, never the Builder's. A bug caught here costs less than one caught at completion.",
    ),
    (
        "completion",
        "completion",
        "Decides whether the whole task is done or sends it back for more work.",
        "A strong judge at medium, never the Builder's model. It weighs evidence rather than writing code, "
        "so medium is usually enough.",
    ),
    (
        "astra",
        "resolver",
        "Steps in when a stage stops making progress, and plans the steps inside each task.",
        "Your strongest model at high. It runs rarely, so it adds little cost, and it gets the hardest problems.",
    ),
)
ORDER = tuple(stage[0] for stage in STAGES)
LABELS = {
    role: (f"{roles.SCREEN[job]} and per-task {roles.SCREEN['planner']}" if role == "astra" else roles.SCREEN[job])
    for role, job, _does, _advice in STAGES
}
BACKEND_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kilocode": "Kilo"}
# Codex has no model listing. These are the bare GPT names AutoCode runs on it (autocode_configure
# DEFAULT_ROLE_MODELS, whose joint planning copies the astra route into Requirements and the Planner
# and gives the Plan Reviewer CODEX_PLAN_REVIEWER_MODELS[0]; tests keep them in step).
CODEX_MODELS = ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
CODEX_DEFAULTS = {role: "gpt-5.6-sol" for role in ORDER} | {"terra": "gpt-5.6-terra", "plan_reviewer": "gpt-6-astra"}
# The level a model with no reasoning variants takes: its own default, sent as no level at all.
OWN_DEFAULT = "default"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,199}$")
_BACKEND = re.compile(r"[a-z][a-z0-9_]{0,62}$")
HEADER = (
    "# The model and reasoning level each stage of a new task runs, per backend.\n"
    "# Written by `autocode stages` and `autocode setup`; change them there. A flag on the command line\n"
    "# or the project's .autocode/config.toml still wins for that run.\n"
)


def path() -> Path:
    """``stages.toml`` beside the user's ``config.toml`` (``$XDG_CONFIG_HOME/autocode``)."""
    return providers.user_config_path().with_name("stages.toml")


def load(file=None) -> dict:
    """The saved choices, ``{backend: {role: {"model", "effort"}}}``; empty when nothing is saved.

    Raises ValueError, naming the file, for a file that is not TOML or names a stage, model or level
    no flag would take, so a run never silently runs models the person did not choose.
    """
    file = Path(file) if file is not None else path()
    if not file.is_file():
        return {}
    try:
        table = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{file} cannot be read as TOML: {error}; fix it or run `autocode stages reset`") from error
    found: dict = {}
    for backend, stages in table.items():
        if not isinstance(stages, dict) or not _BACKEND.fullmatch(str(backend)):
            raise ValueError(f"{file}: [{backend}] must be a table of stages")
        for role, choice in stages.items():
            if role not in ORDER:
                raise ValueError(f"{file}: [{backend}] {role} is not a stage; the stages are {', '.join(ORDER)}")
            model = choice.get("model") if isinstance(choice, dict) else None
            effort = choice.get("effort") if isinstance(choice, dict) else None
            if not isinstance(model, str) or not _MODEL.fullmatch(model):
                raise ValueError(f"{file}: [{backend}] {role} needs a model name")
            if effort is not None and effort not in route_fit.SCALE:
                raise ValueError(f"{file}: [{backend}] {role} effort must be one of {', '.join(route_fit.SCALE)}")
            found.setdefault(backend, {})[role] = {"model": model, "effort": effort}
    return found


def save(choices: dict, file=None) -> Path:
    """Write every backend's choices at once, replacing the file atomically."""
    file = Path(file) if file is not None else path()
    lines = [HEADER]
    for backend in sorted(choices):
        stages = choices[backend]
        lines.append(f"[{backend}]")
        for role in ORDER:
            if role in stages:
                choice = stages[role]
                fields = [f"model = {json.dumps(choice['model'])}"]
                if choice.get("effort"):
                    fields.append(f"effort = {json.dumps(choice['effort'])}")
                lines.append(f"{role} = {{ {', '.join(fields)} }}")
        lines.append("")
    file.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=file.parent, prefix=".stages-", suffix=".toml")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines))
        os.replace(temporary, file)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return file


def offers(backend, workspace=None, *, resolve=None, discover=None) -> list[dict]:
    """The models a stage on ``backend`` may run, each with the levels it takes and its family.

    ``levels_listed`` says whether the levels come from the tool's own listing; otherwise they are
    AutoCode's scale and the tool's own check decides at launch. Raises RuntimeError or ValueError when
    the tool cannot list what its accounts serve, so a person is never offered a guess.
    """
    if backend == "codex":
        return [_option(model, None, account="ChatGPT (Codex login)", billing="subscription") for model in CODEX_MODELS]
    provider = (resolve or providers.resolve)(backend)
    if callable(getattr(provider, "auth_table", None)) or callable(getattr(provider, "auth_logins", None)):
        found = (discover or route_discovery.discover)(provider, workspace)
        rows = []
        for candidate in found["candidates"]:
            facts = candidate.get("metadata") or {}
            if facts.get("toolcall") is False:
                continue
            rows.append(
                _option(
                    candidate["model"],
                    candidate.get("levels"),
                    account=candidate.get("account"),
                    billing=candidate.get("billing"),
                    facts=facts,
                )
            )
        return rows
    listed = provider.available_models(workspace)
    if listed is None:
        raise RuntimeError(f"{BACKEND_LABELS.get(backend, backend)} cannot list its models")
    billing = "subscription" if backend == "claude" else None
    account = "Claude subscription" if backend == "claude" else None
    return [_option(model, None, account=account, billing=billing) for model in sorted(listed)]


def _option(model, levels, *, account=None, billing=None, facts=None) -> dict:
    facts = facts or {}
    return {
        "model": model,
        "levels": list(route_fit.SCALE) if levels is None else list(levels),
        "levels_listed": levels is not None,
        "family": facts.get("family") or model_identity.family(model) or None,
        "context": facts.get("context"),
        "account": account,
        "billing": billing,
    }


def defaults(backend, *, resolve=None) -> dict:
    """``{role: {"model", "effort"}}``: what a stage runs on ``backend`` when nobody chose."""
    if backend == "codex":
        # A Codex run takes the level in the person's own Codex config; None says so.
        return {role: {"model": CODEX_DEFAULTS[role], "effort": None} for role in ORDER}
    provider = (resolve or providers.resolve)(backend)
    models = getattr(provider, "DEFAULT_MODELS", None) or {}
    efforts = getattr(provider, "DEFAULT_REASONING_EFFORTS", None) or {}
    found = {}
    for role in ORDER:
        model = models.get(role) or (models.get("glm") if role == "requirements" else None)
        found[role] = {"model": model, "effort": efforts.get(role)}
    return found


def effort_choices(option) -> list[str]:
    """The levels a person may pick for ``option``, lowest first, or its own default when it lists none.

    A listing prints its variants in no useful order; a level outside the scale keeps its place after them.
    """
    rank = {level: number for number, level in enumerate(route_fit.SCALE)}
    return sorted(option["levels"], key=lambda level: rank.get(level, len(rank))) or [OWN_DEFAULT]


def unavailable(role, model, effective, *, backend) -> str | None:
    """Why ``role`` may not run ``model`` beside the other stages' models, or None.

    ``effective`` maps each other role to the model it will run (a choice, or a default the backend
    offers). A checker never runs the model whose work it checks (autocode_verifier_independence);
    on Claude Code and Codex, which reach one vendor, two different models of that vendor may.
    """
    vendor = backend in ("claude", "codex")
    checkers = []
    for producer, verifier, _label in independence.PAIRS:
        other = effective.get(producer if role == verifier else verifier)
        if role == verifier and other and independence.shares(other, model, vendor_engine=vendor):
            return (
                f"the {LABELS[verifier]} checks the {LABELS[producer]}'s work, and the {LABELS[producer]} runs {other}"
            )
        if role == producer and other and independence.shares(model, other, vendor_engine=vendor):
            checkers.append((LABELS[verifier], other))
    if len(checkers) == 1:
        ((label, other),) = checkers
        return f"the {label} checks this stage's work and runs {other}; change the {label} too"
    if checkers:
        # Every checker that clashes is named, so changing the one named first does not leave another.
        runs = {other for _label, other in checkers}
        if len(runs) == 1:
            first, second = (label for label, _other in checkers)
            return f"the {first} and the {second} check this stage's work and run {runs.pop()}; change both too"
        named = " and ".join(f"the {label} (runs {other})" for label, other in checkers)
        return f"{named} check this stage's work; change both too"
    return None


def view(backend, workspace=None, *, file=None, resolve=None, discover=None) -> dict:
    """The stage list a client renders as one dropdown per stage, and the commands that change it.

    Each stage names what it does, advice on the model and level that suit it, the default, the saved
    choice (or None), its options and two argument arrays following ``autocode``: ``set.next_command``,
    whose ``MODEL`` a client fills with one of ``options[].model`` and ``EFFORT`` with one of that
    option's ``effort_choices``, refusing anything else, and ``reset.next_command`` while a choice is
    saved. ``options_stop`` says why no options could be listed.
    """
    file, saved, fallback, options, stop = _read(backend, workspace, file, resolve, discover)
    effective = _effective(saved, fallback, options)
    stages = []
    for role, _job, does, advice in STAGES:
        others = {other: model for other, model in effective.items() if other != role}
        rows = [
            {
                **option,
                "effort_choices": effort_choices(option),
                "unavailable": unavailable(role, option["model"], others, backend=backend),
            }
            for option in options
        ]
        default = dict(fallback[role])
        offered = next((row for row in rows if row["model"] == default["model"]), None)
        if offered is not None and default["effort"] and offered["levels_listed"]:
            # A default level the model does not declare runs at the nearest one it does (autocode_route_fit).
            default["effort"] = route_fit.nearest(default["effort"], offered["levels"]) or default["effort"]
        stages.append(
            {
                "id": role,
                "label": LABELS[role],
                "does": does,
                "advice": advice,
                "flag": f"--{role.replace('_', '-')}-model",
                "effort_flag": f"--{role.replace('_', '-')}-reasoning-effort",
                "default": {**default, "offered": offered is not None},
                "chosen": saved.get(role),
                "options": rows,
                "set": {"next_command": _command("set", role, backend) + ["--model", "MODEL", "--effort", "EFFORT"]},
                "reset": {"next_command": _command("reset", role, backend)} if role in saved else None,
            }
        )
    return {
        "version": 1,
        "backend": backend,
        "label": BACKEND_LABELS.get(backend, backend),
        "file": str(file),
        "options_stop": stop,
        "stages": stages,
    }


def _read(backend, workspace, file, resolve, discover):
    file = Path(file) if file is not None else path()
    saved = load(file).get(backend, {})
    fallback = defaults(backend, resolve=resolve)
    try:
        options, stop = offers(backend, workspace, resolve=resolve, discover=discover), None
    except (RuntimeError, ValueError, OSError) as error:
        options, stop = [], str(error)
    return file, saved, fallback, options, stop


def _command(action, role, backend):
    return ["stages", action, role, "--backend", backend, "--json"]


def _effective(saved, fallback, options) -> dict:
    """The model each role will run: the saved choice, else a default the backend offers.

    A default the backend does not offer is left out: automatic selection replaces it with an
    independent route, so it constrains nothing.
    """
    listed = {option["model"] for option in options}
    found = {}
    for role in ORDER:
        if role in saved:
            found[role] = saved[role]["model"]
        elif fallback[role]["model"] in listed:
            found[role] = fallback[role]["model"]
    return found


def choose(backend, picks, workspace=None, *, file=None, resolve=None, discover=None) -> dict:
    """Save ``picks`` (``{role: (model, effort)}``) on ``backend`` at once; return the updated view.

    Each pick is checked against the backend's own offer and against the other stages as they will
    stand once every pick is saved, so a Builder and a Tester can be changed together. Refuses
    (ValueError) a stage, model or level the view does not offer, or a model a stage's checker or
    producer runs; nothing is saved then. ``effort`` is one of the option's ``effort_choices``, and
    the model's own default is saved as no level.
    """
    for role in picks:
        if role not in ORDER:
            raise ValueError(f"{role} is not a stage; the stages are {', '.join(ORDER)}")
    file, saved, fallback, options, stop = _read(backend, workspace, file, resolve, discover)
    label = BACKEND_LABELS.get(backend, backend)
    if stop:
        raise ValueError(f"cannot list {label}'s models: {stop}")
    by_model = {option["model"]: option for option in options}
    after = {**saved, **{role: {"model": model, "effort": effort} for role, (model, effort) in picks.items()}}
    effective = _effective(after, fallback, options)
    for role, (model, effort) in picks.items():
        option = by_model.get(model)
        if option is None:
            raise ValueError(f"{label} does not offer {model} to your accounts; `autocode stages` lists them")
        others = {other: chosen for other, chosen in effective.items() if other != role}
        reason = unavailable(role, model, others, backend=backend)
        if reason:
            raise ValueError(f"the {LABELS[role]} cannot run {model}: {reason}")
        if effort not in effort_choices(option):
            raise ValueError(f"{model} takes {', '.join(effort_choices(option))}, not {effort}")
    every = load(file)
    for role, (model, effort) in picks.items():
        every.setdefault(backend, {})[role] = {"model": model, "effort": None if effort == OWN_DEFAULT else effort}
    save(every, file)
    return view(backend, workspace, file=file, resolve=resolve, discover=discover)


def reset(backend, role=None, workspace=None, *, file=None, resolve=None, discover=None) -> dict:
    """Forget ``role``'s choice on ``backend`` (every stage's when ``role`` is None); return the view."""
    if role is not None and role not in ORDER:
        raise ValueError(f"{role} is not a stage; the stages are {', '.join(ORDER)}")
    try:
        every = load(file)
    except ValueError:
        if role is not None:
            raise
        every = {}  # resetting a backend repairs an unreadable file rather than refusing to touch it
    stages = every.get(backend, {})
    for name in ORDER if role is None else (role,):
        stages.pop(name, None)
    if not stages:
        every.pop(backend, None)
    save(every, file)
    return view(backend, workspace, file=file, resolve=resolve, discover=discover)


def apply(args, backend, *, file=None, say=None) -> list[str]:
    """Give a new run the person's saved choices for ``backend``, in place on ``args``; return the roles.

    A stage whose model a flag or the project's file already set, or whose route another tool serves
    (``--ROLE-provider``), keeps that. ``--single-model`` and a replay take no saved choice, and a shared
    ``--reasoning-effort`` keeps every saved level out. One line names the file and the stages taken.
    """
    if getattr(args, "single_model", None) or getattr(args, "replay_recording", None):
        return []
    file = Path(file) if file is not None else path()
    saved = load(file).get(backend, {})
    shared_effort = getattr(args, "reasoning_effort", None) is not None
    taken = []
    for role in ORDER:
        choice = saved.get(role)
        if not choice or getattr(args, f"{role}_model", None) or getattr(args, f"{role}_provider", None):
            continue
        setattr(args, f"{role}_model", choice["model"])
        if choice["effort"] and not shared_effort and getattr(args, f"{role}_reasoning_effort", None) is None:
            setattr(args, f"{role}_reasoning_effort", choice["effort"])
        taken.append(role)
    if taken and say is not None:
        say(f"Stage choices from {file}: {', '.join(LABELS[role] for role in taken)}")
    return taken
