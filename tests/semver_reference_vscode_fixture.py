"""Start a fresh, offline SemVer M1 run for the actual extension host.

The source is a pinned public base plus reviewed checkpoint-derived fixtures.
The weaker initial AC6 and subsequent derived tests are reconstructions; this
is not the original twelve-criterion run.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "semver-reference-m1"
BASE = "6adf8765f6e21910f1f0c13151ce84f32f8d431d"
UPSTREAM = "https://github.com/python-semver/python-semver.git"
SOURCE_HASHES = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
}
FIXTURE_HASHES = {
    "criteria.json": "89320411c00b6b3ce3fcb85e1179f73569e48fb3a8026e74e92bdd6310261c85",
    "jsonio-source.py.txt": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "version-to-json.txt": "062aadb70bcd662846ba778ff9c1124a9a36f8bfd40a2c1789b2c38661ef9a0d",
    "version-from-json.txt": "4bfac77ff15e745b6d5779e09154fc9c3cab9f50c568cfad88c63c14e81966b8",
    "initial-test.py.txt": "b4d75c5331b1ee453c2937d9fe67c12fb3d3597843455260712480895096a541",
    "strengthened-test.py.txt": "92b67bbe4670bddeb74e1309c2f49fc915774352aa5ea616ef8e42a6e30cc3f9",
    "repaired-test.py.txt": "15dceec0f8ef80555f805cd555312b650cdb11e1a520155abecc61a2b8d2170f",
    "strengthened-diagnostic-test.py.txt": "13c8525decfb2ed34b8e2fced484eef41ce4e5ec99030f3579fab1128fb2240b",
    "diagnostic-test.py.txt": "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699",
    "control-initial-field.py": "57e8ceee92b9076bdb6c704b548354dc0b690ca22259e2efc042e76f82ecd772",
    "control-api.py": "9e5f403965c7ee3700d89ed6d63948332628622832953cf247d2477b6ad7327e",
    "control-grounded.py": "8ec5c053373f8959d3840eda56ba0787e0f3c32997123fb28a2aeb86bc1cfdf9",
    "control-missing-export.py": "c2b1bf2c4944faf18fb8a35182cd130fced54a34a1ec0a0364716c6b47459e9b",
    "control-missing-field.py": "355f4fe3f2945b8af196dd39e01ac1087b7fbf341aa8cf28244aa0d6e960f4db",
}
TESTS = {
    "initial.py": "initial-test.py.txt",
    "strengthened-compatible.py": "strengthened-test.py.txt",
    "repaired-compatible.py": "repaired-test.py.txt",
    "broad-diagnostics-compatible.py": "diagnostic-test.py.txt",
    "strengthened-diagnostic.py": "strengthened-diagnostic-test.py.txt",
}


def git(project: Path, env: dict[str, str], *args: str) -> str:
    command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "-C", str(project), *args]
    return subprocess.run(command, check=True, text=True, capture_output=True, env=env, timeout=120).stdout.strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(path: Path, old: str, new: str = "") -> None:
    content = path.read_text()
    if content.count(old) != 1:
        raise ValueError(f"Expected exactly one cleanup anchor in {path}")
    path.write_text(content.replace(old, new))


def environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if (
            key == "GIT_CONFIG_COUNT"
            or key.startswith(
                (
                    "GIT_CONFIG_KEY_",
                    "GIT_CONFIG_VALUE_",
                    "OPENAI_",
                    "ANTHROPIC_",
                    "KILO_API_",
                    "CODEX_API_",
                    "ZAI_API_",
                    "AWS_",
                    "AZURE_",
                    "GOOGLE_",
                )
            )
            or key.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))
        ):
            env.pop(key, None)
    bin_dir = root / "bin"
    env.update(
        {
            "PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), env.get("PATH", "")]),
            "GIT_TERMINAL_PROMPT": "0",
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "AUTOCODE_HOME": str(root / "registry"),
            "AUTOCODE_SOURCE_HOST": "0",
            "SCENARIO_FAKE_CONFIG": str(root / "fake-config.json"),
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUTOCODE_SEMVER_CORE": str(REPO),
            "AUTOCODE_SEMVER_FIXTURE": str(root),
            "AUTOCODE_SEMVER_PYTHON": sys.executable,
        }
    )
    return env


def record_advance(root: Path, label: str, run) -> None:
    proc = run.last_advance
    if proc is None:
        raise AssertionError(f"{label} did not retain its advancing process")
    with (root / "advances.jsonl").open("a") as stream:
        stream.write(
            json.dumps({"action": label, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            + "\n"
        )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: semver_reference_vscode_fixture.py ROOT")
    missing = [
        name for name in ("pytest", "pytest_cov", "coverage", "psutil") if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise SystemExit(f"Fixture interpreter lacks required test dependencies: {missing}")
    for name, expected in FIXTURE_HASHES.items():
        if digest(FIXTURES / name) != expected:
            raise AssertionError(f"Unreviewed fixture bytes: {name}")
    root = Path(argv[0]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    env = environment(root)
    env["AUTOCODE_SEMVER_OWNERS"] = os.environ.get("AUTOCODE_SEMVER_OWNERS", str(root / "owned-processes.jsonl"))
    env["AUTOCODE_SEMVER_ACTIONS"] = os.environ.get("AUTOCODE_SEMVER_ACTIONS", str(root / "fixture-actions.jsonl"))
    for name in ("AUTOCODE_SEMVER_OWNERS", "AUTOCODE_SEMVER_ACTIONS"):
        if not Path(env[name]).resolve().is_relative_to(root.parent):
            raise ValueError(f"{name} must stay in the owned fixture directory")
    # TaskRun deliberately merges os.environ into child env. This fixture is a
    # separate process, so sanitize its actual environment before TaskRun runs.
    os.environ.clear()
    os.environ.update(env)
    project = root / "project"
    project.mkdir()
    git(project, env, "init", "-q")
    git(project, env, "fetch", "--quiet", "--depth=1", UPSTREAM, BASE)
    git(project, env, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if git(project, env, "rev-parse", "HEAD") != BASE or git(project, env, "status", "--porcelain"):
        raise AssertionError("Fetched source is not the exact clean base")
    for name, value in [
        ("user.name", "Offline Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("commit.gpgsign", "false"),
    ]:
        git(project, env, "config", name, value)
    reference = root / "reference"
    shutil.copytree(project, reference, ignore=shutil.ignore_patterns(".git"))
    init = reference / "src/semver/__init__.py"
    anchor = "from .version import Version, VersionInfo\n"
    replace_once(init, anchor, anchor + "from .jsonio import dumps_version, loads_version\n")
    anchor = '    "VersionInfo",\n'
    replace_once(init, anchor, anchor + '    "dumps_version",\n    "loads_version",\n')
    version = reference / "src/semver/version.py"
    anchor = "    def __iter__(self) -> VersionIterator:\n"
    replace_once(version, anchor, (FIXTURES / "version-to-json.txt").read_text() + anchor)
    anchor = '    def replace(self, **parts: Union[int, Optional[str]]) -> "Version":\n'
    replace_once(version, anchor, (FIXTURES / "version-from-json.txt").read_text() + anchor)
    source = reference / "src/semver/jsonio.py"
    if source.exists():
        raise AssertionError("Pinned base unexpectedly contains jsonio.py")
    shutil.copyfile(FIXTURES / "jsonio-source.py.txt", source)
    for path, expected in SOURCE_HASHES.items():
        if digest(reference / path) != expected:
            raise AssertionError(f"Unreviewed candidate source: {path}")
    refs = root / "references"
    refs.mkdir()
    for name, fixture in TESTS.items():
        shutil.copyfile(FIXTURES / fixture, refs / name)
    shutil.copyfile(refs / "initial.py", reference / "tests/test_json.py")
    for name in (
        "control-missing-export.py",
        "control-missing-field.py",
        "control-initial-field.py",
        "control-grounded.py",
        "control-api.py",
    ):
        shutil.copyfile(FIXTURES / name, root / name)
    criteria = json.loads((FIXTURES / "criteria.json").read_text())
    if [row["id"] for row in criteria] != [f"AC{i}" for i in range(1, 7)]:
        raise AssertionError("Unexpected scoped criteria")
    (root / "approved-m1-criteria.json").write_text(json.dumps(criteria, indent=2))
    manifest = {
        "scope": "Reconstructed six-criterion SemVer M1; synthetic weaker initial AC6",
        "base": BASE,
        "checkpoint": "78c461a4 (retained local source; reconstructed using pinned snippets)",
        "sources": SOURCE_HASHES,
        "derived_tests": {name: digest(refs / name) for name in TESTS},
        "provider_sha256": digest(REPO / "tests/semver_reference_provider.py"),
        "controls": {
            name: digest(root / name)
            for name in (
                "control-missing-export.py",
                "control-missing-field.py",
                "control-initial-field.py",
                "control-grounded.py",
                "control-api.py",
            )
        },
    }
    (root / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2))
    brief = "Implement the python-semver library JSON interchange milestone. " + " ".join(
        row["criterion"] for row in criteria
    )
    paths = [*SOURCE_HASHES, "tests/test_json.py"]
    config = {
        "title": "Scoped offline SemVer M1 reference repair",
        "brief": brief,
        "reference": str(reference),
        "check": shlex.join([sys.executable, "-m", "pytest", "-q", "tests/test_json.py"]),
        "paths": paths,
        "criteria": {row["id"]: row["criterion"] for row in criteria},
        "milestones": [],
        "fault": "",
        "turns": [],
    }
    (root / "fake-config.json").write_text(json.dumps(config, indent=2))
    bin_dir = root / "bin"
    bin_dir.mkdir()
    provider = bin_dir / "codex"
    provider.write_text(
        f'#!{sys.executable}\nimport runpy\nrunpy.run_path({str(REPO / "tests/semver_reference_provider.py")!r}, run_name="__main__")\n'
    )
    provider.chmod(0o755)
    sys.path.insert(0, str(REPO / "tools"))
    from autocode_taskrun import TaskRun

    options = (
        "--test-command",
        shlex.join([sys.executable, "-m", "pytest", "-q"]),
        "--engine",
        "codex",
        "--joint-planning",
        "--spec-first",
        "--max-cost-usd",
        "0",
        "--task-planner-model",
        "gpt-6-astra",
        "--builder-model",
        "gpt-5.6-terra",
        "--tester-model",
        "gpt-5.6-sol",
        "--completion-model",
        "gpt-6-astra",
        "--planner-model",
        "gpt-5.6-sol",
        "--plan-reviewer-model",
        "gpt-6-astra",
        "--evidence-provenance",
        "fake",
    )
    run = TaskRun.start(
        project,
        brief,
        command=[sys.executable, str(REPO / "vscode-extension/test/host/semver-reference-cli.py")],
        env=env,
        cwd=root,
        options=options,
        timeout=300,
    )
    record_advance(root, "start", run)
    view = run.status()
    need = view.get("needs") or {}
    if need.get("kind") != "answer" or len(need.get("questions") or []) != 1:
        raise AssertionError(f"Unexpected requirements gate: {need}")
    question = need["questions"][0]
    if question.get("proposed_default") != "plan" or not all(
        s in question.get("question", "")
        for s in ("python-semver library JSON interchange", "dumps_version", "loads_version")
    ):
        raise AssertionError("Requirements question did not match the scoped task")
    (root / "requirements-review.json").write_text(
        json.dumps(
            {
                "delegation": "Operating agent review under AGENTS.md; not personal owner approval",
                "question": question,
                "answer": "plan",
            },
            indent=2,
        )
    )
    run.answer(question["id"], "plan", resolver_token=need["resolver_token"])
    view = run.status()
    for index in range(20):
        if (view.get("needs") or {}).get("kind") != "continue":
            break
        view = run.advance()
        record_advance(root, f"planning-{index}", run)
    plan, need = view.get("displayed_plan") or {}, view.get("needs") or {}
    if need.get("kind") != "approve_plan" or not need.get("token") or need["token"] != plan.get("token"):
        raise AssertionError(f"Expected exact plan approval, got {need}")
    fields = lambda rows: [(r["id"], r["criterion"], r["verification_method"]) for r in rows]
    if fields(plan["acceptance_criteria"]) != fields(criteria):
        raise AssertionError("Displayed criteria differ from the scoped task")
    if len(plan["milestones"]) != 1 or set(plan["milestones"][0]["affected_paths"]) != set(paths):
        raise AssertionError("Displayed paths differ from the reviewed scope")
    if (
        plan["permission_boundaries"] != ["Read and edit only this scenario workspace"]
        or plan.get("git_delivery") is not None
    ):
        raise AssertionError("Displayed boundaries exceed the reviewed scope")
    review = {
        "delegation": "Operating agent review under AGENTS.md; not personal owner approval",
        "plan": plan,
        "token": need["token"],
    }
    (root / "agent-plan-review.json").write_text(json.dumps(review, indent=2))
    (root / "plan-public-view.json").write_text(json.dumps(view, indent=2))
    child_env = {
        key: env[key]
        for key in (
            "PATH",
            "GIT_TERMINAL_PROMPT",
            "XDG_CONFIG_HOME",
            "AUTOCODE_HOME",
            "AUTOCODE_SOURCE_HOST",
            "SCENARIO_FAKE_CONFIG",
            "PYTHONPATH",
            "PYTHONDONTWRITEBYTECODE",
            "AUTOCODE_SEMVER_CORE",
            "AUTOCODE_SEMVER_FIXTURE",
            "AUTOCODE_SEMVER_PYTHON",
        )
    }
    print(
        json.dumps(
            {
                "workspace": str(project),
                "runDir": str(run.run_dir),
                "cli": [sys.executable, str(REPO / "tools/autocode.py")],
                "env": child_env,
                "view": view,
                "review": str(root / "agent-plan-review.json"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
