import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {audioFrame,ReliableUpload} from '../../workbench/meeting/static/reliable-upload.mjs';
const sleep=ms=>new Promise(r=>setTimeout(r,ms)),pcm=s=>new TextEncoder().encode(s).buffer;
class Store{
 constructor(){this.m={id:'s',acked:0,next:0,intent:'active'};this.data=[];}
 async meta(){return {...this.m};}
 async intent(id,v){this.m.intent=v;}
 async enqueue(id,b){if(this.m.intent!=='active')throw Error('stopped');this.data.push(...new Uint8Array(b));this.m.next+=b.byteLength;}
 async ack(id,n){if(n<this.m.acked||n>this.m.next)throw Error('mismatch');this.m.acked=n;}
 async read(id,n,max){return new Uint8Array(this.data.slice(n,n+max)).buffer;}
}
function server({loseAck=false,stall=false}={}){
 const s={bytes:[],connections:0,sends:0};
 s.connect=async()=>{const generation=++s.connections;const ws={readyState:1,close(){if(this.readyState!==1)return;this.readyState=3;queueMicrotask(()=>this.onclose?.());},send(frame){
  s.sends++;const offset=Number(new DataView(frame).getBigUint64(0)),data=new Uint8Array(frame,8);assert.ok(offset<=s.bytes.length);
  for(let i=0;i<data.length;i++){if(offset+i<s.bytes.length)assert.equal(s.bytes[offset+i],data[i]);else s.bytes.push(data[i]);}
  if(generation===1&&loseAck){this.close();return;}if(generation===1&&stall)return;
  queueMicrotask(()=>this.onmessage?.({data:JSON.stringify({type:'ack',bytes:s.bytes.length})}));
 }};setTimeout(()=>ws.onmessage?.({data:JSON.stringify({type:'ready',bytes:s.bytes.length})}),0);return ws;};return s;
}
function uploader(store,s,extra={}){return new ReliableUpload({id:'s',store,connect:s.connect,retryMs:1,sleep:()=>sleep(1),...extra});}
test('frame carries exact PCM offset and unchanged bytes',()=>{const f=audioFrame(48000,pcm('abcdef'));assert.equal(new DataView(f).getBigUint64(0),48000n);assert.equal(new TextDecoder().decode(new Uint8Array(f,8)),'abcdef');});
test('invalid offset and oversized chunk are rejected',()=>{assert.throws(()=>audioFrame(1,pcm('ab')));assert.throws(()=>audioFrame(0,new ArrayBuffer(48002)));});
test('dropped ACK reconnects and confirms saved audio without duplicates',async()=>{const store=new Store(),s=server({loseAck:true}),u=uploader(store,s);await u.enqueue(pcm('abcdef'));await u.finish();assert.equal(new TextDecoder().decode(new Uint8Array(s.bytes)),'abcdef');assert.equal(store.m.acked,6);assert.equal(s.connections,2);});
test('ACK stall is detected without silently claiming upload success',async()=>{const store=new Store(),s=server({stall:true}),states=[],u=uploader(store,s,{ackTimeoutMs:20,onState:x=>states.push(x)});await u.enqueue(pcm('abcdefgh'));await u.finish();assert.ok(s.connections>=2);assert.ok(states.some(x=>x.state==='reconnecting'&&x.queuedBytes>0));assert.equal(store.m.acked,8);});
test('manual stop rejects new samples but drains previously cached audio after recovery',async()=>{
 const store=new Store(),s=server();let online=false;const real=s.connect;s.connect=async()=>{if(!online)throw Error('offline');return real();};
 const u=uploader(store,s);await u.enqueue(pcm('abcdef'));const done=u.finish();await sleep(10);assert.equal(store.m.intent,'stopped');assert.equal(store.m.acked,0);await assert.rejects(store.enqueue('s',pcm('gh')),/stopped/);online=true;await done;assert.equal(store.m.acked,6);
});
test('page lifetime disposal keeps unacknowledged data for explicit later recovery',async()=>{
 const store=new Store(),s={connect:async()=>{throw Error('offline');}};const u=uploader(store,s);await u.enqueue(pcm('abcdef'));u.start();const rejected=assert.rejects(u.task,/暂停/);await sleep(5);u.dispose();await rejected;assert.equal(store.m.next,6);assert.equal(store.m.acked,0);const next=uploader(store,server());await next.finish();assert.equal(store.m.acked,6);
});
test('no automatic microphone start and no pagehide finish request',()=>{
 const source=fs.readFileSync(new URL('../../workbench/meeting/static/workbench.js',import.meta.url),'utf8'),transport=fs.readFileSync(new URL('../../workbench/meeting/static/reliable-upload.mjs',import.meta.url),'utf8');
 const hide=source.slice(source.indexOf("window.addEventListener('pagehide'"),source.indexOf('async function init'));
 assert.ok(!hide.includes('/finish'));assert.ok(hide.includes('transport?.dispose'));assert.ok(source.includes('beforeunload'));assert.ok(source.includes("$('resumeButton').onclick=()=>startMicrophone"));assert.ok(!transport.includes('navigator.mediaDevices'));assert.ok(source.includes('await cap.transport.finish()'));
});
test('outbox writes PCM before advancing next offset and removes only acknowledged chunks',()=>{
 const s=fs.readFileSync(new URL('../../workbench/meeting/static/audio-outbox.mjs',import.meta.url),'utf8');assert.ok(s.indexOf("tx.objectStore('chunks').put")<s.indexOf('m.next+=pcm.byteLength'));assert.ok(s.includes('row.value.offset+row.value.data.byteLength<=offset'));assert.ok(s.includes('tx.oncomplete=()=>resolve(value)'));
});
