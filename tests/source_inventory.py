"""Application Python sources inspected by the repository's source gates."""
from pathlib import Path


def python_sources(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if path.name.startswith("test_") or "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        yield path
