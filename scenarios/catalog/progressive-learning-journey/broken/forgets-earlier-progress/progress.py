import json
from pathlib import Path


def completed(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []


def save(path, lesson):
    Path(path).write_text(json.dumps([lesson]))
