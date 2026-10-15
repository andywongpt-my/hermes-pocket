"""Device- and target-scoped durable chat turns over the task service.

A target owns its own database. One in-flight turn per chat; bounded history is
sent explicitly using Hermes' documented conversation_history field.
"""
import hashlib
import json
from .schema import decode, identifier
from .store import Conflict, NotFound

class ChatService:
    def __init__(self,service):
        self.service=service;self.store=service.store
        with self.store.lock,self.store.db:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS chat_turns (
              ordinal INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,
              chat TEXT NOT NULL, request TEXT NOT NULL, text TEXT NOT NULL,
              task TEXT NOT NULL, history TEXT NOT NULL,
              UNIQUE(device,chat,request))''')
    def _rows(self,device,chat):
        return self.store.db.execute('SELECT * FROM chat_turns WHERE device=? AND chat=? ORDER BY ordinal',(device,chat)).fetchall()
    def submit(self,device,chat,data):
        identifier(device);identifier(chat);data=decode(json.dumps(data).encode())
        with self.service.lock,self.store.lock:
            rows=self._rows(device,chat)
            old=next((r for r in rows if r['request']==data['request_id']),None)
            if old:
                if old['text']!=data['text']:raise Conflict('request reused with changed text')
                task_id=old['task'];history=json.loads(old['history'])
            else:
                history=[]
                for row in rows[-20:]:
                    try:task=self.service.refresh(device,row['task'])
                    except NotFound:raise Conflict('previous admission requires reconciliation') from None
                    if task['state'] not in ('completed','failed','cancelled'):raise Conflict('previous turn is unfinished')
                    if task['state']=='completed' and task['result'] is not None:
                        history.extend([{'role':'user','content':row['text']},{'role':'assistant','content':task['result']}])
                while history and len(json.dumps(history,ensure_ascii=False).encode())>16384:history=history[2:]
                task_id='c_'+hashlib.sha256(json.dumps([device,chat,data['request_id']]).encode()).hexdigest()[:60]
                with self.store.db:
                    self.store.db.execute('INSERT INTO chat_turns(device,chat,request,text,task,history) VALUES(?,?,?,?,?,?)',
                        (device,chat,data['request_id'],data['text'],task_id,json.dumps(history)))
            return self.service.submit(device,dict(data,request_id=task_id),history=history)
    def transcript(self,device,chat):
        identifier(device);identifier(chat)
        with self.service.lock,self.store.lock:
            result=[]
            for row in self._rows(device,chat)[-100:]:
                try:task=self.service.refresh(device,row['task'])
                except NotFound:task={'state':'unknown','result':None}
                except Exception:task=self.store.get(device,row['task']) # Last stored state; never rewritten.
                result.append({'request_id':row['request'],'task_id':row['task'],'text':row['text'],
                               'state':task['state'],'result':task['result']})
            return result
