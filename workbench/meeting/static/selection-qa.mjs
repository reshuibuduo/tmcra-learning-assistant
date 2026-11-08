import {escapeHTML as esc} from './audio-utils.mjs';
import {createStreamTiming} from './question-stream.mjs';

export function makeQuestion(selection,question,requestId){
  if(!selection||!selection.sessionId||!selection.utteranceId)throw Error('请先划选一段字幕。');
  const quote=selection.quote?.trim(),value=question?.trim();
  if(!quote||quote.length>1200)throw Error('请在一条原文或译文里选择 1–1200 个字符。');
  if(!value||value.length>600)throw Error('请输入 1–600 个字符的问题。');
  if(!['original','translation'].includes(selection.source))throw Error('请选择原文或译文。');
  return {request_id:requestId,utterance_id:selection.utteranceId,source:selection.source,quote,question:value};
}
export function selectedQuote(selection,root,sessionId){
  if(!sessionId||!selection||selection.isCollapsed||!selection.rangeCount)return null;
  const element=node=>node?.nodeType===1?node:node?.parentElement;
  const first=element(selection.anchorNode)?.closest('p[data-qa-source]');
  const last=element(selection.focusNode)?.closest('p[data-qa-source]');
  if(!first||first!==last||!root.contains(first))return null;
  const article=first.closest('[data-utterance]'),quote=selection.toString().trim();
  if(!article||!quote||quote.length>1200)return null;
  return {sessionId,utteranceId:article.dataset.utterance,source:first.dataset.qaSource,quote};
}
export function answerHTML(answer){
  if(!answer)return '';
  return `<div class="qa-answer-meta">${answer.status==='needs_context'?'还需要上下文':'回答'} · ${esc(answer.profile_level==='technical'?'深入':answer.profile_level==='standard'?'标准':'通俗')} · ${Number.isFinite(answer.elapsed_seconds)?'服务端 '+answer.elapsed_seconds.toFixed(1)+'s':''}</div><p class="qa-answer-text">${esc(answer.answer)}</p>${answer.example?`<div class="qa-example"><b>举个例子</b><p>${esc(answer.example)}</p></div>`:''}<div class="qa-references"><small>课堂原文依据</small>${(answer.citations||[]).map(c=>`<button type="button" data-qa-reference="${esc(c.utterance_id)}">“${esc(c.quote)}”</button>`).join('')}</div><p class="qa-provenance">结合课堂上下文与通用知识 · 未查询历史记忆<br>问答仅保留在本次课堂，不自动写入长期记忆。</p>`;
}

export function installSelectionQA({getView,streamPost,onOpen,onRelease,jumpToQuote,document=globalThis.document,window=globalThis.window}){
  const $=id=>document.getElementById(id),root=$('transcriptContent'),toolbar=$('selectionToolbar');
  let pending=null,draft=null,busy=false,dragging=false,sessionId=null,generation=0,lastAttempt=null,historyKey='',activeRequest=null;
  const liveSelection=()=>window.getSelection();
  function selected(){return selectedQuote(liveSelection(),root,getView()?.session.id);}
  function holdingTranscript(){const s=liveSelection();return dragging||!!(s&&!s.isCollapsed&&root.contains(s.anchorNode));}
  function showSelection(){
    const next=selected();
    if(!next){toolbar.hidden=true;pending=null;if(!holdingTranscript())onRelease();return;}
    pending=next;const box=liveSelection().getRangeAt(0).getBoundingClientRect();
    toolbar.style.left=Math.max(8,Math.min(window.innerWidth-170,box.left))+'px';
    toolbar.style.top=Math.max(8,Math.min(window.innerHeight-48,box.top-43))+'px';
    toolbar.hidden=false;
  }
  function update(){
    $('selectionAsk').disabled=busy;
    $('qaSend').disabled=busy||!draft;$('qaQuestion').disabled=busy;
    for(const button of document.querySelectorAll('[data-qa-prompt]'))button.disabled=busy||!draft;
    $('qaSend').textContent=busy?'正在回答…':'发送问题';
    $('qaCancel').hidden=!busy;
    $('qaQuote').textContent=draft?.quote||'在左侧原文或译文中划选文字，再点“提问”。';
    $('qaQuoteKind').textContent=draft?(draft.source==='translation'?'选中的译文':'选中的原文'):'先选一句';
    $('qaLocate').hidden=!draft;$('qaStatus').textContent=busy?'正在结合原句、相邻上下文和学习背景回答；不会改变录音状态。':'';
  }
  function open(){
    if(busy)return;
    const value=pending||selected();if(!value)return;
    draft={...value};lastAttempt=null;$('qaQuestion').value='';$('qaError').textContent='';resetStreamView();
    toolbar.hidden=true;liveSelection()?.removeAllRanges();pending=null;onOpen();update();onRelease();
    $('questionPane').scrollIntoView({block:'nearest',behavior:'smooth'});$('qaQuestion').focus();
  }
  function renderAnswer(answer){
    $('qaAnswer').innerHTML=answerHTML(answer);
    for(const button of $('qaAnswer').querySelectorAll('[data-qa-reference]'))button.onclick=()=>jumpToQuote(button.dataset.qaReference);
  }
  function resetStreamView(){
    $('qaAnswer').innerHTML='';$('qaAnswer').setAttribute('aria-busy','false');
    for(const key of Object.keys($('qaAnswer').dataset))delete $('qaAnswer').dataset[key];
  }
  function renderDelta(field,text){
    const root=$('qaAnswer');
    if(!root.querySelector('.qa-partial'))root.innerHTML='<div class="qa-answer-meta qa-partial">正在生成 · 引用尚待核验</div><p class="qa-answer-text"></p><div class="qa-example" hidden><b>举个例子</b><p></p></div>';
    const p=root.querySelector(field==='answer'?'.qa-answer-text':'.qa-example p');p.textContent+=text;
    if(p.textContent.trim())p.dataset.hasText='true';
    if(field==='example')root.querySelector('.qa-example').hidden=false;
    root.dataset.streamPhase='streaming';
  }
  function renderTiming(timing){
    const root=$('qaAnswer');root.dataset.streamUpdates=String(timing.updates);root.dataset.streamCached=String(timing.cached);root.dataset.timingVisibility=timing.visibility;
    for(const [key,value] of Object.entries(timing))if(key.endsWith('_ms'))root.dataset[key.replace(/_([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);
    let label=root.querySelector('.qa-client-timing');
    if(!label){label=document.createElement('p');label.className='qa-client-timing';root.append(label);}
    label.textContent=timing.cached?'已保存的回答 · 不计入首字延迟':
      [Number.isFinite(timing.first_display_ms)?'首字显示 '+(timing.first_display_ms/1000).toFixed(2)+'s':null,
       Number.isFinite(timing.complete_display_ms)?'完整显示 '+(timing.complete_display_ms/1000).toFixed(2)+'s':null].filter(Boolean).join(' · ');
  }
  async function send(){
    if(busy)return;const view=getView();if(!draft||view?.session.id!==draft.sessionId)return;
    let body;
    try{
      const value=makeQuestion(draft,$('qaQuestion').value,'pending-id');
      const key=JSON.stringify(value);
      if(!lastAttempt||lastAttempt.key!==key)lastAttempt={key,id:window.crypto.randomUUID()};
      body={...value,request_id:lastAttempt.id};
    }catch(error){$('qaError').textContent=error.message;return;}
    const current=++generation,sid=draft.sessionId;busy=true;$('qaError').textContent='';$('qaAnswer').innerHTML='';update();
    resetStreamView();const controller=new window.AbortController();activeRequest=controller;
    const root=$('qaAnswer');root.dataset.requestId=body.request_id;root.dataset.streamPhase='waiting';root.setAttribute('aria-busy','true');
    const currentRequest=()=>current===generation&&getView()?.session.id===sid;
    const timing=createStreamTiming({now:()=>window.performance.now(),visibility:()=>document.visibilityState,
      afterPaint:fn=>window.requestAnimationFrame(()=>window.requestAnimationFrame(()=>{if(currentRequest())fn();})),
      onUpdate:value=>{if(currentRequest())renderTiming(value);}});
    try{
      const answer=await streamPost('/v1/sessions/'+sid+'/questions/stream',body,event=>{
        if(!currentRequest())return;
        if(event.type==='start'){timing.cached(event.cached);return;}
        if(event.type==='answer_delta'||event.type==='example_delta'){
          renderDelta(event.type==='answer_delta'?'answer':'example',event.text);timing.delta(event.text);
        }else if(event.type==='done'){
          renderAnswer(event.answer);root.dataset.streamPhase='complete';root.setAttribute('aria-busy','false');renderTiming(timing.state);timing.done();
        }
      },controller.signal);
      if(current!==generation||getView()?.session.id!==sid)return;
      const now=getView();now.questions=[...(now.questions||[]).filter(q=>q.request_id!==answer.request_id),answer];renderHistory(now.questions);
    }catch(error){if(current===generation){
      $('qaError').textContent=(error.name==='AbortError'?'已停止本次回答；已显示内容尚未完成。':error.message)+' 原文和问题已保留，可点“发送问题”重试。';
      root.dataset.streamPhase='error';root.setAttribute('aria-busy','false');const meta=root.querySelector('.qa-answer-meta');if(meta)meta.textContent='未完成 · 内容尚未核验';
    }}
    finally{if(current===generation){activeRequest=null;busy=false;update();}}
  }
  function renderHistory(rows=[]){
    const key=JSON.stringify(rows);if(key===historyKey)return;historyKey=key;
    $('qaHistory').innerHTML=rows.length?'<h4>本次课堂的提问</h4>'+rows.slice(-8).reverse().map((q,i)=>`<button type="button" data-qa-history="${i}">${esc(q.question)}</button>`).join(''):'';
    for(const button of $('qaHistory').querySelectorAll('[data-qa-history]'))button.onclick=()=>{
      if(busy)return;const q=rows.slice(-8).reverse()[Number(button.dataset.qaHistory)];
      draft={sessionId,utteranceId:q.selection.utterance_id,source:q.selection.source,quote:q.selection.quote};
      $('qaQuestion').value=q.question;$('qaError').textContent='';lastAttempt=null;resetStreamView();renderAnswer(q);update();
    };
  }
  function sync(view){
    const next=view?.session.id||null;
    if(next!==sessionId){activeRequest?.abort();activeRequest=null;sessionId=next;generation++;busy=false;draft=null;pending=null;dragging=false;lastAttempt=null;historyKey='';toolbar.hidden=true;$('qaQuestion').value='';$('qaError').textContent='';resetStreamView();update();}
    renderHistory(view?.questions||[]);
  }
  root.addEventListener('pointerdown',()=>{dragging=true;});
  document.addEventListener('pointerup',()=>{dragging=false;showSelection();});
  document.addEventListener('pointercancel',()=>{dragging=false;showSelection();});
  document.addEventListener('selectionchange',showSelection);
  window.addEventListener('scroll',()=>{if(pending)showSelection();},true);
  $('selectionAsk').addEventListener('pointerdown',e=>e.preventDefault());$('selectionAsk').onclick=open;
  $('qaForm').onsubmit=e=>{e.preventDefault();send();};
  $('qaCancel').onclick=()=>activeRequest?.abort();
  $('qaLocate').onclick=()=>{if(draft)jumpToQuote(draft.utteranceId);};
  for(const button of document.querySelectorAll('[data-qa-prompt]'))button.onclick=()=>{$('qaQuestion').value=button.dataset.qaPrompt;send();};
  update();return {holdingTranscript,sync};
}
