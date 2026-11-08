(() => {
  'use strict';
  if (window.TMCRADesktop) return;
  const get = id => document.getElementById(id);
  const text = (selector, limit=450) => (document.querySelector(selector)?.textContent || '').trim().slice(0,limit);
  const visible = element => !!element && !element.hidden && getComputedStyle(element).display !== 'none';
  function snapshot() {
    const record=get('recordButton'), stop=get('stopButton'), busy=visible(stop);
    const demo=document.documentElement.dataset.desktopDemo==='true';
    const connected=demo||!!get('servicePill')?.classList.contains('connected');
    const memory=document.querySelector('#memoryResult .memory-result');
    const memoryTime=Number(memory?.dataset?.updatedAt||0), conceptTime=Number(get('conceptCard')?.dataset?.updatedAt||0);
    const useMemory=memoryTime>conceptTime&&Date.now()/1000-memoryTime<30;
    const card=useMemory?{kind:'learning_memory',title:text('#memoryResult .memory-result h3',80),
      explanation:text('#memoryResult .reminder-body'),example:text('#memoryResult blockquote',250),quote:''}:
      {kind:'concept',title:text('#conceptCard h3',80),explanation:text('#conceptCard .concept-intuition'),
       example:text('#conceptCard .concept-example p',250),quote:text('#conceptCard .source-quote',200)};
    return {type:'state', connected, demo, captureBusy:busy,
      canStart:connected&&!busy&&visible(record)&&!record.disabled,
      canStop:busy&&!stop.disabled,
      status:text('#sessionStatus',180)||'等待连接', duration:text('#duration',12)||'00:00',
      title:(get('sessionTitle')?.value||'新的课堂').slice(0,120),
      card};
  }
  let last='';
  function publish(force=false) {
    const state=snapshot(), encoded=JSON.stringify(state);
    if(force||last!==encoded) { last=encoded; window.webkit?.messageHandlers?.desktop?.postMessage(state); }
    return state;
  }
  function command(action) {
    const state=snapshot();
    const id=action==='start'&&state.canStart?'recordButton':action==='stop'&&state.canStop?'stopButton':null;
    if(!id) return {accepted:false,reason:'control_not_ready'};
    get(id).click(); publish(true);
    return {accepted:true};
  }
  window.TMCRADesktop=Object.freeze({snapshot,publish,command});
  new MutationObserver(()=>publish()).observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
  publish(true);
})();
