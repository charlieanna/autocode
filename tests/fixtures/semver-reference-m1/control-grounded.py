"""Check exact source sites against the preserved, supplemented acceptance tests."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

source = Path.cwd()
root = Path(os.environ["AUTOCODE_SEMVER_FIXTURE"])
py = os.environ["AUTOCODE_SEMVER_PYTHON"]
changes = [
    ("package-import", "src/semver/__init__.py", "from .jsonio import dumps_version, loads_version", ""),
    ("package-all-dumps", "src/semver/__init__.py", '    "dumps_version",\n', ""),
    ("package-all-loads", "src/semver/__init__.py", '    "loads_version",\n', ""),
    ("typing-import", "src/semver/jsonio.py", "from typing import Any, Dict, List, Tuple, Union", ""),
    ("version-import", "src/semver/jsonio.py", "from .version import Version", ""),
    (
        "duplicate-helper",
        "src/semver/jsonio.py",
        "def _object_without_duplicates(",
        "def _disabled_without_duplicates(",
    ),
    ("dumps-definition", "src/semver/jsonio.py", "def dumps_version(", "def disabled_dumps_version("),
    ("loads-definition", "src/semver/jsonio.py", "def loads_version(", "def disabled_loads_version("),
    ("unknown-message", "src/semver/jsonio.py", '"unknown key(s) in JSON object: {}"', '"invalid input"'),
    ("unknown-names", "src/semver/jsonio.py", '", ".join(repr(key) for key in unknown)', '"ignored"'),
    ("type-message", "src/semver/jsonio.py", '"key {!r} must be a nonnegative integer, got {!r}"', '"invalid integer"'),
    ("optional-message", "src/semver/jsonio.py", '"key {!r} must be a string or null, got {!r}"', '"invalid optional"'),
    (
        "invalid-semver-message",
        "src/semver/jsonio.py",
        '"invalid semver prerelease or build in JSON object: {!r}"',
        '"invalid semantic data"',
    ),
    ("to-json-definition", "src/semver/version.py", "    def to_json(self)", "    def disabled_to_json(self)"),
    (
        "from-json-decorator",
        "src/semver/version.py",
        "    @classmethod\n    def from_json(cls, text: str)",
        "    def from_json(cls, text: str)",
    ),
    (
        "from-json-definition",
        "src/semver/version.py",
        "    def from_json(cls, text: str)",
        "    def disabled_from_json(cls, text: str)",
    ),
]
expected = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    "tests/test_json.py": "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699",
}
for name, digest in expected.items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
results = {}
with tempfile.TemporaryDirectory(prefix="grounded-sites-", dir=root) as td:
    for tag, rel, old, new in changes:
        work = Path(td) / tag
        shutil.copytree(
            source,
            work,
            ignore=shutil.ignore_patterns(".git", ".autocode", "__pycache__", ".pytest_cache", ".coverage"),
        )
        path = work / rel
        text = path.read_text()
        assert old in text, tag
        path.write_text(text.replace(old, new, 1))
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [py, "-m", "pytest", "-q", "tests/test_json.py"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        structural = {
            "typing-import": "NameError: name 'List' is not defined",
            "version-import": "NameError: name 'Version' is not defined",
            "dumps-definition": "cannot import name 'dumps_version'",
            "loads-definition": "cannot import name 'loads_version'",
        }
        if tag in structural:
            if result.returncode != 4 or structural[tag] not in output:
                raise SystemExit("Unexpected collection refusal for " + tag)
            results[tag] = "collection:" + structural[tag]
        else:
            suffix = (
                "test_ac6_package_exports"
                if tag.startswith("package-all-")
                else "test_ac5_version_json_methods_agree"
                if tag.startswith(("to-json-", "from-json-"))
                else "test_ac1_dumps_version_compact_five_key_output"
                if tag == "package-import"
                else "test_ac3_loads_version_rejects_invalid_inputs"
            )
            if result.returncode != 1 or ("FAILED tests/test_json.py::" + suffix) not in output:
                raise SystemExit("The expected named test did not fail for " + tag)
            results[tag] = "named:" + suffix
print(json.dumps(results, separators=(",", ":")))
