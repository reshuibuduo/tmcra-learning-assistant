import test from 'node:test';
import assert from 'node:assert/strict';
import {SSEDecoder,readQuestionStream,createStreamTiming} from '../../workbench/meeting/static/question-stream.mjs';
const id='qa-stream-test',body={request_id:id},encoder=new TextEncoder();
const event=(type,extra={})=>({type,request_id:id,...extra});
const answer={request_id:id,answer:'先出字，再完成。',citations:[]};
const done=event('done',{answer});
const frame=e=>'data: '+JSON.stringify(e)+'\n\n';
function response(parts){return new Response(new ReadableStream({start(c){for(const p of parts)c.enqueue(typeof p==='string'?encoder.encode(p):p);c.close();}}),{headers:{'Content-Type':'text/event-stream'}});}
const read=(parts,onEvent=()=>{},options={})=>readQuestionStream(async()=>response(parts),'/questions/stream',body,'TEST',onEvent,options);
test('SSE frames accept split CRLF, comments and multiline data',()=>{
  const decoder=new SSEDecoder();assert.deepEqual(decoder.feed(': comment\r\ndata: {"x":\r\ndata: 1}\r\n'),[]);
  assert.deepEqual(decoder.feed('\r\n'),[{x:1}]);
});
test('split UTF8 characters arrive as exact prose, not JSON syntax',async()=>{
  const bytes=encoder.encode(frame(event('answer_delta',{text:'中文😀'}))+frame(done));let text='';
  const result=await read([...bytes].map(b=>new Uint8Array([b])),e=>{if(e.type==='answer_delta')text+=e.text;});
  assert.equal(text,'中文😀');assert.deepEqual(result,answer);
});
test('first delta callback runs before a blocked final response',async()=>{
  let control,release,seenResolve;const seen=new Promise(r=>seenResolve=r);
  const stream=new ReadableStream({start(c){control=c;c.enqueue(encoder.encode(frame(event('answer_delta',{text:'先出字'}))));}});
  let complete=false;
  const task=readQuestionStream(async()=>new Response(stream,{headers:{'Content-Type':'text/event-stream'}}),'/stream',body,'TEST',e=>{if(e.type==='answer_delta')seenResolve(e.text);}).then(x=>{complete=true;return x;});
  assert.equal(await seen,'先出字');assert.equal(complete,false);
  control.enqueue(encoder.encode(frame(done)));control.close();assert.deepEqual(await task,answer);
});
test('stream request sends auth once, no provider token or retry',async()=>{
  let calls=0;await readQuestionStream(async(path,init)=>{calls++;assert.equal(path,'/stream');assert.equal(init.headers.Authorization,'Bearer TEST');assert.equal(init.headers.Accept,'text/event-stream');assert.equal(JSON.parse(init.body).request_id,id);return response([frame(done)]);},'/stream',body,'TEST',()=>{});
  assert.equal(calls,1);
});
test('EOF before final confirmation is rejected',async()=>assert.rejects(read([frame(event('answer_delta',{text:'未完成'}))]),/提前结束/));
test('SSE semantic 402 is surfaced although transport is HTTP 200',async()=>{
  await assert.rejects(read([frame(event('error',{code:402,message:'余额不足'}))]),e=>e.code===402&&e.message==='余额不足');
});
test('HTTP failure is not retried or treated as stream',async()=>{
  let calls=0;await assert.rejects(readQuestionStream(async()=>{calls++;return new Response(JSON.stringify({detail:'余额不足'}),{status:402});},'/',body,'TEST',()=>{}),/余额不足/);assert.equal(calls,1);
});
test('cross-request response and malformed deltas are rejected',async()=>{
  await assert.rejects(read([frame({...done,request_id:'other'})]),/编号不匹配/);
  await assert.rejects(read([frame(event('answer_delta',{text:{html:'x'}}))]),/片段格式异常/);
});
test('incomplete final schema is rejected',async()=>assert.rejects(read([frame(event('done',{answer:{request_id:id,answer:'x'}}))]),/完整回答格式异常/));
test('batch JSON fallback is rejected',async()=>assert.rejects(readQuestionStream(async()=>new Response('{}',{headers:{'Content-Type':'application/json'}}),'/',body,'TEST',()=>{}),/没有返回流式回答/));
test('invalid UTF8 and oversize SSE frames are rejected',async()=>{
  await assert.rejects(read([new Uint8Array([0xff])]),TypeError);
  assert.throws(()=>new SSEDecoder().feed('x'.repeat(131073)),/数据过长/);
});
function abortFetch(path,{signal}){return new Promise((resolve,reject)=>{const fail=()=>reject(new DOMException('aborted','AbortError'));if(signal.aborted)fail();else signal.addEventListener('abort',fail,{once:true});});}
test('stop-answer abort propagates to fetch independently of recording',async()=>{
  const controller=new AbortController();const task=readQuestionStream(abortFetch,'/',body,'TEST',()=>{},{signal:controller.signal});controller.abort();await assert.rejects(task,e=>e.name==='AbortError');
});
test('deadline abort reports incomplete answer, not success',async()=>assert.rejects(readQuestionStream(abortFetch,'/',body,'TEST',()=>{},{timeoutMs:5}),/超时/));
test('timing measures first nonblank text and post-paint before completion',()=>{
  let now=100;const paints=[],updates=[];const timing=createStreamTiming({now:()=>now,afterPaint:fn=>paints.push(fn),onUpdate:s=>updates.push({...s}),visibility:()=>'visible'});
  now=110;timing.delta(' ');assert.equal(timing.state.first_text_ms,undefined);
  now=150;timing.delta('首');assert.equal(timing.state.first_text_ms,50);assert.equal(timing.state.first_display_ms,undefined);
  now=166;paints.shift()();assert.equal(timing.state.first_display_ms,66);
  now=200;timing.delta('字');assert.equal(timing.state.first_text_ms,50);assert.equal(timing.state.updates,3);
  now=500;timing.done();now=516;paints.shift()();assert.equal(timing.state.complete_received_ms,400);assert.equal(timing.state.complete_display_ms,416);assert.equal(timing.state.visibility,'visible');
});
test('cached replay is explicit and does not create fake first-text timing',()=>{
  const t=createStreamTiming({now:()=>10,afterPaint:f=>f(),onUpdate:()=>{},visibility:()=>'visible'});t.cached(true);t.done();assert.equal(t.state.cached,true);assert.equal(t.state.first_display_ms,undefined);assert.equal(t.state.updates,0);
});
