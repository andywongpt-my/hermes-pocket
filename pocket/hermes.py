"""Pinned Hermes HTTP contract with injected transport; no credentials stored here."""
from .schema import identifier
from .transport import UpstreamRejected

class UpstreamError(RuntimeError):pass

class HermesAdapter:
    def __init__(self,transport):self.transport=transport;self.ready=False
    def check(self):
        # An unreachable probe proves nothing about capabilities, so it keeps the
        # last verdict; clearing it first would fail concurrent submissions. An
        # answered rejection (such as a revoked token's 401) does clear it.
        try:data=self.transport('GET','/v1/capabilities',None,{})
        except UpstreamRejected:self.ready=False;raise
        features=data.get('features',{}) if isinstance(data,dict) else {}
        if not isinstance(features,dict) or not all(features.get(k) is True for k in ('run_submission','run_status','run_stop')):
            self.ready=False;raise UpstreamError('required Hermes capabilities unavailable')
        self.ready=True;return features
    def _call(self,method,path,body=None,headers=None):
        if not self.ready:raise UpstreamError('Hermes capabilities not verified')
        data=self.transport(method,path,body,headers or {})
        if not isinstance(data,dict):raise UpstreamError('invalid Hermes response')
        return data
    def create(self,text,key,history=None):
        body={'input':text}
        if history is not None:body['conversation_history']=history
        data=self._call('POST','/v1/runs',body,{'Idempotency-Key':key})
        return identifier(data.get('run_id'))
    def read(self,rid):
        data=self._call('GET','/v1/runs/'+identifier(rid))
        status=data.get('status')
        mapping={'queued':'queued_upstream','running':'running','stopping':'stopping',
                 'completed':'completed','failed':'failed','cancelled':'cancelled',
                 'interrupted':'cancelled','waiting_for_approval':'awaiting_approval'}
        if status not in mapping:raise UpstreamError('unsupported upstream run state')
        result=data.get('output')
        if result is not None and not isinstance(result,str):raise UpstreamError('invalid result format')
        return {'state':mapping[status],'result':result}
    def stop(self,rid):self._call('POST','/v1/runs/'+identifier(rid)+'/stop',{})
