"""Validated Figma references and accepted UI-to-implementation handoffs."""

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import json
from pathlib import Path
from urllib.parse import urlparse

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def design_url(value):
    parsed = urlparse(value)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("figma.com", "www.figma.com")
        or len(parts) < 2
        or parts[0] != "design"
        or not parts[1].isalnum()
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Expected an https://www.figma.com/design/<file-key> URL")
    return value


def load_handoff(run_dir):
    root = Path(run_dir).resolve()
    state = json.loads((root / "state.json").read_text())
    handoff = json.loads((root / "handoff.json").read_text())
    version = handoff.get("version")
    if state.get("status") != "COMPLETE" or version not in (1, 2):
        raise ValueError("Only a completed, accepted Autocode UI run can be built")
    design_url(handoff["figma_file"])
    refs = handoff["artifacts"]
    required = (
        ("brief", "terra", "sol", "astra")
        if version == 1
        else (
            "requirements_draft",
            "plan_reviewer",
            "brief",
            "plan_finalizer",
            "builder",
            "validator",
            "decision_owner",
        )
    )
    for role in required:
        ref = refs[role]
        path = root / ref["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("UI handoff artifact is missing or outside the run")
        if util.file_hash(path) != ref["sha256"]:
            raise ValueError("UI handoff changed after acceptance; rerun its review")
    execution = (
        (("terra", "COMPLETE"), ("sol", "PASS"), ("astra", "ACCEPT"))
        if version == 1
        else (("builder", "COMPLETE"), ("validator", "PASS"), ("decision_owner", "ACCEPT"))
    )
    if version == 2:
        for role, allowed in (("plan_reviewer", ("PASS", "REVISE")), ("plan_finalizer", ("ACCEPT",))):
            report = json.loads((root / refs[role]["path"]).read_text())
            if (
                report.get("status") not in allowed
                or not report.get("evidence")
                or (role == "plan_finalizer" and report.get("required_changes"))
            ):
                raise ValueError("UI handoff does not contain an accepted requirements plan")
    for role, expected in execution:
        report = json.loads((root / refs[role]["path"]).read_text())
        if (
            report.get("status") != expected
            or report.get("figma_file") != handoff["figma_file"]
            or report.get("required_changes")
            or not report.get("evidence")
        ):
            raise ValueError("UI handoff does not have a consistent accepted Figma result")
    return {**handoff, "brief": (root / refs["brief"]["path"]).read_text()}


def instructions(settings, *, stage=None, current_task=None):
    target = settings.get("figma_file")
    if not target:
        return ""
    if settings.get("figma_references"):
        target = "\n".join(settings["figma_references"])
    policy = (
        prompts.get("fragments/figma/instructions.md")
        if settings.get("figma_review", "automatic") == "automatic"
        else prompts.get("fragments/figma/instructions-02.md")
    )
    if current_task and stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
        return (
            "\nFIGMA DESIGN INPUT\nReference: "
            + target
            + prompts.get("fragments/figma/instructions-04.md")
            + policy
            + "\n"
        )
    return (
        prompts.get("fragments/figma/instructions-05.md")
        + target
        + prompts.get("fragments/figma/instructions-03.md")
        + policy
        + "\n"
    )


def require_chatgpt(settings):
    if (
        settings.get("auth_mode") != "ChatGPT"
        or settings.get("environment_auth_present")
        or settings.get("environment_base_url_present")
        or settings.get("openai_base_url")
    ):
        raise ValueError("Figma workflow requires Codex ChatGPT login without API-key or base-URL overrides")
