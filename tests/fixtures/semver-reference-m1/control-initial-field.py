"""Check that the current named tests miss an unknown-field diagnostic."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(os.environ["AUTOCODE_SEMVER_FIXTURE"])
source = Path.cwd()
expected = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    "tests/test_json.py": "b4d75c5331b1ee453c2937d9fe67c12fb3d3597843455260712480895096a541",
}
for name, digest in expected.items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
with tempfile.TemporaryDirectory(prefix="stage-missing-field-", dir=root) as td:
    work = Path(td) / "project"
    shutil.copytree(
        source, work, ignore=shutil.ignore_patterns(".git", ".autocode", "__pycache__", ".pytest_cache", ".coverage")
    )
    path = work / "src/semver/jsonio.py"
    text = path.read_text()
    before = '", ".join(repr(key) for key in unknown)'
    assert text.count(before) == 1
    path.write_text(text.replace(before, '"hidden"'))
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [
            os.environ["AUTOCODE_SEMVER_PYTHON"],
            "-m",
            "pytest",
            "-q",
            "tests/test_json.py",
            "--junitxml=" + str(work / "result.xml"),
        ],
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit("The original named tests no longer miss this diagnostic")
    cases = ET.parse(work / "result.xml").getroot().iter("testcase")
    expected = {
        "test_ac1_dumps_version_compact_five_key_output",
        "test_ac2_loads_version_valid_and_round_trip",
        "test_ac3_loads_version_rejects_invalid_inputs",
        "test_ac4_dumps_version_rejects_invalid_input",
        "test_ac5_version_json_methods_agree",
        "test_ac6_package_exports",
    }
    passed = {
        case.get("name") for case in cases if not any(child.tag in ("failure", "error", "skipped") for child in case)
    }
    if passed != expected:
        raise SystemExit("The original six named tests were not all observed passing")
print(json.dumps({"current_six_named_tests_pass_when_unknown_field_is_hidden": sorted(passed)}))
