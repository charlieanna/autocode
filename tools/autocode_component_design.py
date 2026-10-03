"""Design references supplied to one component's task, before implementation.

Accepted UI runs use the same public handoff validator as single-task builds.
Their content participates in the architecture's saved-build identity; a bare
Figma URL is a reference, without a claim that a design run accepted it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_figma as figma
except ImportError:
    import autocode_figma as figma


@dataclass(frozen=True)
class ComponentDesign:
    figma_file: str | None = None
    ui_run: Path | None = None

    @classmethod
    def load(cls, row: dict, architecture_dir: Path) -> ComponentDesign | None:
        supplied = {key: row[key] for key in ("figma_file", "ui_run") if row.get(key) is not None}
        if not supplied:
            return None
        if len(supplied) != 1:
            raise ValueError("choose figma_file or ui_run, not both")
        key, value = next(iter(supplied.items()))
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a nonempty string")
        if key == "figma_file":
            return cls(figma_file=figma.design_url(value))
        path = Path(value)
        design = cls(ui_run=(path if path.is_absolute() else architecture_dir / path).resolve())
        design.fingerprint()  # reject incomplete or changed accepted artifacts before any task starts
        return design

    def start_options(self) -> tuple[str, str]:
        if self.ui_run is not None:
            return ("--ui-run", str(self.ui_run))
        return ("--figma-file", self.figma_file)

    def fingerprint(self) -> str | None:
        if self.ui_run is None:
            return None  # the URL is already included in components.json
        handoff = figma.load_handoff(self.ui_run)
        content = json.dumps({"ui_run": str(self.ui_run), "handoff": handoff}, sort_keys=True).encode()
        return hashlib.sha256(content).hexdigest()


def validate_engine(options: tuple[str, ...]) -> None:
    """Match argparse's last explicit engine choice before dispatching any tasks."""
    engine = None
    for index, option in enumerate(options):
        if option == "--engine" and index + 1 < len(options):
            engine = options[index + 1]
        elif option.startswith("--engine="):
            engine = option.partition("=")[2]
    if engine not in (None, "codex"):
        raise ValueError("Figma implementation uses --engine codex; component designs cannot use " + engine)
