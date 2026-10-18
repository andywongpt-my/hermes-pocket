"""Task orchestration, with explicit uncertain-admission handling."""
import threading
import uuid
import json
from .schema import decode, ProtocolError
from .model_selection import snapshot, validate_snapshot, catalog
from .store import TERMINAL, InvalidTransition

class Service:
    def __init__(self,store,backend):
        self.store=store;self.backend=backend;self.lock=threading.RLock()
    def validate_model_choice(self,choice):
        value=snapshot(choice)
        if choice!='default' and not callable(getattr(self.backend,'create_selected',None)):
            raise ProtocolError('backend does not support model selection')
        return value
    def model_options(self):
        return catalog(callable(getattr(self.backend,'create_selected',None)))
    def validate_submission(self,data,selection=None):
        data=decode(json.dumps(data,ensure_ascii=False).encode())
        value=self.validate_model_choice(data.get('model_choice','default'))
        if selection is not None:value=validate_snapshot(selection,value['choice_id'])
        return data,value
    def submit(self,device,data,*,history=None,selection_snapshot=None):
        data,selection=self.validate_submission(data,selection_snapshot)
        with self.lock:
            task,new=self.store.admit(device,data,selection=selection)
            if not new:return task
            rid=data['request_id'];self.store.transition(device,rid,'dispatching')
            try:
                selected=self.store.model_selection(device,rid)
                if selected['choice_id']!='default':
                    run=self.backend.create_selected(data['text'],device+':'+rid,selection=selected,history=history)
                else:
                    run=self.backend.create(data['text'],device+':'+rid) if history is None else self.backend.create(data['text'],device+':'+rid,history=history)
                return self.store.transition(device,rid,'queued_upstream',run_id=run)
            except Exception:
                # Dispatch may have reached the server. Never retry automatically.
                return self.store.transition(device,rid,'unknown')
    def refresh(self,device,rid):
        with self.lock:
            task=self.store.get(device,rid)
            if task['state'] in TERMINAL or not task['run_id']:return task
            snapshot=self.backend.read(task['run_id'])
            return self.store.transition(device,rid,snapshot['state'],result=snapshot.get('result'))
    def stop(self,device,rid):
        with self.lock:
            task=self.store.get(device,rid)
            if task['state'] in TERMINAL:return task
            if not task['run_id']:raise InvalidTransition('no confirmed upstream run')
            self.backend.stop(task['run_id'])
            # A stop request is not proof that work stopped.
            return self.refresh(device,rid)
    def abandon(self,device,rid):
        """Explicitly close an unresolvable task. Never stops or resubmits upstream work."""
        with self.lock:
            try:task=self.refresh(device,rid)
            except Exception:task=self.store.get(device,rid)
            if task['state']!='unknown':raise InvalidTransition('only unknown tasks can be abandoned')
            return self.store.transition(device,rid,'failed')

class FakeHermes:
    """Deterministic test backend. Does not invoke a model or execute tools."""
    def __init__(self):self.runs={};self.submissions=0
    def create(self,text,key,history=None):
        self.submissions+=1;rid='run_'+uuid.uuid4().hex
        self.runs[rid]={'state':'running','result':None};return rid
    def read(self,rid):return dict(self.runs[rid])
    def complete(self,rid,result):self.runs[rid]={'state':'completed','result':result}
    def stop(self,rid):self.runs[rid]={'state':'cancelled','result':None}
