"""Run an entirely synthetic HTTP device lifecycle. No provider accounts used."""
import json
import tempfile
import threading
import urllib.request
from pathlib import Path
from .store import Store
from .service import Service, FakeHermes
from .server import make_server


def run():
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'demo.sqlite';store=Store(path);backend=FakeHermes()
        svc=Service(store,backend)
        server=make_server(svc,{'pocket-demo-only':'demo-device'},port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def call(path,data=None):
            body=json.dumps(data).encode() if data is not None else None
            req=urllib.request.Request(base+path,data=body,headers={'Authorization':'Bearer pocket-demo-only','Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
        try:
            payload={'version':1,'request_id':'demo-task','text':'Synthetic task, do not execute real tools'}
            accepted=call('/tasks',payload)
            duplicate=call('/tasks',payload)
            # Simulate server-side work finishing while the device has no connection.
            backend.complete(accepted['run_id'],'Synthetic task completed successfully')
            result=call('/tasks/demo-task')
            events=call('/tasks/demo-task/events?after=1')
            usage=call('/usage')
            assert accepted['run_id']==duplicate['run_id'] and backend.submissions==1
            assert result['state']=='completed' and result['result']=='Synthetic task completed successfully'
            assert all(item['value'] is None for item in usage)
            return {'mode':'synthetic_only','duplicate_dispatches':0,'result':result['result'],
                    'state':result['state'],'replayed_event_count':len(events),
                    'usage':'not_configured','real_hardware_tested':False}
        finally:
            server.shutdown();server.server_close();thread.join();store.close()
if __name__=='__main__':print(json.dumps(run(),indent=2))
