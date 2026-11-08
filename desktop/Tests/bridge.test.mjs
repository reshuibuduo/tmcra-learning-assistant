import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const script=fs.readFileSync(new URL('../Resources/bridge.js',import.meta.url),'utf8');
function page({ready=true,busy=false,demo=false,memoryTime=0}={}) {
  const messages=[], clicks=[];
  const elements={recordButton:{hidden:busy,disabled:false,click(){clicks.push('start');this.hidden=true;elements.stopButton.hidden=false;}},stopButton:{hidden:!busy,disabled:false,click(){clicks.push('stop');this.hidden=true;elements.recordButton.hidden=false;}},servicePill:{classList:{contains:()=>ready}},sessionTitle:{value:'fixture'}};
  const texts={'#sessionStatus':'fixture','#duration':'00:01','#conceptCard h3':'梯度下降','#conceptCard .concept-intuition':'直觉','#conceptCard .concept-example p':'例子','#memoryResult .memory-result h3':'联系以前的 Transformer 学习','#memoryResult .reminder-body':'你曾讨论过注意力缩放。'};
  const context={document:{documentElement:{dataset:{desktopDemo:String(demo)}},getElementById:id=>elements[id],querySelector:selector=>({textContent:texts[selector]||'',dataset:selector==='#memoryResult .memory-result'?{updatedAt:memoryTime}:{}})},getComputedStyle:()=>({display:'block'}),MutationObserver:class{observe(){}},window:{webkit:{messageHandlers:{desktop:{postMessage:m=>messages.push(m)}}}}};
  vm.runInNewContext(script,context);
  return {api:context.window.TMCRADesktop,elements,texts,messages,clicks};
}
test('publishes initial state without starting recording',()=>{const p=page();assert.equal(p.messages.length,1);assert.deepEqual(p.clicks,[]);assert.equal(p.api.snapshot().canStart,true)});
test('native start targets real page start control once',()=>{const p=page();assert.equal(p.api.command('start').accepted,true);assert.equal(p.api.command('start').accepted,false);assert.deepEqual(p.clicks,['start']);assert.equal(p.api.snapshot().captureBusy,true)});
test('native stop targets page stop even when main view is not visible',()=>{const p=page({busy:true});assert.equal(p.api.command('stop').accepted,true);assert.deepEqual(p.clicks,['stop']);assert.equal(p.api.snapshot().captureBusy,false)});
test('offline cannot start',()=>{const p=page({ready:false});assert.equal(p.api.command('start').accepted,false);assert.equal(p.clicks.length,0)});
test('disabled control cannot be invoked',()=>{const p=page();p.elements.recordButton.disabled=true;assert.equal(p.api.command('start').accepted,false)});
test('unsupported commands have no side effects',()=>{const p=page();assert.equal(p.api.command('deleteEverything').accepted,false);assert.equal(p.clicks.length,0)});
test('new card is published without native HTML rendering',()=>{const p=page();p.texts['#conceptCard h3']='<script>plain text</script>';assert.equal(p.api.publish().card.title,'<script>plain text</script>')});
test('demo is explicitly labeled, not reported as real recording',()=>{const p=page({demo:true});assert.equal(p.api.snapshot().demo,true)});
test('fresh grounded memory reminder is forwarded to native panel',()=>{const p=page({memoryTime:Date.now()/1000});assert.equal(p.api.snapshot().card.kind,'learning_memory');assert.equal(p.api.snapshot().card.title,'联系以前的 Transformer 学习')});
test('expired memory reminder does not suppress current concept',()=>{const p=page({memoryTime:Date.now()/1000-60});assert.equal(p.api.snapshot().card.kind,'concept')});
test('bridge never reads auth storage or sends network requests',()=>{assert.doesNotMatch(script,/sessionStorage|localStorage|fetch\(|XMLHttpRequest|api_key|access_token/)});
test('native architecture retains independent panel and explicit media prompt',()=>{const src=fs.readFileSync(new URL('../Sources/main.swift',import.meta.url),'utf8');for(const contract of ['panel.level = .floating','panel.hidesOnDeactivate = false','.canJoinAllSpaces,.fullScreenAuxiliary','mainWindow.miniaturize(nil);showPanel()','decisionHandler(.prompt)','UserDefaults.standard.set(hintsVisible','setMicrophoneCaptureState(.none)'])assert.ok(src.includes(contract),contract);assert.ok(!src.includes('decisionHandler(.grant)'))});
