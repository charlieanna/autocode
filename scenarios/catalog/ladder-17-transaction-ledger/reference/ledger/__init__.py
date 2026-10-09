import sqlite3

# Store caller integers as hexadecimal text; do arithmetic with Python ints.
from contextlib import contextmanager


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, balance TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS transfers (key TEXT PRIMARY KEY, source TEXT, target TEXT, amount TEXT)"
            )

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, account, balance=0):
        if type(balance) is not int or balance < 0:
            raise ValueError("invalid balance")
        try:
            with self._transaction() as db:
                db.execute("INSERT INTO accounts VALUES (?, ?)", (account, hex(balance)))
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate account") from error

    def balance(self, account):
        with self._transaction() as db:
            row = db.execute("SELECT balance FROM accounts WHERE name=?", (account,)).fetchone()
            if row is None:
                raise KeyError(account)
            return int(row[0], 16)

    def transfer(self, key, source, target, amount):
        if type(amount) is not int or amount <= 0 or source == target:
            raise ValueError("invalid transfer")
        with self._transaction() as db:
            prior = db.execute("SELECT source,target,amount FROM transfers WHERE key=?", (key,)).fetchone()
            if prior is not None:
                if (prior[0], prior[1], int(prior[2], 16)) != (source, target, amount):
                    raise ValueError("idempotency conflict")
                return False
            balances = {}
            for account in (source, target):
                row = db.execute("SELECT balance FROM accounts WHERE name=?", (account,)).fetchone()
                if row is None:
                    raise KeyError(account)
                balances[account] = int(row[0], 16)
            if balances[source] < amount:
                raise ValueError("insufficient funds")
            db.execute("UPDATE accounts SET balance=? WHERE name=?", (hex(balances[source] - amount), source))
            db.execute("UPDATE accounts SET balance=? WHERE name=?", (hex(balances[target] + amount), target))
            db.execute("INSERT INTO transfers VALUES (?,?,?,?)", (key, source, target, hex(amount)))
            return True
