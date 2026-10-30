"""The terminal chat's /stages: the model and reasoning level each stage of a new chat runs.

The chat reads only `autocode stages list --json` (owner decision 2026-10-30, docs/task-run.md
#stage-choices-for-clients) and takes only a stage's own ``set.next_command``, with ``MODEL`` filled
from that stage's options and ``EFFORT`` from the chosen option's ``effort_choices``; an option the
view marks ``unavailable`` is shown with its reason and never taken. The full-screen chat shows the
stages in a drawer (autocode_chat_stages_panel), one list per stage like a dropdown: Up and Down move,
Enter opens a stage's models, then that model's levels, and saves the choice; Esc goes back. The
line-by-line chat prints the same list (``str(menu)``) and takes ``/stages STAGE N LEVEL``.

A choice applies to new chats: a chat already running keeps the routes it started with.

Client layer: imports nothing from AutoCode.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

LEVELS = ("stages", "models", "levels")
HINT = "Up/Down move · Enter chooses · Esc goes back"


def route(stage: dict) -> str:
    """What the stage runs now: the person's choice, else its default."""
    chosen = stage.get("chosen") or None
    if chosen:
        return f"{chosen.get('model')} at {chosen.get('effort') or 'its own default level'} (your choice)"
    default = stage.get("default") or {}
    if default.get("model") and not default.get("offered"):
        return "chosen from your accounts when a chat starts"
    return f"{default.get('model')} at {default.get('effort') or 'its own default level'} (default)"


def facts(option: dict) -> str:
    """A model's family, context window and account, as the view lists them."""
    found = [str(option["family"])] if option.get("family") else []
    if isinstance(option.get("context"), int):
        found.append(f"{option['context'] // 1000}k context")
    if option.get("account"):
        found.append(str(option["account"]))
    return "; ".join(found)


def fill(stage: dict, model: str, effort: str) -> list[str]:
    """The stage's named ``set`` command with the person's model and level, or ValueError.

    Only a model the stage's options list and the view does not mark unavailable, and only one of that
    model's ``effort_choices``, is ever filled in; the command must name each placeholder once.
    """
    option = next((row for row in stage.get("options") or [] if row.get("model") == model), None)
    if option is None:
        raise ValueError(f"The {stage.get('label')} is not offered {model}.")
    if option.get("unavailable"):
        raise ValueError(f"The {stage.get('label')} cannot run {model}: {option['unavailable']}.")
    if effort not in (option.get("effort_choices") or []):
        raise ValueError(f"{model} takes {', '.join(option.get('effort_choices') or [])}.")
    command = list((stage.get("set") or {}).get("next_command") or [])
    if command.count("MODEL") != 1 or command.count("EFFORT") != 1:
        raise ValueError("The stage view names no choice the chat can make; update AutoCode.")
    return [model if word == "MODEL" else effort if word == "EFFORT" else word for word in command]


def text(view: dict) -> str:
    """Every stage with what it does, the advice for it, what it runs and the numbered models."""
    lines = [f"Stages for new chats on {view.get('label') or view.get('backend')}:"]
    for stage in view.get("stages") or []:
        lines.append(f"{stage.get('label')}: {route(stage)}")
        lines.append(f"  {stage.get('does')}")
        lines.append(f"  Advice: {stage.get('advice')}")
        for number, option in enumerate(stage.get("options") or [], 1):
            why = f" (not available: {option['unavailable']})" if option.get("unavailable") else ""
            lines.append(f"  {number}) {option.get('model')} [{', '.join(option.get('effort_choices') or [])}]{why}")
    if view.get("options_stop"):
        lines.append(f"Models cannot be listed: {view['options_stop']}")
    lines.append(
        "Type /stages STAGE N LEVEL to choose, for example /stages builder 2 medium. Chats already started keep theirs."
    )
    return "\n".join(lines)


def find(view: dict, name: str) -> dict | None:
    """A stage by its id (``terra``), its label's first word (``builder``) or its label (``plan reviewer``)."""
    wanted = name.strip().lower().replace("-", " ").replace("_", " ")
    for stage in view.get("stages") or []:
        label = str(stage.get("label") or "").lower()
        if wanted in (str(stage.get("id")).replace("_", " "), label, label.split(" ")[0]):
            return stage
    return None


@dataclass
class Menu:
    """The /stages drawer: which list is open (stages, a stage's models, a model's levels) and where.

    ``save`` runs a filled ``set`` command and returns the core's updated view, or raises ValueError
    with the core's reason; the drawer then shows that reason and keeps its place.
    """

    view: dict
    save: Callable[[list[str]], dict]
    level: str = "stages"
    selected: int = 0
    stage: int = 0
    model: int = 0
    note: str = HINT

    def __str__(self) -> str:
        return text(self.view)

    def current(self) -> dict:
        return (self.view.get("stages") or [{}])[self.stage]

    def option(self) -> dict:
        return (self.current().get("options") or [{}])[self.model]

    def rows(self) -> list[tuple[str, bool]]:
        """The open list's lines, each with whether it can be chosen."""
        if self.level == "stages":
            return [(f"{stage.get('label')}: {route(stage)}", True) for stage in self.view.get("stages") or []]
        if self.level == "models":
            rows = []
            for option in self.current().get("options") or []:
                shown = f"{option.get('model')}  ({facts(option)})" if facts(option) else str(option.get("model"))
                if option.get("unavailable"):
                    shown += f"  not available: {option['unavailable']}"
                rows.append((shown, not option.get("unavailable")))
            return rows
        return [(level, True) for level in self.option().get("effort_choices") or []]

    def title(self) -> str:
        if self.level == "stages":
            return f"STAGES on {self.view.get('label') or self.view.get('backend')} — for new chats"
        if self.level == "models":
            return f"{self.current().get('label')}: choose its model"
        return f"{self.current().get('label')} on {self.option().get('model')}: choose its reasoning level"

    def detail(self) -> list[str]:
        """What the selected stage does and the advice for it, under the list."""
        stage = (self.view.get("stages") or [{}])[self.selected] if self.level == "stages" else self.current()
        return [str(stage.get("does") or ""), f"Advice: {stage.get('advice') or ''}"]

    def move(self, step: int) -> None:
        count = len(self.rows())
        self.selected = min(max(0, self.selected + step), max(0, count - 1))

    def enter(self) -> None:
        rows = self.rows()
        if not rows:
            self.note = self.view.get("options_stop") or "Nothing to choose here."
            return
        if not rows[self.selected][1]:
            reason = (self.current().get("options") or [{}])[self.selected].get("unavailable")
            self.note = f"Not available: {reason}."
            return
        if self.level == "stages":
            if not (self.view.get("stages") or [{}])[self.selected].get("options"):
                self.note = f"Models cannot be listed: {self.view.get('options_stop') or 'none are offered'}"
                return
            self.stage, self.level, self.note = self.selected, "models", HINT
            self.selected = self._now_model()
        elif self.level == "models":
            self.model, self.level, self.note = self.selected, "levels", HINT
            self.selected = self._now_level()
        else:
            self._save(rows[self.selected][0])

    def back(self) -> bool:
        """Esc: one list up; False when the stages list was open, so the drawer closes."""
        if self.level == "stages":
            return False
        self.level, self.selected = ("stages", self.stage) if self.level == "models" else ("models", self.model)
        self.note = HINT
        return True

    def _now_model(self) -> int:
        stage = self.current()
        now = (stage.get("chosen") or stage.get("default") or {}).get("model")
        options = stage.get("options") or []
        return next((number for number, row in enumerate(options) if row.get("model") == now), 0)

    def _now_level(self) -> int:
        stage, choices = self.current(), self.option().get("effort_choices") or []
        now = (stage.get("chosen") or stage.get("default") or {}).get("effort")
        return choices.index(now) if now in choices else 0

    def _save(self, effort: str) -> None:
        stage, model = self.current(), self.option().get("model")
        try:
            view = self.save(fill(stage, str(model), effort))
        except ValueError as error:
            self.note = str(error)
            return
        if not isinstance(view, dict) or not isinstance(view.get("stages"), list):
            self.note = "The choice was sent but no stage view came back; /stages reads it again."
            return
        self.view, self.level, self.selected = view, "stages", self.stage
        self.note = f"Saved: the {stage.get('label')} runs {model} at {effort} in new chats."
