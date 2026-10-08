import sqlite3
from contextlib import closing


class Repository:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS attempts (student TEXT, lesson TEXT, hinted INTEGER, "
                               "outcome TEXT, points INTEGER, mastered INTEGER)")

    def save(self, student, lesson, result, hinted):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?)",
                               (student, lesson, False, result["outcome"], result["points"], result["mastered"]))

    def attempts(self, student):
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT lesson, hinted, outcome, points, mastered FROM attempts "
                                      "WHERE student = ? ORDER BY rowid", (student,)).fetchall()
        return [{"lesson": lesson, "hinted": bool(hinted), "outcome": outcome, "points": points,
                 "mastered": bool(mastered)} for lesson, hinted, outcome, points, mastered in rows]
