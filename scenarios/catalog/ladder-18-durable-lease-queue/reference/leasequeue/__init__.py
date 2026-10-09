import sqlite3

# Store caller integers as hexadecimal text; do arithmetic with Python ints.
import uuid
from contextlib import contextmanager


class LeaseQueue:
    def __init__(self,path):
        self.path=str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,payload TEXT,token TEXT,deadline TEXT,done INTEGER DEFAULT 0)')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()

    @staticmethod
    def _time(now):
        if type(now) is not int or now<0: raise ValueError('invalid time')

    def enqueue(self,id,payload):
        if not isinstance(payload,str): raise ValueError('payload must be text')
        with self._db() as db:
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(id,)).fetchone()
            if row:
                if row[0]!=payload: raise ValueError('conflicting payload')
                return False
            db.execute('INSERT INTO jobs(id,payload) VALUES (?,?)',(id,payload))
            return True

    def claim(self,now,lease_seconds):
        self._time(now)
        if type(lease_seconds) is not int or lease_seconds<=0: raise ValueError('invalid lease')
        with self._db() as db:
            # Compare decoded integers in Python: injected clocks have no signed-64-bit bound.
            candidates=db.execute('SELECT id,payload,token,deadline FROM jobs WHERE done=0 ORDER BY seq')
            row=next((item for item in candidates if item[2] is None or int(item[3],16)<=now),None)
            if row is None: return None
            token=uuid.uuid4().hex; deadline=now+lease_seconds
            db.execute('UPDATE jobs SET token=?,deadline=? WHERE id=?',(token,hex(deadline),row[0]))
            return dict(id=row[0],payload=row[1],token=token,deadline=deadline)

    def _finish(self,id,token,now,done):
        self._time(now)
        with self._db() as db:
            row=db.execute('SELECT token,deadline,done FROM jobs WHERE id=?',(id,)).fetchone()
            if row is None or row[2] or row[0] is None or row[0]!=token or int(row[1],16)<=now: return False
            db.execute('UPDATE jobs SET done=?,token=NULL,deadline=NULL WHERE id=?',(done,id))
            return True

    def ack(self,id,token,now): return self._finish(id,token,now,1)
    def nack(self,id,token,now): return self._finish(id,token,now,0)
    def pending(self):
        with self._db() as db: return db.execute('SELECT count(*) FROM jobs WHERE done=0').fetchone()[0]
