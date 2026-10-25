"""Reading a status view's named actions, for a client that shows them to a person.

A human-facing client (the terminal chat, the VS Code extension, the browser
dashboard) takes only the action the status view names: ``view["next_command"]``,
the optional ``view["chat"]["input"]`` message offer and the optional
``view["pause"]["next_command"]`` (docs/task-run.md), and a saved code step's
``compare`` and ``restore`` actions (``view["code_checkpoints"]``). It fills only the decisions
the view leaves to a person: a ``QUESTION=ANSWER`` or ``QUESTION=MODEL`` slot, a
``QUESTION=EFFORT`` slot limited to the levels ``needs.effort`` declares, or the one
typed number a recovery offers. A command the client cannot take from the
view alone is a ``ClientGap``, to fix in the CLI or the view, never to work around
in the client. These are the rules ``vscode-extension/src/core/next-command.ts``
and the headless conformance client (``scenarios/harness/conformance.py``) follow;
the three must agree, so a person sees the same choices in every client.

Pure functions over the view's JSON. Standard library only; nothing here imports
AutoCode, reads ``state.json`` or launches a process.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# Placeholders next_command leaves open for a decision the view does not supply (autocode_pause_command).
UNSUPPLIED = ("GOAL_FILE", "N", "WHAT CHANGED", "TOKEN", "RUN_DIR", "--explain")
# Flags that continue the run.
ADVANCING = ("--no-chat", "--resume-paused")
# Needs that are an honest stop: a person decides, a client may not answer for them.
PERSON_ONLY_KINDS = ("resume", "retry_job", "recover_source")
# A person's words for ``QUESTION=PLACEHOLDER`` in a named command, and the decision each is.
PLACEHOLDERS = {"ANSWER": "answer", "MODEL": "model", "EFFORT": "effort"}
# Resolver scopes that stop the run because it could not continue safely.
PERSON_ONLY_SCOPES = ("blocker", "operational_exhaustion")
MAX_SAFE_INTEGER = 9007199254740991
ENV_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


class ClientGap(RuntimeError):
    """The view names no action this client can take: a gap in the CLI or the status view."""


def attention_acknowledgement(value: dict, workspace: Path, run_dir: Path):
    """Only the core's exact acknowledgement array, never a task answer or resume."""
    if not isinstance(value, dict):
        raise ClientGap("The core offers no valid attention metadata")
    item = value.get("item") or {}
    offer = value.get("acknowledge") or {}
    receipt = value.get("acknowledgement") or {}
    if not all(isinstance(part, dict) for part in (item, offer, receipt)):
        raise ClientGap("The core offers no valid attention metadata")
    identity = item.get("id")
    offered = offer.get("next_command")
    wanted = [
        "autocode",
        "attention",
        "ack",
        "--workspace",
        str(workspace),
        "--run-dir",
        str(run_dir),
        "--item-id",
        identity,
    ]
    if (
        type(value.get("version")) is not int
        or value.get("version") != 1
        or receipt.get("available") is not True
        or not isinstance(identity, str)
        or re.fullmatch(r"[0-9a-f]{64}", identity) is None
        or item.get("workspace") != str(workspace)
        or item.get("run_dir") != str(run_dir)
        or offered != wanted
    ):
        raise ClientGap("The core offers no acknowledgement for that exact alert/run")
    return NamedCommand(list(offered[1:]), {})


@dataclass(frozen=True)
class NamedCommand:
    """A named action as arguments after ``autocode``, with its issued tokens kept out of argv."""

    argv: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class Decision:
    """A slot the named command leaves for a person to fill."""

    key: str  # the question id, or the typed input's id
    kind: str  # "answer", "model", "effort" or "integer"
    question: dict | None = None  # the needs.questions row an answer slot names
    integer: dict | None = None  # the needs.command_inputs row an integer slot names


def own(view: dict, workspace: Path, run_dir: Path) -> tuple[Path, Path]:
    """The run's effective paths: a chat started in a plain folder runs in the project AutoCode bootstrapped there."""
    return Path(view.get("workspace") or workspace), Path(view.get("run_dir") or run_dir)


def worker_transport(execution: dict, argv: list[str], workspace: Path, run_dir: Path, *, fresh=False) -> list[str]:
    """Take only the core's exact worker envelope, keeping the named task action literal."""
    prefix = execution.get("next_command")
    if fresh:
        if execution.get("id") != "durable_local" or execution.get("available") is not True:
            raise ClientGap("Durable execution is unavailable on this host")
        wanted = [
            "autocode",
            "worker",
            "launch",
            "--workspace",
            "WORKSPACE",
            "--run-dir",
            "RUN_DIR",
            "--checkpoint",
            "CHECKPOINT",
            "--",
        ]
        values = {"WORKSPACE": str(workspace), "RUN_DIR": str(run_dir), "CHECKPOINT": "-"}
    else:
        if execution.get("mode") != "durable_local" or execution.get("detach_supported") is not True:
            raise ClientGap("This run does not advertise durable worker execution")
        token = prefix[8] if isinstance(prefix, list) and len(prefix) == 10 else None
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ClientGap("The worker action has no current checkpoint token")
        wanted = [
            "autocode",
            "worker",
            "launch",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run_dir),
            "--checkpoint",
            token,
            "--",
        ]
        values = {}
    if prefix != wanted:
        raise ClientGap("Unsupported or cross-run durable worker action")
    return [values.get(word, word) for word in prefix[1:]] + list(argv)


def parse(text: str, workspace: Path, run_dir: Path) -> NamedCommand:
    """A command the view formatted for a person, as words to run without a shell.

    ``--workspace`` and ``--run-dir`` come off: the caller names its own run, and a
    command pointing at another run is a gap, not a command to run against this one.
    """
    try:
        words = shlex.split(text)
    except ValueError as error:
        raise ClientGap(f"cannot read the named command {text!r}: {error}") from None
    env = {}
    while words and words[0] != "autocode" and ENV_WORD.match(words[0]):
        name, value = words.pop(0).split("=", 1)
        env[name] = value
    if words[:1] != ["autocode"]:
        raise ClientGap(f"the named command does not start with autocode: {text!r}")
    argv = words[1:]
    for flag, expected in (("--run-dir", run_dir), ("--workspace", workspace)):
        if flag not in argv:
            continue
        index = argv.index(flag)
        if index + 1 >= len(argv):
            raise ClientGap(f"{flag} has no value in the named command")
        named = argv[index + 1]
        del argv[index : index + 2]
        if Path(named).resolve() != Path(expected).resolve():
            raise ClientGap(f"the named command names {flag} {named}, not this run's {expected}")
    return NamedCommand(argv, env)


def named(view: dict, workspace: Path, run_dir: Path) -> NamedCommand:
    """``view["next_command"]``; an active run's ``continue`` need has none, and its action is ``--no-chat``."""
    text = view.get("next_command")
    if text:
        return parse(text, *own(view, workspace, run_dir))
    if (view.get("needs") or {}).get("kind") == "continue":
        return NamedCommand(["--no-chat"], {})
    raise ClientGap(f"needs {(view.get('needs') or {}).get('kind')!r} at {view.get('status')!r} names no next_command")


# The numbers a person may type into a core-named command (autocode_command_inputs, #2044): the flag,
# the row id and label the core gives it, the key `used` is reported under, and whether the saved
# current value can be fractional. A row for any other flag is refused, as is one that disagrees here.
INTEGER_INPUTS = {
    "--max-seconds": ("max_seconds", "Total active time (seconds)", "elapsed", True),
    "--max-cost-usd": ("max_cost_usd", "Cost cap (whole USD)", "used", True),
    "--max-iterations": ("max_iterations", "Total iterations", "used", False),
    "--max-milestone-seconds": ("max_milestone_seconds", "Milestone active time (seconds)", "used", True),
    "--max-milestone-replans": ("max_milestone_replans", "Milestone replans", "used", False),
    "--no-progress-limit": ("no_progress_limit", "Unchanged implementation batches allowed", "used", False),
    "--planning-review-call-limit": ("planning_review_call_limit", "Total plan-review calls", "used", False),
    "--grant-recovery": ("grant_recovery", "Additional recoveries", "used", False),
}
TOKEN_ENV = "AUTOCODE_EXPECTED_RECOVERY_TOKEN"


def _count(value, *, fractional):
    """A saved count the core reported: a finite nonnegative safe number, whole unless ``fractional``."""
    if type(value) is int:
        return 0 <= value < MAX_SAFE_INTEGER
    return fractional and type(value) is float and value == value and 0 <= value < MAX_SAFE_INTEGER


def check_input(view: dict, row, command: NamedCommand) -> dict:
    """``row`` when it is a closed number offer for exactly ``command``; otherwise a ClientGap.

    The command must be ``[--resume-paused] FLAG N --expected-recovery-token -`` with the view's
    current recovery token in its private channel, and the bounds must be the ones the saved
    counts imply, so a client can never type a number into a command the core did not offer it for.
    """
    need = view.get("needs") or {}
    token = (view.get("recovery") or {}).get("token")
    flag = row.get("flag") if isinstance(row, dict) else None
    known = INTEGER_INPUTS.get(flag) if isinstance(flag, str) else None
    if known is None:
        raise ClientGap("Unsupported or stale numeric recovery command from AutoCode")
    identity, label, used_key, fractional = known
    flag, current, used = row["flag"], row.get("current"), row.get(used_key)
    prefix = ["--resume-paused"] if row.get("arg_index") == 2 else []
    minimum, maximum = row.get("minimum"), row.get("maximum")
    bounded = type(minimum) is int and type(maximum) is int and 1 <= minimum <= maximum <= MAX_SAFE_INTEGER
    if flag == "--grant-recovery":
        limits = bounded and type(used) is int and used >= 1 and minimum == 1 and maximum == used
    else:
        limits = (
            bounded
            and _count(used, fractional=fractional)
            and maximum == MAX_SAFE_INTEGER
            and _count(current, fractional=fractional and flag != "--max-seconds")
            and minimum == int(max(current, used)) + 1
        )
    if (
        not limits
        or row.get("id") != identity
        or row.get("label") != label
        or row.get("kind") != "integer"
        or row.get("placeholder") != "N"
        or row.get("arg_index") not in ((1, 2) if flag == "--planning-review-call-limit" else (2,))
        or not _count(current, fractional=fractional and flag != "--max-seconds")
        or (current == 0 and flag != "--max-iterations")
        or need.get("new_run_required")
        or need.get("edit_required")
        or not isinstance(token, str)
        or not token
        or command.env != {TOKEN_ENV: token}
        or command.argv != [*prefix, flag, "N", "--expected-recovery-token", "-"]
    ):
        raise ClientGap("Unsupported or stale numeric recovery command from AutoCode")
    return row


def _offered(view: dict):
    rows = (view.get("needs") or {}).get("command_inputs")
    if rows is None:
        return None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ClientGap("Unsupported or stale numeric recovery command from AutoCode")
    return rows[0]


def integer_inputs(view: dict, command: NamedCommand) -> list[dict]:
    """The one closed number ``command`` (the view's next_command) leaves open, or none.

    A row that names its own ``command`` is a separate offer (bound_offer), not a slot of this one.
    """
    row = _offered(view)
    if row is None or "command" in row:
        return []
    return [check_input(view, row, command)]


def bound_offer(view: dict, workspace: Path, run_dir: Path) -> tuple[dict, NamedCommand] | None:
    """The raise of the bound holding the run when next_command is not that raise (#2044), or None.

    At a plain resume or an operational request for a bound pause, the core names the raise as the
    row's own ``command``: a person's number goes there, never into the plain resume or a reply.
    """
    row = _offered(view)
    if row is None or "command" not in row:
        return None
    if not isinstance(row["command"], str) or not row["command"]:
        raise ClientGap("Unsupported or stale numeric recovery command from AutoCode")
    command = parse(row["command"], *own(view, workspace, run_dir))
    return check_input(view, row, command), command


def fill_offer(view: dict, row: dict, command: NamedCommand, text: str) -> NamedCommand:
    """A separate bound offer's command with the person's number in its one slot."""
    check_input(view, row, command)
    argv = list(command.argv)
    argv[row["arg_index"]] = integer_value(text, row)
    return NamedCommand(argv, dict(command.env))


def decisions(view: dict, command: NamedCommand) -> list[Decision]:
    """The slots a person fills before the command can run, in order."""
    questions = {row.get("id"): row for row in (view.get("needs") or {}).get("questions") or []}
    found = [Decision(row["id"], "integer", integer=row) for row in integer_inputs(view, command)]
    for word in command.argv:
        key, separator, placeholder = word.partition("=")
        if separator and placeholder in PLACEHOLDERS:
            found.append(Decision(key, PLACEHOLDERS[placeholder], question=questions.get(key)))
    return found


def effort_levels(view: dict, key: str) -> list[str]:
    """The levels an ``effort-ROLE=EFFORT`` slot may take: the ones its model declares (#2057)."""
    effort = (view.get("needs") or {}).get("effort") or {}
    declared = effort.get("declared")
    if (
        effort.get("question_id") != key
        or not isinstance(declared, list)
        or not declared
        or not all(isinstance(level, str) and level for level in declared)
    ):
        raise ClientGap(f"{key} asks for an effort the status view does not describe")
    return list(declared)


def effort_value(view: dict, key: str, text: str) -> str:
    """A level a person chose for ``key``, one its model declares."""
    levels = effort_levels(view, key)
    chosen = text.strip().lower()
    if chosen not in levels:
        raise ClientGap(f"Choose one of {', '.join(levels)}")
    return chosen


def unsupplied(view: dict, command: NamedCommand) -> str | None:
    """The first placeholder the view cannot supply, or None. Never the client's to fill."""
    typed = {row["arg_index"] for row in integer_inputs(view, command)}
    return next((word for index, word in enumerate(command.argv) if word in UNSUPPLIED and index not in typed), None)


def integer_value(text: str, row: dict) -> str:
    """Digits a person typed, within the advertised bounds."""
    text = text.strip()
    if not re.fullmatch(r"[0-9]+", text) or len(text.lstrip("0")) > 16:
        raise ClientGap(f"Enter a whole number from {row['minimum']} to {row['maximum']} for {row['label']}")
    number = int(text)
    if not row["minimum"] <= number <= row["maximum"]:
        raise ClientGap(f"Enter a whole number from {row['minimum']} to {row['maximum']} for {row['label']}")
    return str(number)


def fill(view: dict, command: NamedCommand, answers: dict[str, str]) -> NamedCommand:
    """The command with a person's decisions in its slots; an unanswered or unsupplied slot is refused."""
    inputs = {row["arg_index"]: row for row in integer_inputs(view, command)}
    filled = []
    for index, word in enumerate(command.argv):
        if index in inputs:
            row = inputs[index]
            if row["id"] not in answers:
                raise ClientGap(f"{row['label']} is a decision for a person; none was given")
            filled.append(integer_value(answers[row["id"]], row))
            continue
        if word in UNSUPPLIED:
            raise ClientGap(f"the command leaves {word} open, which the status view does not supply")
        key, separator, placeholder = word.partition("=")
        if separator and placeholder in PLACEHOLDERS:
            if key not in answers:
                raise ClientGap(f"{key} is a decision for a person; none was given")
            value = effort_value(view, key, answers[key]) if placeholder == "EFFORT" else answers[key]
            word = f"{key}={value}"
        filled.append(word)
    return NamedCommand(filled, dict(command.env))


def advancing(command: NamedCommand) -> bool:
    """Whether the command continues the run (and so can run for as long as the work does)."""
    return any(word in ADVANCING for word in command.argv)


def leaves_for_person(need: dict | None) -> bool:
    """Whether this need is an honest stop only a person decides; a client asks before taking it."""
    if not need:
        return False
    if need.get("kind") in PERSON_ONLY_KINDS:
        return True
    return need.get("kind") == "answer" and need.get("resolver_scope") in PERSON_ONLY_SCOPES


def working(view: dict) -> bool:
    """A stage is running, or a live controller is advancing a boundary that needs nobody."""
    controller = view.get("controller") or {}
    return (view.get("liveness") or {}).get("kind") in ("supervised", "unsupervised") or (
        not view.get("done")
        and (view.get("needs") or {}).get("kind") == "continue"
        and controller.get("kind") == "running"
        and controller.get("checked") is True
        and controller.get("alive") is True
    )


def approval_token(view: dict) -> str | None:
    """The token an approval is bound to, while the run asks for one."""
    need = view.get("needs") or {}
    return need.get("token") if need.get("kind") in ("approve_plan", "review") else None


def plan_for_approval(view: dict) -> dict | None:
    """``displayed_plan`` when it is the complete plan the current token approves, else None.

    A matching token is not enough if the plan's terms are missing or malformed:
    a client must not ask a person to approve terms it cannot show.
    """
    plan = view.get("displayed_plan") or {}
    token = approval_token(view)
    if not token or plan.get("token") != token:
        return None
    criteria = plan.get("acceptance_criteria")
    if not isinstance(criteria, list) or not all(
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("criterion"), str)
        and isinstance(row.get("verification_method"), str)
        and isinstance(row.get("human_review"), bool)
        for row in criteria
    ):
        return None
    if not all(
        isinstance(rows, list) and all(isinstance(text, str) for text in rows)
        for rows in (plan.get("constraints"), plan.get("permission_boundaries"))
    ):
        return None
    return plan


def review_criteria(view: dict) -> list[dict] | None:
    """The criteria a review need asks a person to accept, each with its text, or None.

    A row names its criterion by id (or carries its text); the text comes from the
    approved plan. A criterion whose text the view does not carry makes the whole
    review unshowable, as an incomplete plan is for approval.
    """
    need = view.get("needs") or {}
    contract = (view.get("approved_contract") or {}).get("body") or {}
    planned = contract.get("acceptance_criteria") or (view.get("displayed_plan") or {}).get("acceptance_criteria")
    known = {
        row["id"]: row["criterion"]
        for row in planned or []
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("criterion"), str)
    }
    rows = need.get("criteria")
    if need.get("kind") != "review" or not isinstance(rows, list) or not rows:
        return None
    shown = []
    for row in rows:
        key = row.get("id") if isinstance(row, dict) else row
        text = row.get("criterion") if isinstance(row, dict) and isinstance(row.get("criterion"), str) else None
        text = text or known.get(key)
        if not isinstance(key, str) or not text:
            return None
        shown.append({"id": key, "criterion": text})
    return shown


def message(view: dict, text: str, shown_context: str, workspace: Path, run_dir: Path) -> NamedCommand:
    """A person's message through the view's chat input offer, bound to the conversation they read.

    Only the advertised text is filled in. A Resolver reply carries its request's
    binding and cannot grant a retry or release a pause on its own.
    """
    chat = view.get("chat") or {}
    offered = chat.get("input") or {}
    if not text.strip():
        raise ClientGap("Enter a message first")
    if not offered.get("next_command"):
        raise ClientGap("The view names no message action at this checkpoint")
    if chat.get("context") != shown_context:
        raise ClientGap("This conversation changed; review its current state before sending")
    command = parse(offered["next_command"], *own(view, workspace, run_dir))
    need = view.get("needs") or {}
    if offered.get("kind") == "resolver_response":
        expected = [
            "--resolver-request",
            need.get("resolver_request_id"),
            "--resolver-token",
            "-",
            "--resolver-response",
            "provide_information",
            "--resolver-message",
            "MESSAGE",
        ]
        if (
            need.get("kind") == "answer"
            and need.get("resolver_scope") in PERSON_ONLY_SCOPES
            and not need.get("route")
            and need.get("resolver_request_id")
            and need.get("resolver_token")
            and command.argv == expected
            and command.env == {"AUTOCODE_RESOLVER_TOKEN": need["resolver_token"]}
        ):
            return NamedCommand([*command.argv[:-1], text], command.env)
        raise ClientGap("Unsupported message action from AutoCode")
    flag = {"feedback": "--feedback", "follow_up": "--follow-up"}.get(str(offered.get("kind")))
    if not flag or command.argv != [flag, "MESSAGE"] or command.env:
        raise ClientGap("Unsupported message action from AutoCode")
    return NamedCommand([flag, text], {})


def pause(view: dict, request_id: str, workspace: Path, run_dir: Path) -> list[str]:
    """The pause the view offers for a working run (#1598), as arguments after ``autocode``, or a gap."""
    offer = view.get("pause") or {}
    command = offer.get("next_command")
    if not offer or offer.get("requested") or not isinstance(command, list):
        raise ClientGap("AutoCode offers no pause for this run right now")
    workspace, run_dir = own(view, workspace, run_dir)
    expected = [
        "intervention",
        "submit",
        "--workspace",
        str(workspace),
        "--run-dir",
        str(run_dir),
        "--request-id",
        "REQUEST_ID",
        "--kind",
        "pause",
        "--json",
    ]
    if command != expected:
        raise ClientGap("Unsupported pause action from AutoCode")
    return [request_id if word == "REQUEST_ID" else word for word in command]


def checkpoint(view: dict, checkpoint_id: str, action_id: str, workspace: Path, run_dir: Path) -> list[str]:
    """A saved code step's named ``compare`` or ``restore``, as arguments after ``autocode``, or a gap.

    The view names ``restore`` only where going back is allowed now; it carries ``REQUEST_ID``,
    which the caller replaces with its own stable id, and reads the token a fresh ``compare``
    returned from ``AUTOCODE_CHECKPOINT_EXPECTED_TOKEN`` (docs/task-run.md#saved-code-checkpoints).
    """
    rows = (view.get("code_checkpoints") or {}).get("rows") or []
    row = next((row for row in rows if isinstance(row, dict) and row.get("id") == checkpoint_id), None)
    action = next(
        (item for item in (row or {}).get("actions") or [] if isinstance(item, dict) and item.get("id") == action_id),
        None,
    )
    if action is None:
        raise ClientGap(f"AutoCode offers no {action_id} for that saved step right now")
    workspace, run_dir = own(view, workspace, run_dir)
    expected = [
        "checkpoint",
        "--workspace",
        str(workspace),
        "--run-dir",
        str(run_dir),
        "--compare" if action_id == "compare" else "--restore",
        checkpoint_id,
        *(["--expected-token", "-", "--request-id", "REQUEST_ID"] if action_id == "restore" else []),
    ]
    if action.get("next_command") != expected:
        raise ClientGap(f"Unsupported {action_id} action from AutoCode")
    return list(expected)


def live_diff(view: dict, workspace: Path, run_dir: Path) -> list[str]:
    """The view's named, display-only ``working`` diff of the running Builder's changes, or a gap."""
    action = ((view.get("code_checkpoints") or {}).get("working")) or {}
    workspace, run_dir = own(view, workspace, run_dir)
    expected = ["checkpoint", "--workspace", str(workspace), "--run-dir", str(run_dir), "--working"]
    if not action:
        raise ClientGap("AutoCode offers no live diff right now. It is offered while the Builder works")
    if action.get("next_command") != expected:
        raise ClientGap("Unsupported working-diff action from AutoCode")
    return expected
