#!/usr/bin/env python3
"""Run the offline simulated learning journey; --database retains progress across invocations."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scenarios/catalog/program-adaptive-learning/reference"))
from learning.flow import Session


def journey(database):
    students = {}
    for student, hinted in (("hint-assisted-student", True), ("independent-student", False)):
        learner = Session(student, database)
        lesson = learner.read()
        hint = learner.hint() if hinted else None
        if hinted:
            learner.read(lesson["id"])
        answer = learner.answer(lesson["answer"])
        reopened = Session(student, database)
        students[student] = {"read": lesson["text"], "hint": hint, "answer": answer,
                             "progress": reopened.progress(), "next": reopened.next()}
    return {"journey": "read -> hint -> answer -> progress -> next", "students": students,
            "does_not_prove": "that real students learn better"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.database:
        print(json.dumps(journey(args.database), indent=2))
    else:
        with tempfile.TemporaryDirectory(prefix="adaptive-learning-") as directory:
            print(json.dumps(journey(Path(directory) / "progress.sqlite"), indent=2))


if __name__ == "__main__":
    main()
