"""Real negative control for the scoped AC6 coverage gap."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(os.environ["AUTOCODE_SEMVER_FIXTURE"])
workspace = Path.cwd()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert (
    digest(workspace / "src/semver/__init__.py") == "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81"
)
assert digest(workspace / "src/semver/jsonio.py") == "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537"
assert digest(workspace / "src/semver/version.py") == "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23"
assert digest(workspace / "tests/test_json.py") == "b4d75c5331b1ee453c2937d9fe67c12fb3d3597843455260712480895096a541"
assert (
    digest(root / "references/strengthened-compatible.py")
    == "92b67bbe4670bddeb74e1309c2f49fc915774352aa5ea616ef8e42a6e30cc3f9"
)
env = dict(os.environ)
env["PYTHONPATH"] = "src"
with tempfile.TemporaryDirectory(prefix="review-all-", dir=root) as td:
    project = Path(td) / "project"
    shutil.copytree(
        workspace,
        project,
        ignore=shutil.ignore_patterns(".git", ".autocode", "__pycache__", ".coverage", ".pytest_cache"),
    )
    source = project / "src/semver/__init__.py"
    text = source.read_text()
    needle = '    "dumps_version",\n'
    assert text.count(needle) == 1
    source.write_text(text.replace(needle, ""))
    command = [os.environ["AUTOCODE_SEMVER_PYTHON"], "-m", "pytest", "-q", "tests/test_json.py"]
    weak = subprocess.run(
        [*command, "--junitxml=" + str(project / "weak.xml")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    shutil.copyfile(root / "references/strengthened-compatible.py", project / "tests/test_json.py")
    strong = subprocess.run(
        [*command, "--junitxml=" + str(project / "strong.xml")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    weak_cases = list(ET.parse(project / "weak.xml").getroot().iter("testcase"))
    strong_cases = list(ET.parse(project / "strong.xml").getroot().iter("testcase"))
    expected = {f"test_ac{i}_" for i in range(1, 7)}
    weak_names = {
        case.get("name")
        for case in weak_cases
        if not any(child.tag in ("failure", "error", "skipped") for child in case)
    }
    failed = {case.get("name") for case in strong_cases if any(child.tag == "failure" for child in case)}
    if (
        len(weak_names) != 6
        or not all(any(name.startswith(prefix) for name in weak_names) for prefix in expected)
        or failed != {"test_ac6_package_exports"}
    ):
        raise SystemExit("The six weak cases or the exact AC6 failure were not observed")
    if weak.returncode != 0 or strong.returncode != 1 or "__all__" not in strong.stdout:
        raise SystemExit("actual AC6 mutation control did not reproduce")
print("Observed: initial six tests pass after deleting the dumps_version __all__ entry; archived stronger AC6 fails.")
