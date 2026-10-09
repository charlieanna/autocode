import sqlite3
import uuid
from contextlib import contextmanager

class Store:
    def __init__(self,path):
        self.path=str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY,amount INTEGER,key TEXT UNIQUE)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,order_id TEXT,amount INTEGER,published INTEGER DEFAULT 0)')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    def create_order(self,order_id,amount,key):
        if type(amount) is not int or amount<=0: raise ValueError('invalid amount')
        try:
            with self._db() as db:
                prior=db.execute('SELECT id,amount FROM orders WHERE key=?',(key,)).fetchone()
                if prior:
                    if prior!=(order_id,amount): raise ValueError('idempotency conflict')
                    return False
                db.execute('INSERT INTO orders VALUES (?,?,?)',(order_id,amount,key))
                db.execute('INSERT INTO events(event_id,order_id,amount) VALUES (?,?,?)',(uuid.uuid4().hex,order_id,amount))
                return True
        except sqlite3.IntegrityError as error: raise ValueError('duplicate order') from error
    def orders(self):
        with self._db() as db: return dict(db.execute('SELECT id,amount FROM orders ORDER BY id'))
    def pending(self,limit=100):
        if type(limit) is not int or limit<=0: raise ValueError('invalid limit')
        with self._db() as db:
            return [dict(zip(('event_id','order_id','amount'),row)) for row in db.execute('SELECT event_id,order_id,amount FROM events WHERE published=0 ORDER BY seq LIMIT ?',(limit,))]
    def publish(self,sink,limit=100):
        count=0
        for event in self.pending(limit):
            sink(dict(event))
            with self._db() as db: db.execute('UPDATE events SET published=1 WHERE event_id=?',(event['event_id'],))
            count+=1
        return count
