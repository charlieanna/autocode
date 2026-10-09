import sqlite3
import uuid


def migrate(path):
    db = sqlite3.connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            db.commit()
            return 2
        if version != 1:
            raise ValueError("unsupported schema version")
        temporary_name = "people_v2_" + uuid.uuid4().hex
        db.execute(f'CREATE TABLE "{temporary_name}" (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE)')
        for identifier, name, email in db.execute("SELECT id, name, email FROM people ORDER BY id").fetchall():
            if not isinstance(email, str) or not email.strip():
                raise ValueError("email is required")
            normalized = email.strip()
            db.execute(f'INSERT INTO "{temporary_name}" VALUES (?, ?, ?)', (identifier, name, normalized))
        db.execute("DROP TABLE people")
        db.execute(f'ALTER TABLE "{temporary_name}" RENAME TO people')
        db.execute("PRAGMA user_version = 2")
        db.commit()
        return 2
    except (sqlite3.Error, ValueError) as error:
        db.rollback()
        raise ValueError(str(error)) from error
    finally:
        db.close()
