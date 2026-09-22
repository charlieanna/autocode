"""GoCode-managed direct Codex transport.

This adapter intentionally has no OpenCode dependency. GoCode supplies the
managed environment and routes model aliases; Codex is the only agent CLI that
executes a role.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


DEFAULT_MODELS = {
    "glm": "gocode-openai/luna",
    "astra": "gocode-openai/astra",
    "terra": "gocode-openai/terra",
    "sol": "gocode-openai/sol",
}

API_MODELS = {
    "astra": "gpt-6-astra",
    "terra": "gpt-5.6-terra",
    "sol": "gpt-5.6-sol",
    "luna": "gpt-5.6-luna",
}


def local_settings(workspace: Path) -> dict:
    """Return non-secret GoCode identity or fail before any provider request."""
    del workspace
    executable = shutil.which("gocode")
    if not executable:
        raise RuntimeError("GoCode is not on PATH; no agent was launched")
    try:
        result = subprocess.run([executable, "status"], capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("GoCode status check failed; no agent was launched") from error
    summary = result.stdout + result.stderr
    managed = "mode: managed" in summary and "credential bundle: present" in summary
    unmanaged_authenticated = ("mode: unmanaged" in summary
                               and "GoCode authentication: ok" in summary)
    if result.returncode or not (managed or unmanaged_authenticated):
        raise RuntimeError("GoCode has no authenticated managed route; no agent was launched")
    return {
        "engine": "gocode",
        "executable": executable,
        "mode": "managed" if managed else "unmanaged",
        # Status includes changing usage, key rotation and connectivity details.
        # Those are diagnostics, not a change to the selected CLI route.
        "version": next((line.removeprefix("gocode version: ").strip()
                         for line in summary.splitlines() if line.startswith("gocode version: ")), None),
    }


def transport_drift(current: dict, checkpoint: dict) -> bool:
    """Require the same managed GoCode identity when resuming a run."""
    return current != checkpoint


def validate_model(model: str) -> str:
    """Resolve an explicit GoCode display route to its API model name."""
    if not isinstance(model, str) or not model.startswith("gocode-openai/"):
        raise ValueError("GoCode roles require a gocode-openai/<model> identifier")
    selected = model.removeprefix("gocode-openai/")
    resolved = API_MODELS.get(selected, selected)
    if resolved not in API_MODELS.values():
        raise ValueError("Unknown GoCode GPT model; select Astra, Terra, Sol or Luna explicitly")
    return resolved


def launch(*, role: str, workspace: Path, session: str | None, model: str,
           effort: str | None, sandbox: str, schema: Path, output: Path) -> list[str]:
    """Construct a direct GoCode-to-Codex invocation without an OpenCode hop."""
    del role
    api_model = validate_model(model)
    command = ["gocode", "exec", "codex", "exec", "-C", str(workspace), "--sandbox", sandbox]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    if session:
        command += ["resume", session]
    command += ["-", "--json", "--output-schema", str(schema), "-o", str(output), "--model", api_model]
    return command
