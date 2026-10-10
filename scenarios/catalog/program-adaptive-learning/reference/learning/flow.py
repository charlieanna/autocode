"""Thin connected journey, extended by the engine and backend without changing its API."""

import json
from pathlib import Path


def thin_evaluate(lesson, answer, hinted):
    correct = str(answer).strip() == lesson["answer"]
    return {
        "outcome": "hint-assisted" if correct and hinted else "independent" if correct else "wrong",
        "points": (1 if hinted else 2) if correct else 0,
        "mastered": correct and not hinted,
    }


class Session:
    def __init__(self, student, database=None):
        self.student = student
        self.lessons = json.loads(Path(__file__).with_name("lessons.json").read_text())
        self.current = self.lessons[0]
        self.hinted = False
        self.records = []
        self.repository = None
        if database is not None:
            from .backend import Repository

            self.repository = Repository(database)
            self.records = self.repository.attempts(student)

    def read(self, lesson="addition"):
        self.current = next(row for row in self.lessons if row["id"] == lesson)
        return dict(self.current)

    def hint(self):
        self.hinted = True
        return self.current["hint"]

    def answer(self, answer):
        try:
            from .engine import evaluate
        except ModuleNotFoundError as error:
            if error.name != "learning.engine":
                raise
            evaluate = thin_evaluate
        result = evaluate(self.current, answer, self.hinted)
        row = {"lesson": self.current["id"], "hinted": self.hinted, **result}
        self.records.append(row)
        if self.repository is not None:
            self.repository.save(self.student, self.current["id"], result, self.hinted)
        self.hinted = False
        return dict(row)

    def progress(self):
        return [dict(row) for row in self.records]

    def next(self):
        mastered = {row["lesson"] for row in self.records if row["mastered"]}
        return next((row["id"] for row in self.lessons if row["id"] not in mastered), None)
