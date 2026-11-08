import {escapeHTML as esc,formatTime} from './audio-utils.mjs';
import {subtitleLine,highlightOriginal,translationOptions} from './subtitle-ui.mjs';
export function renderRows(view){
 const options=translationOptions(view.translation.options);
 return view.transcripts.slice(-120).map(u=>`<article class="utterance"><div class="meta"><b>${esc(u.speaker||'未区分声音')}</b><time>${formatTime((u.start_ms||0)/1000)}</time></div><p class="original">${highlightOriginal(u,view.translation,options)}</p>${subtitleLine(u,view.translation,options)}</article>`).join('');
}
export function summary(view){const t=view.translation;return {active:t.active_requests||0,pending:t.pending||0,done:t.items.filter(x=>x.status==='translated').length,errors:t.errors||0};}
if(typeof document!=='undefined'){
 const $=id=>document.getElementById(id);let token=sessionStorage.getItem('tmcraParallelToken')||'',view=null,busy=false,polling=false,version=0,lastKey='';
 async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},...(body===undefined?{}:{body:JSON.stringify(body)})});const data=await r.json();if(!r.ok)throw Error(r.status===401?'连接已过期，请重新打开已授权的字幕入口。':typeof data.detail==='string'?data.detail:'请求未完成。');return data;}
 function draw(next){
  view=next;const t=view.translation,o=t.options,n=summary(view);
  $('meetingTitle').textContent=view.session.title||'当前会议';$('captureState').textContent=view.session.status==='recording'?'原录音仍在采集':'录音状态：'+view.session.status;
  for(const id of ['mode','language','toggle','retry'])$(id).disabled=busy;
  $('mode').value=o.mode;$('language').value=o.target_language;$('toggle').textContent=o.enabled?'暂停翻译':'开始翻译';$('retry').disabled=busy||!o.enabled||!n.errors;
  $('active').textContent=n.active+' / '+t.parallelism;$('pending').textContent=n.pending;$('done').textContent=n.done;$('errors').textContent=n.errors;
  $('status').textContent=view.source_error?'源会话暂时不可读，保留已有字幕。':!o.enabled?'并行翻译已暂停，原录音不受影响。':t.retry_after_seconds>0?`上游限流/暂时故障，${t.retry_after_seconds}s 后继续；原文保留。`:n.errors?`${n.errors} 句未完成，可手动重试；其他句继续并行处理。`:n.pending?`正在并行处理 ${n.pending} 句，最新句优先。`:'已跟上当前转写。';
  $('background').textContent='词汇背景：'+o.vocabulary.level+' · 已声明熟悉 '+o.vocabulary.known_words.length+' 项；沿用接入时的背景。';
  const key=JSON.stringify([view.transcripts.map(x=>[x.id,x.text,x.speaker]),t.items,o]);
  if(key!==lastKey){const top=$('rows').scrollTop;lastKey=key;$('rows').innerHTML=renderRows(view)||'<p class="empty">等待定稿原文。</p>';$('rows').scrollTop=$('follow').checked?$('rows').scrollHeight:top;}
 }
 async function change(next,retry=false){if(!view||busy)return;busy=true;version++;draw(view);try{draw(await api(retry?'/v1/parallel/retry':'/v1/parallel/options',retry?{}:next));}catch(e){$('status').textContent=e.message;}finally{busy=false;for(const id of ['mode','language','toggle'])$(id).disabled=false;$('retry').disabled=!view.translation.options.enabled||!view.translation.errors;}}
 for(const id of ['mode','language'])$(id).disabled=true;
 $('toggle').onclick=()=>view&&change({...view.translation.options,enabled:!view.translation.options.enabled});
 $('mode').onchange=()=>view&&change({...view.translation.options,mode:$('mode').value});
 $('language').onchange=()=>view&&change({...view.translation.options,target_language:$('language').value});
 $('retry').onclick=()=>change(null,true);
 async function poll(){if(polling||busy||!token)return;polling=true;const revision=version;try{const data=await api('/v1/parallel');if(revision===version&&!busy)draw(data);}catch(e){$('status').textContent=e.message;}finally{polling=false;}}
 async function init(){const code=new URLSearchParams(location.hash.slice(1)).get('login');if(code){history.replaceState(null,'',location.pathname);try{const data=await api('/v1/browser-login/exchange',{code});token=data.access_token;sessionStorage.setItem('tmcraParallelToken',token);}catch(e){$('status').textContent=e.message;}}if(token){await poll();setInterval(poll,750);}else $('status').textContent='等待已授权的字幕连接。';}
 init();
}
