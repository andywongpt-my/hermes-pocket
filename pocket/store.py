"""Durable, device-scoped task journal. No external effects in transactions."""
import json
import sqlite3
import threading
import time
from .schema import decode, identifier, ProtocolError

class Conflict(ValueError): pass
class NotFound(LookupError): pass
class InvalidTransition(ValueError): pass
TERMINAL={'completed','failed','cancelled'}
ALLOWED={
    'queued':{'dispatching','unknown'},
    'dispatching':{'running','queued_upstream','unknown'},
    'queued_upstream':{'stopping','running','awaiting_approval','completed','failed','cancelled','unknown'},
    'running':{'stopping','awaiting_approval','completed','failed','cancelled','unknown'},
    'awaiting_approval':{'stopping','running','completed','failed','cancelled','unknown'},
    'stopping':{'completed','failed','cancelled','unknown'},
    'unknown':{'stopping','running','queued_upstream','awaiting_approval','completed','failed','cancelled'},
}

class Store:
    def __init__(self,path):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(path),timeout=10,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks (
          device TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL, run_id TEXT, result TEXT, seq INTEGER NOT NULL,
          PRIMARY KEY(device,request_id));
        CREATE TABLE IF NOT EXISTS events (
          device TEXT NOT NULL, request_id TEXT NOT NULL, seq INTEGER NOT NULL,
          state TEXT NOT NULL, at REAL NOT NULL,
          PRIMARY KEY(device,request_id,seq),
          FOREIGN KEY(device,request_id) REFERENCES tasks(device,request_id));
        ''')
    def close(self):self.db.close()
    def _get(self,device,request_id):
        row=self.db.execute('SELECT * FROM tasks WHERE device=? AND request_id=?',(device,request_id)).fetchone()
        if row is None:raise NotFound('task not found')
        return dict(row)
    def get(self,device,request_id):
        identifier(device);identifier(request_id)
        with self.lock:
            row=self._get(device,request_id);row.pop('payload');return row
    def admit(self,device,data):
        identifier(device);data=decode(json.dumps(data,ensure_ascii=False).encode())
        payload=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        rid=data['request_id']
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            try: old=self._get(device,rid)
            except NotFound:old=None
            if old:
                if old['payload']!=payload:raise Conflict('request ID already used for different content')
                return self.get(device,rid),False
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,\'queued\',NULL,NULL,1)',(device,rid,payload))
            self.db.execute('INSERT INTO events VALUES(?,?,1,\'queued\',?)',(device,rid,time.time()))
            return self.get(device,rid),True
    def transition(self,device,rid,state,*,run_id=None,result=None):
        if run_id is not None:identifier(run_id)
        if result is not None and (not isinstance(result,str) or len(result.encode('utf-8'))>65536):
            raise ProtocolError('invalid result')
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE');old=self._get(device,rid)
            if state==old['state']:return self.get(device,rid)
            if state not in ALLOWED.get(old['state'],set()):raise InvalidTransition('invalid state transition')
            if run_id and old['run_id'] and run_id!=old['run_id']:raise Conflict('run ID cannot change')
            seq=old['seq']+1
            self.db.execute('UPDATE tasks SET state=?,run_id=?,result=?,seq=? WHERE device=? AND request_id=?',
                (state,run_id or old['run_id'],result if result is not None else old['result'],seq,device,rid))
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(device,rid,seq,state,time.time()))
            return self.get(device,rid)
    def events(self,device,rid,after=0):
        if type(after) is not int or after<0:raise ProtocolError('invalid cursor')
        with self.lock:
            self.get(device,rid)
            return [dict(r) for r in self.db.execute('SELECT seq,state,at FROM events WHERE device=? AND request_id=? AND seq>? ORDER BY seq LIMIT 100',(device,rid,after))]
    def recover(self):
        """Call once at exclusive process startup, never on device reconnect."""
        with self.lock:
            rows=list(self.db.execute("SELECT device,request_id FROM tasks WHERE state IN ('queued','dispatching')"))
            for r in rows:self.transition(r['device'],r['request_id'],'unknown')
