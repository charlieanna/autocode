from pathlib import Path


class Repository:
    records = {}

    def __init__(self, path):
        self.path = str(Path(path).resolve())

    def save(self, student, lesson, result, hinted):
        self.records.setdefault((self.path, student), []).append({"lesson": lesson, "hinted": bool(hinted), **result})

    def attempts(self, student):
        return [dict(row) for row in self.records.get((self.path, student), [])]
