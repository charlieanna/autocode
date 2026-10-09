import sqlite3
from contextlib import contextmanager

class Service:
    def __init__(self,path):
        self.path=str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS memberships (tenant TEXT,user TEXT,role TEXT,PRIMARY KEY(tenant,user))')
            db.execute('CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS documents (tenant TEXT,key TEXT,value TEXT,PRIMARY KEY(tenant,key))')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    def _authorize(self,db,actor,tenant,roles=('viewer','editor','admin')):
        row=db.execute('SELECT role FROM memberships WHERE tenant=? AND user=?',(tenant,actor)).fetchone()
        if row is None or row[0] not in roles: raise PermissionError('not authorized')
    def create_tenant(self,tenant,owner):
        try:
            with self._db() as db:
                db.execute('INSERT INTO tenants VALUES (?)',(tenant,)); db.execute('INSERT INTO memberships VALUES (?,?,?)',(tenant,owner,'admin'))
        except sqlite3.IntegrityError as error: raise ValueError('tenant exists') from error
    def _last_admin(self,db,tenant,user,new_role):
        old=db.execute('SELECT role FROM memberships WHERE tenant=? AND user=?',(tenant,user)).fetchone()
        if old and old[0]=='admin' and new_role!='admin':
            count=db.execute("SELECT count(*) FROM memberships WHERE tenant=? AND role='admin'",(tenant,)).fetchone()[0]
            if count<=1: raise ValueError('last admin')
    def grant(self,actor,tenant,user,role):
        with self._db() as db:
            self._authorize(db,actor,tenant,('admin',))
            if role not in ('viewer','editor','admin'): raise ValueError('invalid role')
            self._last_admin(db,tenant,user,role)
            db.execute('INSERT INTO memberships VALUES (?,?,?) ON CONFLICT(tenant,user) DO UPDATE SET role=excluded.role',(tenant,user,role))
    def revoke(self,actor,tenant,user):
        with self._db() as db:
            self._authorize(db,actor,tenant,('admin',))
            if db.execute('SELECT 1 FROM memberships WHERE tenant=? AND user=?',(tenant,user)).fetchone() is None: raise ValueError('unknown user')
            self._last_admin(db,tenant,user,None)
            db.execute('DELETE FROM memberships WHERE tenant=? AND user=?',(tenant,user))
    def put(self,actor,tenant,key,value):
        with self._db() as db:
            self._authorize(db,actor,tenant,('editor','admin'))
            if not isinstance(value,str): raise ValueError('value must be text')
            db.execute('INSERT INTO documents VALUES (?,?,?) ON CONFLICT(tenant,key) DO UPDATE SET value=excluded.value',(tenant,key,value))
    def get(self,actor,tenant,key):
        with self._db() as db:
            self._authorize(db,actor,tenant)
            row=db.execute('SELECT value FROM documents WHERE tenant=? AND key=?',(tenant,key)).fetchone()
            if row is None: raise KeyError(key)
            return row[0]
    def documents(self,actor,tenant):
        with self._db() as db:
            self._authorize(db,actor,tenant)
            return dict(db.execute('SELECT key,value FROM documents WHERE tenant=? ORDER BY key',(tenant,)))
    def delete(self,actor,tenant,key):
        with self._db() as db:
            self._authorize(db,actor,tenant,('editor','admin'))
            if db.execute('SELECT 1 FROM documents WHERE tenant=? AND key=?',(tenant,key)).fetchone() is None: raise KeyError(key)
            db.execute('DELETE FROM documents WHERE key=?',(key,))
