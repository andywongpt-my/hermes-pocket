'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
assert.match(html, /<form\b[^>]*id="wifi"/, 'Wi-Fi must have its own form');
assert.match(html, /<form\b[^>]*id="pairing"/, 'Pairing must have its own form');
assert(!script.includes("'/save'"), 'Combined save endpoint must be removed');
assert(!script.includes('innerHTML'), 'SSID must render only as text');
assert(!script.includes('localStorage'), 'Never persist secrets in the phone browser');
assert.match(html, /blank[^<]*(keep|preserv)/i, 'Blank optional fields preserve pairing');
assert(!/Save Wi-Fi and restart|Blank fields remove/.test(html));

// Execute the real page script; replace only unavailable browser APIs. Fake
// timers and fetch make every test offline and prevent real secret submission.
const tests=[], test=(name,run)=>tests.push({name,run});
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve()};
const state=changes=>({connected:false,ssid:'',connection:0,join_result:0,operation:0,
 capacity:5,profiles:[],scan_state:0,networks:[],pairing_saved:true,store_writable:true,expires_seconds:290,...changes});
const response=(code,data)=>({status:code,ok:code>=200&&code<300,json:async()=>data,
 text:async()=>typeof data==='string'?data:JSON.stringify(data)});
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return{promise,resolve}};
function boot(initial=state()){
 const elements={},calls=[],timers=new Map(),events={},confirmations=[];
 let timerId=0,current=initial,nextFetch=null,confirmResult=true;
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.children=[];this.listeners={};this.attributes={}}
  addEventListener(name,fn){(this.listeners[name]||=[]).push(fn)}
  async fire(name){for(const fn of this.listeners[name]||[])await fn({preventDefault(){}});await settle()}
  appendChild(child){this.children.push(child);return child}
  replaceChildren(...children){this.children=children}
  setAttribute(name,value){this.attributes[name]=String(value)}
  focus(){this.focused=true}
 }
 for(const m of html.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/gi)){
  const el=elements[m[2]]=new Element(m[1]);el.disabled=/\sdisabled(?:\s|>)/.test(m[0]);el.hidden=/\shidden(?:\s|>)/.test(m[0]);
  el.textContent=html.slice(m.index+m[0].length).split('<')[0];
 }
 const ctx={document:{getElementById:id=>elements[id]||null,createElement:tag=>new Element(tag),querySelector:selector=>{
  assert.equal(selector,'meta[name="pocket-nonce"]');return{content:'offline-test-nonce'}}},TextEncoder,AbortController,
  setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},
  addEventListener(name,fn){events[name]=fn},confirm(message){confirmations.push(message);return confirmResult},
  fetch(path,options={}){calls.push({path,options});if(nextFetch){const fn=nextFetch;nextFetch=null;return fn(path,options)}
   if(path==='/wifi/status')return Promise.resolve(response(200,current));return Promise.reject(new Error('Unexpected action '+path))}};
 ctx.window=ctx;vm.createContext(ctx);vm.runInContext(script,ctx);
 return{elements,calls,timers,events,confirmations,get(id){assert(elements[id],'Missing #'+id);return elements[id]},
  setStatus(value){current=state(value)},next(fn){nextFetch=fn},confirm(value){confirmResult=value},
  async start(){await settle();return this},async tick(){const entry=[...timers].find(([,t])=>t.ms===1500);
   assert(entry,'Status check must be scheduled after 1500 ms');timers.delete(entry[0]);await entry[1].fn();await settle()}};
}
const body=call=>JSON.parse(call.options.body),actions=app=>app.calls.filter(c=>c.path!=='/wifi/status');
const descendants=el=>el.children.flatMap(c=>[c,...descendants(c)]);
const buttons=app=>descendants(app.get('profiles')).filter(el=>el.tagName==='BUTTON');
const button=(app,label)=>{const el=buttons(app).find(el=>el.textContent===label);assert(el,'Missing profile button '+label);return el};
async function join(app,ssid='New network',password='test-password'){
 app.get('ssid').value=ssid;app.get('password').value=password;await app.get('wifi').fire('submit');
}
const accept=(app,operation)=>app.next(()=>Promise.resolve(response(202,{accepted:true,operation})));

test('terminal radio cleanup blocks replacement actions until retirement',async()=>{
 const app=await boot(state({join_result:4,operation:7,connection_busy:true,
  profiles:[{ssid:'Synthetic saved',connected:false}]})).start();
 for(const id of ['join','scan','pair-save','cancel'])assert.equal(app.get(id).disabled,true,id+' must wait for cleanup');
 await join(app,'Synthetic replacement');assert.equal(actions(app).length,0);
 assert.equal(button(app,'Connect').disabled,true);assert.equal(button(app,'Forget').disabled,true);
 assert.match(app.get('connection').textContent,/current connection attempt/i);
 app.setStatus({join_result:4,operation:7,connection_busy:false,
  profiles:[{ssid:'Synthetic saved',connected:false}]});await app.tick();
 assert.equal(app.get('join').disabled,false);accept(app,8);await join(app,'Synthetic replacement');
 assert.equal(actions(app).length,1);assert.equal(actions(app)[0].path,'/wifi/join');
});

test('nonce-protected initial status and nonoverlapping 1.5 second polling',async()=>{
 const app=await boot().start();assert.equal(app.calls[0].path,'/wifi/status');
 assert.equal(app.calls[0].options.headers['X-Pocket-Setup'],'offline-test-nonce');assert.equal(app.calls[0].options.cache,'no-store');
 const hold=deferred();app.next(()=>hold.promise);const check=app.tick();await settle();const count=app.calls.length;
 await app.get('retry-status').fire('click');assert.equal(app.calls.length,count);
 hold.resolve(response(200,state()));await check;assert.equal([...app.timers.values()].filter(t=>t.ms===1500).length,1);
});
test('literal SSIDs, saved count, and separate Wi-Fi / Hermes reachability status',async()=>{
 const name='<img src=x onerror=alert(1)>',app=await boot(state({connected:true,ssid:name,connection:3,
  profiles:[{ssid:name,connected:true},{ssid:'Other',connected:false}]})).start();
 assert.match(app.get('profile-count').textContent,/2\s*\/\s*5/);
 assert(descendants(app.get('profiles')).some(el=>el.textContent.includes(name)));
 assert.match(app.get('connection').textContent,/Wi-Fi connected/);assert.match(app.get('hermes-note').textContent,/Hermes.*(unreachable|reach)/i);
 assert.match(app.get('pairing-state').textContent,/saved.*preserv|preserv.*saved/i);
});
test('async scan obtains networks only from status, shows signal, and disables open networks',async()=>{
 const app=await boot().start();accept(app,4);await app.get('scan').fire('click');
 assert.equal(actions(app)[0].path,'/networks');assert.equal(actions(app)[0].options.headers['X-Pocket-Setup'],'offline-test-nonce');
 assert.match(app.get('netstatus').textContent,/Scanning/);assert.equal(app.get('networks').children.filter(el=>el.value).length,0);
 app.setStatus({operation:4,scan_state:2,networks:[{ssid:'<b>Home</b>',rssi:-42,open:false},{ssid:'Guest',rssi:-68,open:true},{ssid:'Far',rssi:-81,open:false}]});await app.tick();
 const opts=app.get('networks').children;assert(opts.some(el=>el.value==='<b>Home</b>'&&/Strong/.test(el.textContent)));
 assert(opts.some(el=>el.value==='Guest'&&el.disabled&&/Fair.*Open/.test(el.textContent)));assert(opts.some(el=>el.value==='Far'&&/Weak/.test(el.textContent)));
 app.get('networks').value='<b>Home</b>';await app.get('networks').fire('change');assert.equal(app.get('ssid').value,'<b>Home</b>');assert(app.get('password').focused);
 app.get('showpass').checked=true;await app.get('showpass').fire('change');assert.equal(app.get('password').type,'text');
});
test('failed scan permits hidden SSID entry and explicit retry',async()=>{
 const app=await boot().start();accept(app,1);await app.get('scan').fire('click');
 app.setStatus({operation:1,scan_state:3});await app.tick();assert.match(app.get('netstatus').textContent,/type.*name|name.*below/i);
 assert.equal(app.get('scan').disabled,false);accept(app,2);await join(app,'Hidden network');assert.equal(actions(app)[1].path,'/wifi/join');
});
test('join sends exactly SSID/password, accepts 32 UTF-8 bytes, clears only the accepted Wi-Fi secret',async()=>{
 const app=await boot().start();app.get('token').value='separate pairing draft';accept(app,6);await join(app,'é'.repeat(16));
 assert.deepEqual(body(actions(app)[0]),{ssid:'é'.repeat(16),password:'test-password'});assert.equal(actions(app)[0].path,'/wifi/join');
 assert.equal(actions(app)[0].options.method,'POST');assert.equal(actions(app)[0].options.headers['X-Pocket-Setup'],'offline-test-nonce');
 assert.equal(app.get('password').value,'');assert.equal(app.get('token').value,'separate pairing draft');assert.match(app.get('result').textContent,/Testing/);
});
test('invalid UTF-8 byte lengths cannot submit',async()=>{
 const app=await boot().start();for(const[ssid,password,message]of[['','test-password',/network name/i],['é'.repeat(17),'test-password',/32/],['Home','é'.repeat(3),/8/],['Home','é'.repeat(32),/63/]]){
  await join(app,ssid,password);assert.match(app.get('result').textContent,message)}assert.equal(actions(app).length,0);
});
test('queued join blocks duplicate submission and ignores stale completion',async()=>{
 const app=await boot().start();accept(app,9);await join(app);await app.get('wifi').fire('submit');assert.equal(actions(app).length,1);
 app.setStatus({operation:8,connected:true,connection:3,join_result:2,ssid:'Old'});await app.tick();assert.match(app.get('result').textContent,/Testing/);
 app.setStatus({operation:9,connection:2,join_result:1});await app.tick();assert.match(app.get('connection').textContent,/IP/);
 app.setStatus({operation:9,connected:true,connection:3,join_result:2,ssid:'New network',profiles:[{ssid:'New network',connected:true}]});await app.tick();
 assert.match(app.get('result').textContent,/saved/i);assert.equal(app.get('join').disabled,false);
});
test('authentication failure preserves known profiles and pairing and permits retry',async()=>{
 const app=await boot(state({profiles:[{ssid:'Known',connected:false}]})).start();accept(app,2);await join(app);
 app.setStatus({operation:2,join_result:3,connection:5,profiles:[{ssid:'Known',connected:false}]});await app.tick();
 assert.match(app.get('result').textContent,/password|authentication/i);assert.match(app.get('result').textContent,/kept|unchanged|preserv/i);
 assert.match(app.get('pairing-state').textContent,/preserv/i);assert.equal(app.get('join').disabled,false);
});
test('full profile storage never automatically evicts; existing SSID may be retested',async()=>{
 const profiles=['One','Two','Three','Four','Five'].map(ssid=>({ssid,connected:false})),app=await boot(state({profiles})).start();
 assert.match(app.get('storage-note').textContent,/full|5.*saved/i);assert.match(app.get('storage-note').textContent,/forget/i);
 await join(app,'Six');assert.equal(actions(app).length,0);assert.match(app.get('result').textContent,/full|forget/i);
 accept(app,1);await join(app,'Two');assert.equal(actions(app)[0].path,'/wifi/join');assert(!actions(app).some(c=>c.path==='/wifi/forget'));
});
test('saved connect never requests or transmits the typed password',async()=>{
 const app=await boot(state({profiles:[{ssid:'Known',connected:false}]})).start();app.get('password').value='unsubmitted secret';
 accept(app,3);await button(app,'Connect').fire('click');assert.equal(actions(app)[0].path,'/wifi/connect');assert.deepEqual(body(actions(app)[0]),{ssid:'Known'});
});
test('forget requires confirmation and awaits status before declaring removal',async()=>{
 const app=await boot(state({profiles:[{ssid:'Known',connected:false}]})).start();app.confirm(false);await button(app,'Forget').fire('click');
 assert.equal(actions(app).length,0);assert.match(app.confirmations[0],/Known/);app.confirm(true);accept(app,4);await button(app,'Forget').fire('click');
 assert.equal(actions(app)[0].path,'/wifi/forget');assert.deepEqual(body(actions(app)[0]),{ssid:'Known'});assert.match(app.get('profile-count').textContent,/1\s*\/\s*5/);
 app.setStatus({operation:4,join_result:7});await app.tick();assert.match(app.get('result').textContent,/forgotten|removed/i);assert.match(app.get('profile-count').textContent,/0\s*\/\s*5/);
});
test('second Forget ignores a pre-queue GET with a stale terminal and awaits its own fresh terminal',async()=>{
 const profile=ssid=>({ssid,connected:false});
 const app=await boot(state({profiles:[profile('First'),profile('Second'),profile('Keep')]})).start();
 accept(app,1);await button(app,'Forget').fire('click');
 app.setStatus({operation:1,join_result:7,profiles:[profile('Second'),profile('Keep')]});await app.tick();
 assert.match(app.get('result').textContent,/forgotten|removed/i);
 const oldGet=deferred();app.next(()=>oldGet.promise);const checking=app.tick();await settle();
 accept(app,2);await button(app,'Forget').fire('click');
 assert.deepEqual(body(actions(app)[1]),{ssid:'Second'});
 // This GET began before the second action. Its result/profile fields belong
 // to the prior terminal even if operation was read after the queue advanced.
 oldGet.resolve(response(200,state({operation:2,join_result:7,profiles:[profile('Second'),profile('Keep')]})));await checking;
 assert.match(app.get('result').textContent,/Removing.*Waiting/i);
 assert.doesNotMatch(app.get('result').textContent,/forgotten|removed/i);
 assert(buttons(app).every(el=>el.disabled),'Queued Forget must stay pending');
 // An older operation's terminal cannot finish this action either.
 app.setStatus({operation:1,join_result:7,profiles:[profile('Second'),profile('Keep')]});await app.tick();
 assert.match(app.get('result').textContent,/Removing.*Waiting/i);
 app.setStatus({operation:2,join_result:1,profiles:[profile('Second'),profile('Keep')]});await app.tick();
 assert.match(app.get('result').textContent,/Removing.*Waiting/i);
 app.setStatus({operation:2,join_result:7,profiles:[profile('Keep')]});await app.tick();
 assert.match(app.get('result').textContent,/forgotten|removed/i);assert.match(app.get('profile-count').textContent,/1\s*\/\s*5/);
 assert.equal(button(app,'Forget').disabled,false);assert.equal(actions(app).length,2);
});
test('GET begun while Forget acceptance is in flight cannot complete the accepted action',async()=>{
 const profile={ssid:'Keep until confirmed',connected:false};
 const app=await boot(state({profiles:[profile]})).start();
 const posting=deferred();app.next(()=>posting.promise);const forgetting=button(app,'Forget').fire('click');await settle();
 const oldGet=deferred();app.next(()=>oldGet.promise);const checking=app.tick();await settle();
 posting.resolve(response(202,{accepted:true,operation:1}));await forgetting;
 oldGet.resolve(response(200,state({operation:1,join_result:7,profiles:[profile]})));await checking;
 assert.match(app.get('result').textContent,/Removing.*Waiting/i);assert(buttons(app).every(el=>el.disabled));
 // A fast worker may finish before the next poll; observing pending=1 is not
 // required when a new GET confirms this operation's actual terminal state.
 app.setStatus({operation:1,join_result:7});await app.tick();
 assert.match(app.get('result').textContent,/forgotten|removed/i);assert.match(app.get('profile-count').textContent,/0\s*\/\s*5/);
});
test('cancel sends an empty body and status confirms restored networking',async()=>{
 const app=await boot().start();accept(app,1);await join(app);assert.equal(app.get('cancel').disabled,false);accept(app,2);await app.get('cancel').fire('click');
 assert.equal(actions(app)[1].path,'/wifi/cancel');assert.deepEqual(body(actions(app)[1]),{});
 app.setStatus({operation:2,join_result:4,connected:true,ssid:'Known',connection:3,profiles:[{ssid:'Known',connected:true}]});await app.tick();
 assert.match(app.get('result').textContent,/cancel/i);assert.match(app.get('connection').textContent,/Known/);
});
test('explicit HTTP rejection retains input for correction',async()=>{
 const app=await boot().start();app.next(()=>Promise.resolve(response(400,'Invalid Wi-Fi settings.')));await join(app);
 assert.match(app.get('result').textContent,/Invalid Wi-Fi settings/);assert.equal(app.get('password').value,'test-password');assert.equal(app.get('join').disabled,false);
});
test('uncertain action never resends secrets; retry checks status only',async()=>{
 const app=await boot().start();app.next(()=>Promise.reject(new Error('connection dropped')));await join(app);
 assert.match(app.get('result').textContent,/confirm|uncertain/i);assert.equal(app.get('password').value,'');assert.equal(app.get('join').disabled,true);
 app.setStatus({operation:3,join_result:1,connection:1});await app.get('retry-status').fire('click');assert.equal(actions(app).length,1);assert.equal(app.get('join').disabled,true);
 app.setStatus({operation:3,join_result:2,connected:true,connection:3,ssid:'New network',profiles:[{ssid:'New network',connected:true}]});await app.tick();
 assert.equal(actions(app).length,1);assert.match(app.get('result').textContent,/check.*saved|confirm|uncertain/i);assert.equal(app.get('join').disabled,false);
});
test('save failure does not claim the tested profile was persisted',async()=>{
 const app=await boot().start();accept(app,1);await join(app);app.setStatus({operation:1,join_result:6,connection:3,store_writable:false});await app.tick();
 const message=app.get('result').textContent;
 assert.match(message,/could not confirm saving/i);assert.match(message,/check saved networks before retrying/i);
 assert.match(message,/Hermes pairing was kept/);assert.doesNotMatch(message,/profiles.*(kept|unchanged|preserv)/i);
 assert.match(app.get('storage-note').textContent,/storage|read.only|writ/i);assert.equal(app.get('join').disabled,true);
});
test('status outage preserves visible profiles and disables changes until recovery',async()=>{
 const app=await boot(state({profiles:[{ssid:'Known',connected:false}]})).start();app.next(()=>Promise.reject(new Error('offline')));await app.tick();
 assert.match(app.get('session-status').textContent,/reach|unavailable|reconnect/i);assert.equal(app.get('join').disabled,true);assert.match(app.get('profile-count').textContent,/1\s*\/\s*5/);
 await app.tick();assert.equal(app.get('join').disabled,false);
});
test('poll failure during an accepted test reports uncertain completion until status confirms it',async()=>{
 const app=await boot().start();accept(app,7);await join(app);
 app.next(()=>Promise.reject(new Error('hotspot interrupted')));await app.tick();
 assert.match(app.get('result').textContent,/could not confirm|uncertain/i);
 assert.equal(actions(app).length,1);assert.equal(app.get('join').disabled,true);
 app.setStatus({operation:7,join_result:1,connection:2});await app.tick();
 assert.match(app.get('result').textContent,/Testing|Connecting/);
 app.setStatus({operation:7,join_result:2,connected:true,connection:3,ssid:'New network',profiles:[{ssid:'New network',connected:true}]});await app.tick();
 assert.match(app.get('result').textContent,/saved/i);assert.equal(actions(app).length,1);
});
test('pairing is explicit and separate, blank fields preserve values, accepted save clears fields',async()=>{
 const app=await boot().start();await app.get('pairing').fire('submit');assert.equal(actions(app).length,0);assert.match(app.get('pair-result').textContent,/kept|unchanged|preserv/i);
 app.get('windows').value='https://bridge.example';app.get('token').value='t'.repeat(32);app.next(()=>Promise.resolve(response(200,'Pairing saved.')));await app.get('pairing').fire('submit');
 assert.equal(actions(app)[0].path,'/pairing');assert.deepEqual(body(actions(app)[0]),{windows:'https://bridge.example',linux:'',token:'t'.repeat(32),ca:''});
 for(const id of['windows','linux','token','ca'])assert.equal(app.get(id).value,'');assert.match(app.get('pair-result').textContent,/saved/i);assert(!/restart|reboot/i.test(app.get('pair-result').textContent));
});
test('invalid optional pairing values cannot submit',async()=>{
 const app=await boot().start();for(const[id,value,pattern]of[['windows','http://bridge.example',/HTTPS/],['linux','https://user@bridge.example',/HTTPS/],['token','short',/32/],['ca','bad',/certificate/i]]){
  app.get(id).value=value;await app.get('pairing').fire('submit');assert.match(app.get('pair-result').textContent,pattern);app.get(id).value=''}assert.equal(actions(app).length,0);
});
test('uncertain pairing clears fields and never retries secrets',async()=>{
 const app=await boot().start();app.get('token').value='x'.repeat(32);app.next(()=>Promise.reject(new Error('timeout')));await app.get('pairing').fire('submit');
 assert.equal(app.get('token').value,'');assert.match(app.get('pair-result').textContent,/confirm|uncertain/i);await app.tick();assert.equal(actions(app).length,1);
});
test('read-only storage disables mutations while allowing saved connect',async()=>{
 const app=await boot(state({store_writable:false,profiles:[{ssid:'Known',connected:false}]})).start();
 assert.equal(app.get('join').disabled,true);assert.equal(app.get('pair-save').disabled,true);assert.equal(button(app,'Forget').disabled,true);assert.equal(button(app,'Connect').disabled,false);
});
test('403 expiry stops polling and every action',async()=>{
 const app=await boot(state({profiles:[{ssid:'Known',connected:false}]})).start();app.next(()=>Promise.resolve(response(403,'Setup expired.')));await app.tick();
 assert.match(app.get('session-status').textContent,/expired.*hold OK|hold OK.*expired/i);
 for(const id of['join','scan','cancel','pair-save','retry-status'])assert.equal(app.get(id).disabled,true);assert(buttons(app).every(el=>el.disabled));
 assert.equal([...app.timers.values()].filter(t=>t.ms===1500).length,0);const count=app.calls.length;await app.get('scan').fire('click');await app.get('wifi').fire('submit');assert.equal(app.calls.length,count);
 if(app.events.pageshow)await app.events.pageshow();assert.equal(app.calls.length,count);
});
test('action expiry clears typed secrets without a success claim',async()=>{
 const app=await boot().start();app.next(()=>Promise.resolve(response(403,'Setup expired.')));await join(app);
 assert.equal(app.get('password').value,'');assert.match(app.get('session-status').textContent,/expired/i);assert(!/Testing|saved/i.test(app.get('result').textContent));
});
test('zero remaining time and pagehide stop future polls',async()=>{
 const expired=await boot(state({expires_seconds:0})).start();assert.match(expired.get('session-status').textContent,/expired/i);assert.equal([...expired.timers.values()].filter(t=>t.ms===1500).length,0);
 const app=await boot().start();assert(app.events.pagehide);app.events.pagehide();assert.equal([...app.timers.values()].filter(t=>t.ms===1500).length,0);
});
test('returning to a cached phone page checks status again without restoring typed secrets',async()=>{
 const app=await boot().start();app.get('password').value='private draft';app.get('token').value='private pairing draft';
 app.events.pagehide();assert.equal(app.get('password').value,'');assert.equal(app.get('token').value,'');
 assert(app.events.pageshow,'Cached page must resume status checks on pageshow');
 const before=app.calls.length;await app.events.pageshow();await settle();
 assert.equal(app.calls.length,before+1);assert.equal(app.calls.at(-1).path,'/wifi/status');assert.equal(actions(app).length,0);
});
test('request timeout is uncertain and cannot resend the Wi-Fi password',async()=>{
 const app=await boot().start();app.next((path,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted')))));
 const sending=join(app);await settle();const timeout=[...app.timers.values()].find(timer=>timer.ms===15000);
 assert(timeout,'Request must have a finite timeout');timeout.fn();await sending;
 assert.match(app.get('result').textContent,/confirm|uncertain/i);assert.equal(app.get('password').value,'');assert.equal(actions(app).length,1);
});
(async()=>{for(const{name,run}of tests){try{await run();console.log('PASS '+name)}catch(error){console.error('FAIL '+name+'\n'+error.stack);process.exitCode=1;return}}console.log('PASS '+tests.length+' offline setup UI tests')})();
