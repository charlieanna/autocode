"""Source-controlled miniature projects for public visual-check tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "autocode.py"
MODULE = ROOT / "tools" / "autocode_visual_check.py"

try:
    from PIL import Image
except ImportError:
    Image = None


APP_SOURCE = """from PIL import Image, ImageDraw

def functional_value():
    return 42

def render(size, variant="match"):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    offset = 7 if variant == "shift" else 0
    draw.rectangle((5 + offset, 5, 24 + offset, size[1] - 6), fill="#224466")
    draw.rectangle((40, 10, size[0] - 10, 14), fill="#222222")
    if variant != "missing-control":
        draw.rectangle((size[0] - 20, size[1] - 16, size[0] - 11, size[1] - 9), fill="#cc4422")
    return image
"""

CAPTURE_SOURCE = """import json
import os
from pathlib import Path
import sys
from app import functional_value, render

root = Path.cwd()
output = Path(os.environ["AUTOCODE_VISUAL_OUTPUT"])
manifest_path = Path(os.environ["AUTOCODE_VISUAL_MANIFEST"])
assert output.is_absolute() and manifest_path.is_absolute()
assert manifest_path.name == "manifest.json" and manifest_path.is_relative_to(root)
assert output.is_dir() and not list(output.iterdir())
assert functional_value() == 42
marker = root / ".autocode" / "capture-invocations.txt"
with marker.open("a") as stream:
    stream.write(str(output) + "\\n")
print("fixture functional assertions passed; this is not report JSON")
config = json.loads((root / "capture-config.json").read_text())
mode = config.get("mode", "match")
if mode == "nonzero":
    print("deliberate capture failure", file=sys.stderr)
    sys.exit(7)
if mode == "no-inventory":
    sys.exit(0)
manifest = json.loads(manifest_path.read_text())
rows = []
for case in manifest["cases"]:
    viewport = case["viewport"]
    size = (round(viewport["width"] * viewport["device_scale_factor"]),
            round(viewport["height"] * viewport["device_scale_factor"]))
    name = case["id"] + ".png"
    variant = config.get("variants", {}).get(case["id"], "match")
    image = render(size, variant)
    if mode == "wrong-size":
        image = render((size[0] - 1, size[1]), variant)
    image.save(output / name)
    row = {"id": case["id"], "path": name, "state": case["state"],
           "route": case["route"], "viewport": dict(viewport)}
    if mode == "bad-viewport":
        row["viewport"]["width"] += 1
    if mode == "bad-state":
        row["state"] = "not-the-requested-state"
    if mode == "bad-route":
        row["route"] = "/other"
    if mode == "corrupt":
        (output / name).write_bytes(b"not a PNG")
    if mode == "truncated":
        data = (output / name).read_bytes()
        (output / name).write_bytes(data[:len(data) // 2])
    if mode == "missing-image":
        (output / name).unlink()
    if mode in ("escape", "symlink"):
        baseline = manifest_path.parent / case["artifacts"]["screenshot"]["path"]
        if mode == "escape":
            row["path"] = os.path.relpath(baseline, output)
        else:
            (output / name).unlink()
            (output / name).symlink_to(baseline)
    rows.append(row)
if mode == "missing":
    rows.pop()
if mode == "duplicate":
    rows.append(dict(rows[0]))
if mode == "unknown-case":
    rows.append({**rows[0], "id": "unapproved"})
if mode == "unknown-field":
    rows[0]["accepted"] = True
inventory = {"version": 1, "cases": rows}
text = json.dumps(inventory)
if mode == "malformed-inventory":
    text = "{not JSON"
if mode == "duplicate-key":
    text = text.replace('"version": 1', '"version": 0, "version": 1')
(output / "captures.json").write_text(text)
if mode == "source-change":
    with (root / "app.py").open("a") as stream:
        stream.write("\\n# source changed during capture\\n")
if mode == "baseline-change":
    baseline = manifest_path.parent / manifest["cases"][0]["artifacts"]["screenshot"]["path"]
    baseline.write_bytes(baseline.read_bytes() + b"changed during capture")
if mode == "policy-change":
    with (root / "visual-policy.json").open("a") as stream:
        stream.write(" ")
if mode == "manifest-change":
    with manifest_path.open("a") as stream:
        stream.write(" ")
if mode == "context-change":
    with (manifest_path.parent / "context.json").open("a") as stream:
        stream.write(" ")
if mode == "ignored-source-change":
    (root / "web/generated.css").write_text("body { display: none; }\\n")
"""

BROWSER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
* { box-sizing: border-box; animation: none !important; transition: none !important; }
html, body { margin: 0; background: #fff; color: #182538; font: 14px Arial, sans-serif; }
header { height: 32px; padding: 6px 12px; background: #182538; color: white; }
aside { position: absolute; left: 0px; top: 32px; width: 64px; height: 128px; padding: 12px 6px; background: #e6edf3; }
main { margin-left: 76px; padding-top: 12px; }
h1 { margin: 0 0 10px; font-size: 17px; }
button { border: 0; padding: 5px 8px; background: #2864a0; color: white; font: inherit; }
#extra { position: absolute; right: 10px; bottom: 10px; width: 12px; height: 12px; padding: 0; background: #ca432a; }
</style></head><body><header>Offline workspace</header><aside>Home<br>Reports</aside>
<main><h1>Current task</h1><button id="action" onclick="document.querySelector('#count').textContent = '1'">Run</button>
<output id="count">0</output></main><button id="extra" aria-label="More options"></button></body></html>
"""

BROWSER_CAPTURE_SOURCE = """import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path.cwd()
output = Path(os.environ["AUTOCODE_VISUAL_OUTPUT"])
manifest = json.loads(Path(os.environ["AUTOCODE_VISUAL_MANIFEST"]).read_text())
executable = os.environ.get("AUTOCODE_CHROMIUM") or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
rows = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
    try:
        for case in manifest["cases"]:
            viewport = case["viewport"]
            context = browser.new_context(viewport={"width": viewport["width"], "height": viewport["height"]},
                                          device_scale_factor=viewport["device_scale_factor"],
                                          reduced_motion="reduce", locale="en-US", timezone_id="UTC",
                                          color_scheme="light")
            try:
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort())
                page = context.new_page()
                page.goto((root / "app.html").as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.locator("#action").click()
                assert page.locator("#count").inner_text() == "1"
                page.evaluate("document.querySelector('#count').textContent = '0'")
                page.mouse.move(0, 0)
                page.locator("#action").blur()
                page.screenshot(path=str(output / (case["id"] + ".png")), animations="disabled", caret="hide")
                rows.append({"id": case["id"], "path": case["id"] + ".png", "state": case["state"],
                             "route": case["route"], "viewport": viewport})
            finally:
                context.close()
    finally:
        browser.close()
(output / "captures.json").write_text(json.dumps({"version": 1, "cases": rows}))
print("Browser click assertion passed")
"""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def offline_provider_environment(project, command):
    """Reuse the offline provider, changing only its proposed acceptance method."""
    bindir = project.root.parent / "provider-bin"
    bindir.mkdir()
    wrapper = bindir / "codex"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
        "import goal_fixtures\n"
        "original = goal_fixtures.body\n"
        "def visual_contract(**options):\n"
        "    options.update(questions=False, human=False)\n"
        "    contract = original(**options)\n"
        f"    contract['acceptance_criteria'][0]['verification_method'] = {command!r}\n"
        "    return contract\n"
        "goal_fixtures.body = visual_contract\n"
        f"runpy.run_path({str(ROOT / 'tools/fake_codex.py')!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    config = project.root.parent / "config"
    config.mkdir()
    codex_home = project.root.parent / "codex-home"
    codex_home.mkdir()
    return {
        "PATH": str(bindir) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "XDG_CONFIG_HOME": str(config),
        "CODEX_HOME": str(codex_home),
        "AUTOCODE_HOME": str(project.root.parent / "registry"),
        "AUTOCODE_FIXTURE_MODE": "no-human",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


class VisualProject:
    def __init__(self, *, cases=None):
        self.temp = tempfile.TemporaryDirectory(prefix="visual-check-")
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        (self.root / "design").mkdir()
        self.manifest_path = self.root / "design" / "manifest.json"
        self.policy_path = self.root / "visual-policy.json"
        self.write(".gitignore", ".autocode/\n__pycache__/\n")
        self.write("app.py", APP_SOURCE)
        self.write("capture.py", CAPTURE_SOURCE)
        self.write("design/context.json", '{"component": "offline test fixture"}\n')
        self.configure()
        cases = cases or [
            {
                "id": "desktop",
                "state": "ready",
                "route": "/",
                "viewport": {"width": 100, "height": 80, "device_scale_factor": 1},
            }
        ]
        namespace = {}
        exec(APP_SOURCE, namespace)
        rows = []
        for index, case in enumerate(cases):
            viewport = case["viewport"]
            screenshot = self.root / "design" / (case["id"] + ".png")
            namespace["render"]((viewport["width"], viewport["height"])).save(screenshot)
            rows.append(
                {
                    **case,
                    "file_key": "Abc",
                    "node_id": f"1:{index + 2}",
                    "implementation_paths": ["app.py"],
                    "export_scale": 1,
                    "artifacts": {
                        "screenshot": {"path": screenshot.name, "sha256": sha256(screenshot)},
                        "design_context": {"path": "context.json", "sha256": sha256(self.root / "design/context.json")},
                    },
                }
            )
        self.manifest = {
            "version": 1,
            "files": [{"key": "Abc", "nodes": [row["node_id"] for row in rows]}],
            "cases": rows,
        }
        self.policy = {
            "version": 1,
            "manifest": {"path": "design/manifest.json", "sha256": ""},
            "capture_command": [sys.executable, "capture.py"],
            "timeout_seconds": 30,
            "cases": [
                {"id": row["id"], "channel_tolerance": 0, "max_changed_ratio": 0.0, "regions": []} for row in rows
            ],
        }
        self.pin_manifest()
        git(self.root, "init", "-q")
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.name=Visual Test",
            "-c",
            "user.email=visual@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "visual fixture",
        )

    def close(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def configure(self, *, mode="match", variants=None):
        self.write("capture-config.json", json.dumps({"mode": mode, "variants": variants or {}}))

    def pin_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.policy["manifest"]["sha256"] = sha256(self.manifest_path)
        return self.pin_policy()

    def pin_policy(self):
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        return sha256(self.policy_path)

    def invoke(self, *, output=None, digest=None, policy="visual-policy.json", entry="cli", bootstrap=None):
        args = [
            "--workspace",
            str(self.root),
            "--policy",
            str(policy),
            "--policy-sha256",
            digest if digest is not None else sha256(self.policy_path),
        ]
        if output is not None:
            args += ["--output", str(output)]
        if bootstrap is not None:
            command = [sys.executable, "-c", bootstrap, str(MODULE), *args]
        elif entry == "module":
            command = [sys.executable, "-m", "autocode_cli.autocode_visual_check", *args]
        elif entry == "file":
            command = [sys.executable, str(MODULE), *args]
        else:
            command = [sys.executable, str(CLI), "visual-check", *args]
        return subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def inputs(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in [
                self.policy_path,
                self.manifest_path,
                *sorted((self.root / "design").glob("*.png")),
                self.root / "design/context.json",
            ]
        }
