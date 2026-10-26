"""Intersect ordinary Kilo stages with the operator's effective tool permissions.

Only model-free native debug commands resolve policy. Read-only verification uses
our qualified kernel shell, and so does planning (owner decision 2026-10-21); planning
report recovery has no tools. Unknown tools stay denied.
"""

import json
import re
import shutil
import subprocess
import uuid

try:
    from .. import autocode_tool_containment as containment
except ImportError:
    import autocode_tool_containment as containment

READ_TOOLS = ("read", "glob", "grep", "list")
# Writable stage work stays in the one session whose commands/reports the core
# can authenticate. Delegation, interactive gates and unknown tools stay denied.
WRITE_TOOLS = (*READ_TOOLS, "bash", "edit", "todowrite", "todoread", "lsp", "webfetch", "websearch", "codesearch")


def intersect(rules, tools):
    """Restrict an ordered effective native policy without turning a deny/ask into allow."""
    if not isinstance(rules, list) or not rules:
        raise ValueError("Kilo readonly launch requires effective native permission rules")
    result = {"*": "deny"}
    for tool in tools:
        patterns = {}
        for row in rules:
            if (
                not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) for key in ("permission", "pattern", "action"))
                or row["action"] not in ("allow", "ask", "deny")
            ):
                raise ValueError("Kilo returned an unsupported native permission rule")
            pattern = re.escape(row["permission"]).replace(r"\*", ".*").replace(r"\?", ".")
            if re.fullmatch(pattern, tool):
                patterns.pop(row["pattern"], None)
                # --auto grants asks. A restricted stage must not expand an
                # operator ask to execution merely because it is headless.
                patterns[row["pattern"]] = "deny" if row["action"] == "ask" else row["action"]
        result[tool] = patterns or {"*": "deny"}
    result["external_directory"] = "deny"
    return result


def restrict(
    argv, child, workspace, *, role, request=None, report_recovery=False, portable_shell=False, write_stage=False
):
    """``portable_shell``: a read-only checker on a host without the kernel shell (macOS, #1766) keeps the read
    tools and Kilo's own shell, intersected with the operator's policy; edits stay denied."""
    if write_stage and (request is not None or report_recovery or portable_shell):
        raise ValueError("A writable Kilo stage cannot use a read-only tool boundary")
    if portable_shell and (request is not None or report_recovery):
        raise ValueError("A portable Kilo shell replaces the kernel boundary; it cannot accompany one")
    # A remote server or inherited session could apply different tool grants.
    if any(arg in ("--attach", "--continue", "-c") or arg.startswith("--attach=") for arg in argv):
        raise ValueError("Restricted Kilo stages require a fresh local provider, not attach/continue")
    argv = list(argv)
    for option in ("--agent", "--session", "--model"):
        for index, argument in enumerate(argv):
            if argument.startswith(option + "="):
                argv[index : index + 1] = [option, argument[len(option) + 1 :]]
                break
        if argv.count(option) > 1 or option in argv and argv.index(option) + 1 >= len(argv):
            raise ValueError("Restricted Kilo launch has an ambiguous " + option)
    executable = shutil.which(argv[0], path=child.get("PATH", ""))
    if not executable:
        raise RuntimeError("Kilo readonly launch cannot find the configured executable")

    def debug(*args):
        result = subprocess.run(
            [executable, "debug", *args], cwd=workspace, env=child, capture_output=True, text=True, timeout=30
        )
        if result.returncode:
            raise RuntimeError("Kilo readonly policy could not be resolved before launch")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("Kilo debug policy must be a JSON object")
        return value

    if "--agent" in argv:
        selected = argv[argv.index("--agent") + 1]
    else:
        selected = debug("config").get("default_agent") or "code"
    original = debug("agent", selected)
    prompt = original.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("Kilo effective agent prompt must be a string")
    if report_recovery and request is not None:
        raise ValueError("Report recovery cannot grant a verification shell")
    permitted = (
        WRITE_TOOLS
        if write_stage
        else ()
        if report_recovery
        else ("bash",)
        if request is not None
        else (*READ_TOOLS, "bash")
        if portable_shell
        else READ_TOOLS
    )
    policy = intersect(original.get("permission"), permitted)
    if write_stage:
        # Kilo's action-only permissions reject pattern objects (and can discard
        # the entire inline config). Collapse only an unconditional rule; a
        # patterned rule cannot be represented here, so it remains denied.
        for tool in ("todowrite", "todoread", "webfetch", "websearch"):
            rules = policy[tool]
            policy[tool] = rules["*"] if set(rules) == {"*"} else "deny"
    config = json.loads(child["KILO_CONFIG_CONTENT"])
    agents = config.get("agent", {})
    if not isinstance(agents, dict):
        raise ValueError("Kilo inline agents must be an object")
    name = "autocode_" + role + "_" + uuid.uuid4().hex
    config["agent"] = {
        **agents,
        name: {
            "description": "AutoCode restricted " + role,
            "mode": "primary",
            "permission": policy,
            "model": argv[argv.index("--model") + 1],
            **({"prompt": prompt} if prompt is not None else {}),
        },
    }
    child["KILO_CONFIG_CONTENT"] = json.dumps(config)
    argv = list(argv)
    if "--agent" in argv:
        index = argv.index("--agent")
        del argv[index : index + 2]
    argv += ["--agent", name]
    # Session permissions can override an agent's restrictions. Never reuse a
    # previous unrestricted session across this boundary.
    if "--session" in argv:
        index = argv.index("--session")
        del argv[index : index + 2]
    if request is not None:
        child, _ = containment.configure(
            argv, child, workspace, allow_write=False, request=request, transport="kilocode"
        )
    return argv, child
