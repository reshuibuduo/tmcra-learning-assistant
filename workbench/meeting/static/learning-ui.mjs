import {escapeHTML as esc,parseConcepts} from './audio-utils.mjs';

export function installLearningUI({api,post,requireConnection,health,getView,onReminder}) {
  const $=id=>document.getElementById(id);
  let draft=null,busy=false,pending=false,lastReminder='',polling=false;
  const statusNames={ready:'已写入 · 可关联',queued:'等待写入',indexing:'正在建立记忆索引',error:'尚未确认写入'};
  function error(e){$('learningError').textContent=e.message||String(e);}
  function controls(){ $('learningFileButton').disabled=busy;$('learningTranscriptButton').disabled=busy||!getView()?.transcripts?.length;$('learningConfirm').disabled=busy||!draft||!$('learningConsent').checked; }
  async function list(){
    if(polling)return;polling=true;
    try{
      const data=await api('/v1/learning/imports');pending=data.imports.some(x=>['queued','indexing'].includes(x.status));
      $('learningLibrary').innerHTML=data.imports.length?data.imports.map(x=>`<div class="learning-library-row"><div><b>${esc(x.title)}</b><small>${esc(x.provider)} · ${esc(statusNames[x.status]||x.status)}</small></div><button type="button" class="text-button" data-learning-source="${esc(x.id)}">原文</button>${x.status==='error'?`<button type="button" class="text-button" data-learning-recheck="${esc(x.id)}">重新检查</button>`:''}</div>`).join(''):'<p class="muted">还没有导入学习资料。选择一段真正想用于学习关联的对话即可。</p>';
      bindSources($('learningLibrary'));
      for(const b of $('learningLibrary').querySelectorAll('[data-learning-recheck]'))b.onclick=async()=>{try{await post('/v1/learning/imports/'+b.dataset.learningRecheck+'/recheck');await list();}catch(e){error(e);}};
      await health();
    }finally{polling=false;}
  }
  function bindSources(root){for(const b of root.querySelectorAll('[data-learning-source]'))b.onclick=()=>source(b.dataset.learningSource);}
  async function source(id){
    try{
      const row=await api('/v1/learning/imports/'+id);
      $('learningSourceTitle').textContent=row.title;
      $('learningSourceMeta').textContent=`${row.provider} · ${row.learning_date||'学习日期未知'} · ${({discussed:'曾讨论过',studied:'曾学习过',reviewed:'曾复习过'})[row.learning_state]||'状态未知'}（不代表已掌握）`;
      $('learningSourceText').textContent=row.messages.map((m,i)=>`[${i+1}] ${m.role==='assistant'?'助手':'用户／原文资料'}${m.timestamp?' · '+m.timestamp:''}\n${m.content}`).join('\n\n');
      $('learningSourceDialog').showModal();
    }catch(e){error(e);}
  }
  async function open(){if(!requireConnection())return;$('learningError').textContent='';controls();$('learningDialog').showModal();try{await list();}catch(e){error(e);}}
  async function preview(filename,content){
    busy=true;draft=null;$('learningError').textContent='';$('learningConsent').checked=false;controls();$('learningPreview').textContent='正在解析预览，尚未写入记忆…';
    try{
      draft=await post('/v1/learning/preview',{filename,content});
      $('learningPreview').innerHTML=`<p class="muted">找到 ${draft.conversations.length} 段对话，请选择要导入的内容。</p>`+draft.conversations.map(c=>`<section class="learning-preview-row"><label><input type="checkbox" data-learning-id="${esc(c.id)}" checked><span><b>${esc(c.title)}</b><small>${esc(c.provider)} · ${c.messages.length} 条 · ${esc(c.learning_date||'日期未知')}</small></span></label><details><summary>查看完整解析内容</summary><pre>${esc(c.messages.map(m=>`[${m.role}] ${m.content}`).join('\n\n'))}</pre></details></section>`).join('')+draft.warnings.map(w=>`<p class="muted">${esc(w)}</p>`).join('');
    }catch(e){$('learningPreview').textContent='预览未完成，没有写入记忆。';error(e);}finally{busy=false;controls();}
  }
  $('learningNav').onclick=$('learningFromProfile').onclick=$('learningFromMemory').onclick=open;
  $('learningClose').onclick=()=>$('learningDialog').close();$('learningSourceClose').onclick=()=>$('learningSourceDialog').close();
  $('learningFileButton').onclick=()=>$('learningFile').click();
  $('learningFile').onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;if(f.size>8*1024*1024){error(Error('文件需小于 8 MB，请拆分后导入。'));return;}try{await preview(f.name,await f.text());}catch(e){error(e);}};
  $('learningTranscriptButton').onclick=async()=>{const v=getView();if(!v?.transcripts?.length)return;const text='# '+v.session.title+'\n\n'+v.transcripts.map(u=>`[${u.source||'音频'} / ${u.speaker||'未知说话人'}]\n${u.text}`).join('\n\n');await preview(v.session.title+'.md',text);};
  $('learningConsent').onchange=controls;
  $('learningConfirm').onclick=async()=>{
    if(busy||!draft||!$('learningConsent').checked)return;
    const selected_ids=[...$('learningPreview').querySelectorAll('[data-learning-id]:checked')].map(x=>x.dataset.learningId);
    busy=true;controls();$('learningError').textContent='';
    try{
      const topics=parseConcepts($('learningTopics').value);if(topics.length>20)throw Error('最多填写 20 个主题。');
      const result=await post('/v1/learning/previews/'+draft.id+'/commit',{digest:draft.digest,selected_ids,learning_state:$('learningState').value,topics,confirmed:true});
      const ready=result.imports.every(x=>x.status==='ready');
      $('learningPreview').textContent=ready?'这些资料已经写入，无需重复导入。':'已提交索引任务。显示“已写入 · 可关联”后，才会用于学习提醒。';
      draft=null;$('learningConsent').checked=false;await list();
    }catch(e){error(e);}finally{busy=false;controls();}
  };
  setInterval(()=>{if(pending||$('learningDialog').open)list().catch(error);},3000);
  function renderReminder(event){
    if(event?.action!=='show'||!event.cards?.length)return;
    const key=JSON.stringify([event.updated_at,event.cards]);if(key===lastReminder)return;lastReminder=key;
    const card=event.cards[0];
    $('memoryResult').innerHTML=`<article class="memory-result" data-updated-at="${Number(event.updated_at)||0}"><small>联系过去的学习 · 有原文依据</small><h3>${esc(card.title)}</h3><p class="reminder-body">${esc(card.body)}</p>${card.citations.map(c=>`<blockquote>${esc(c.quote)}</blockquote>${c.import_id?`<button class="text-button" data-learning-source="${esc(c.import_id)}">查看「${esc(c.source_title||'学习记录')}」原文</button>`:''}`).join('')}${card.follow_up?`<p class="muted">可以继续想：${esc(card.follow_up)}</p>`:''}</article>`;
    bindSources($('memoryResult'));
    if(Date.now()/1000-(event.updated_at||0)<30)onReminder?.(card);
  }
  return {open,renderReminder};
}
