import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../workbench/meeting/static/workbench.js',import.meta.url),'utf8');
function fn(name,next){return source.slice(source.indexOf('async function '+name+'('),source.indexOf('async function '+next+'('));}
function harness(){
 const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{value:'meeting',textContent:'',className:'',innerHTML:''});return elements.get(id);};
 const timers=[],starts=[];const context={S:{capture:null,processing:false},$,requireConnection:()=>true,updateControls(){},window:{isSecureContext:true},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{addEventListener(){}}]})}},audioOutbox:{db:async()=>{}},prepareReliableCapture:async cap=>{cap.transport={enqueue:async()=>{},finish:async()=>{}};},drawAudio(){},
  AudioContext:class{constructor(){this.audioWorklet={addModule:async()=>{}};}async resume(){}createMediaStreamSource(){return {connect(){return {connect(){return {connect(){}};}};}};}createGain(){return {gain:{value:1}};}},
  AudioWorkletNode:class{constructor(){this.port={};}},startSession:async(...args)=>{starts.push(args);return 'sid';},audioSocket:async()=>({readyState:1}),setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},sendPCM(){},finishCapture:async cap=>{cap.finished=true;},toast(){},Error};
 return {context,timers,starts,$};
}
test('microphone creates manual session without duration timer',async()=>{
 const h=harness();await vm.runInNewContext(fn('startMicrophone','stopCapture')+';startMicrophone()',h.context);
 assert.equal(h.starts[0][0],'live_capture');assert.equal(h.starts[0][1],null);assert.equal(h.timers.length,0);assert.equal(h.context.S.capture.finished,undefined);
 assert.match(h.$('captureHint').textContent,/手动点击/);
});
test('virtual two-hour passage does not finish live capture',async()=>{
 const h=harness();await vm.runInNewContext(fn('startMicrophone','stopCapture')+';startMicrophone()',h.context);
 for(const timer of h.timers.filter(t=>t.ms<=7200000))await timer.fn();assert.ok(h.context.S.capture);assert.equal(h.context.S.capture.finished,undefined);
});
test('manual button path still finishes',async()=>{
 const h=harness();await vm.runInNewContext(fn('startMicrophone','stopCapture')+';startMicrophone()',h.context);
 h.context.S.capture.node=null;
 await vm.runInNewContext(fn('stopCapture','saveProfile')+';stopCapture()',h.context);
 assert.equal(h.context.S.capture.finished,true);assert.equal(h.context.S.capture.stopping,true);
});
test('session payload carries manual duration and continuation',async()=>{
 const h=harness();let sent;Object.assign(h.context,{continuationId:'a'.repeat(32),emptyConcept:'',renderView(){},listSessions:async()=>{},post:async(path,body)=>{sent=body;return {session:{id:'sid'}};}});h.context.S.dismissed=new Set();
 await vm.runInNewContext(fn('startSession','audioSocket')+";startSession('live_capture',900,'meeting')",h.context);
 assert.equal(sent.seconds,null);assert.equal(sent.continuation_of,'a'.repeat(32));
});
