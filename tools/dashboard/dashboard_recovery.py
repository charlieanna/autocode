"""Run the exact CLI controls a freshly inspected recovery card names."""

from copy import deepcopy
from pathlib import Path

try:
    from .. import autocode_client_actions as client_actions
except ImportError:
    import autocode_client_actions as client_actions

# The dashboard binds the run and the inspected pause itself; a card cannot rebind them.
BOUND_FLAGS = {"--run-dir", "--workspace", "--expected-recovery-token"}


def projection(data):
    view = data.get("view") if isinstance(data, dict) else None
    card = view.get("recovery") if isinstance(view, dict) else None
    if isinstance(card, dict) and card.get("version") == 1:
        return deepcopy(card)
    if isinstance(data, dict) and data.get("stale") is True:
        # The supported status inspector established worker absence. Do not
        # invent a saved pause/token or offer execution against RUNNING state.
        check = bool(data.get("runner_check_workers")) and not data.get("active_stage")
        return {
            "version": 1,
            "token": None,
            "status": data.get("status"),
            "category": "recovery",
            "title": "Worker stopped before the task finished",
            "what_happened": (
                "The runner-owned test process was gone at the last status check."
                if check
                else "The recorded worker was gone at the last status check, with no completed report saved."
            ),
            "retained": "Saved work and attempt evidence remain available. The task has not been verified as complete.",
            "saved_reason": data.get("next_action"),
            "context": {
                "attempt": data.get("attempt_id"),
                "active_stage": data.get("active_stage"),
                "runner_check": view.get("runner_check") if isinstance(view, dict) else None,
            },
            "failure_groups": [],
            "actions": [
                {
                    "id": "inspect",
                    "kind": "inspect",
                    "label": "Inspect saved work",
                    "effect": "Read the saved attempt and checks. Worker ownership must be reconciled before any new execution.",
                },
                {
                    "id": "feedback",
                    "kind": "feedback",
                    "label": "Explain what should change",
                    "effect": "Draft corrective information in chat. This does not launch another worker.",
                },
            ],
        }
    return None


def command(card, selected):
    """Run the flags the runner's card names for this action (#730), never client arguments."""
    args = selected.get("args")
    if (
        not isinstance(args, list)
        or not args
        or any(not isinstance(word, str) or not word for word in args)
        or not args[0].startswith("--")
        or BOUND_FLAGS.intersection(word.split("=", 1)[0] for word in args)
    ):
        raise ValueError("This card action does not run a recovery command")
    return ["--no-chat", "--expected-recovery-token", card["token"], *args]


def number_command(view, workspace, run, input_id, value):
    """The flags that set the one number a pause leaves to a person (#2044), with what they typed.

    The number goes only into the command the status view names for it: the row's own ``command``
    when next_command is a plain resume or AutoResolver's request, otherwise next_command itself.
    The shared client rule validates the row, its bounds and the token binding; the dashboard adds no
    flag of its own. The recovery token returns to argv for enqueue to move into its private channel.
    """
    workspace, run = Path(workspace), Path(run)
    try:
        offer = client_actions.bound_offer(view, workspace, run)
        if offer:
            row, command = offer
            filled = client_actions.fill_offer(view, row, command, value)
        else:
            command = client_actions.named(view, workspace, run)
            rows = client_actions.integer_inputs(view, command)
            if not rows:
                raise client_actions.ClientGap("This pause offers no number to set")
            row = rows[0]
            filled = client_actions.fill(view, command, {row["id"]: value})
    except client_actions.ClientGap as error:
        raise ValueError(str(error)) from None
    if row["id"] != input_id:
        raise ValueError("The limit this pause offers changed. Refresh and review it before continuing.")
    argv = list(filled.argv)
    index = argv.index("--expected-recovery-token") + 1
    argv[index] = filled.env[client_actions.TOKEN_ENV]
    return ["--no-chat", *argv], row


class RecoveryActionsMixin:
    def mutate(self, data):
        if data.get("action") == "set_pause_number":
            return self._set_pause_number(data)
        if data.get("action") != "recover_pause":
            return super().mutate(data)
        workspace = self.workspace_for(data.get("workspace", ""))
        run = self.run_for(workspace, data.get("run", ""))
        if not run:
            raise ValueError("Run does not belong to an available project")
        status, error = self._json_command(["--workspace", str(workspace), "--run-dir", str(run), "--status"])
        if error:
            raise ValueError(error["message"])
        card = projection(status)
        if not card or not card.get("token") or card["token"] != data.get("recovery_token"):
            raise ValueError("The saved pause changed. Refresh and inspect the current recovery card.")
        selected = next(
            (item for item in card.get("actions", []) if item.get("id") == data.get("recovery_action")), None
        )
        if selected is None:
            raise ValueError("This recovery action is no longer offered. Refresh the current card.")
        extra = command(card, selected)
        self.status_cache.pop(str(run), None)
        return self.enqueue(workspace, run, selected["label"], extra)

    def _set_pause_number(self, data):
        """A number typed at a pause, taken through the command the fresh status view names for it."""
        workspace = self.workspace_for(data.get("workspace", ""))
        run = self.run_for(workspace, data.get("run", ""))
        if not run:
            raise ValueError("Run does not belong to an available project")
        value = data.get("value")
        if not isinstance(value, str):
            raise ValueError("Enter a whole number")
        status, error = self._json_command(["--workspace", str(workspace), "--run-dir", str(run), "--status"])
        if error:
            raise ValueError(error["message"])
        view = status.get("view") if isinstance(status, dict) else None
        token = (view.get("recovery") or {}).get("token") if isinstance(view, dict) else None
        if not token or token != data.get("recovery_token"):
            raise ValueError("The saved pause changed. Refresh and inspect the current recovery card.")
        extra, row = number_command(view, workspace, run, data.get("input_id"), value)
        self.status_cache.pop(str(run), None)
        return self.enqueue(workspace, run, "Set " + row["label"].lower(), extra)
