import json
from pathlib import Path


def completed(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []


def save(path, lesson):
    path = Path(path)
    rows = completed(path)
    if lesson not in rows:
        rows.append(lesson)
    path.write_text(json.dumps(rows))
