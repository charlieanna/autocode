"""Observe exact scripted delimiter lines without manufacturing mutation evidence."""

import ast
import hashlib
import json
from pathlib import Path

EXPECTED = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    "tests/test_json.py": "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699",
}
for path, expected in EXPECTED.items():
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
        raise SystemExit(f"The scripted source or tests differ: {path}")

source = Path("src/semver/jsonio.py").read_text()
lines = source.splitlines(keepends=True)
ast.parse(source)
observed = []
for number in (89, 90, 98, 105, 112):
    line = lines[number - 1]
    if line.strip() != ")" or line.count(")") != 1:
        raise SystemExit(f"Unreviewed delimiter line: {number}")
    for replacement in ("", "]", "}"):
        changed = lines.copy()
        changed[number - 1] = line.replace(")", replacement, 1)
        try:
            ast.parse("".join(changed))
        except SyntaxError:
            continue
        raise SystemExit(f"The changed delimiter unexpectedly parses: {number}")
    observed.append(number)
print(json.dumps({"original_parses": True, "removed_or_replaced_delimiters_are_syntax_errors": observed}))
