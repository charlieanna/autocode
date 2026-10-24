"""Recognize installed pytest console launchers without executing a PATH wrapper.

Only the fixed pip/distlib launcher bodies qualify. Their paired interpreter is
then discovered normally; the launcher and its version probe stay contained.
"""

import ast
import re
from pathlib import Path


def _bodies():
    for module, entry in (("pytest", "console_main"), ("_pytest.config", "_console_main")):
        for imports, bootstrap in (
            ("import sys", ""),
            ("import re\nimport sys", r"    sys.argv[0] = re.sub(r'(-script\.pyw|\.exe)?$', '', sys.argv[0])" + "\n"),
            ("import sys", "    if sys.argv[0].endswith('.exe'):\n        sys.argv[0] = sys.argv[0][:-4]\n"),
        ):
            text = f"{imports}\nfrom {module} import {entry}\nif __name__ == '__main__':\n{bootstrap}    sys.exit({entry}())\n"
            yield ast.dump(ast.parse(text))


_BODIES = frozenset(_bodies())


def pytest_interpreter(launcher: Path) -> Path:
    """Return the exact paired interpreter, refusing modified or unknown launchers."""
    problem = f"Cannot qualify the selected pytest console launcher: {launcher}; no model launched"
    try:
        with launcher.open(encoding="utf-8") as stream:
            text = stream.read(16385)
        if len(text) > 16384:
            raise ValueError("oversized launcher")
        header, _, body = text.partition("\n")
        interpreter = Path(header[2:])
        if (
            not header.startswith("#!/")
            or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", interpreter.name)
            or interpreter.parent.resolve() != launcher.resolve().parent
            or ast.dump(ast.parse(body)) not in _BODIES
        ):
            raise ValueError("unknown launcher")
        return interpreter
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        raise RuntimeError(problem) from error
