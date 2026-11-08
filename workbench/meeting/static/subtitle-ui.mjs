import {escapeHTML as esc} from './audio-utils.mjs';
import {normalizeVocabulary,vocabularyFromFields,VOCABULARY_LEVELS,VOCABULARY_STORAGE_KEY} from './vocabulary-profile.mjs';
export const TRANSLATION_DEFAULT={enabled:false,target_language:'zh-CN',mode:'full'};
export const TRANSLATION_LANGUAGES={'zh-CN':'简体中文',en:'English'};
export const TRANSLATION_MODES={full:'整句翻译',assisted:'整句 + 生词',vocabulary:'仅生词提示'};
export function concurrencyNote(data){
  const limit=data?.parallelism,active=data?.active_requests;
  const window=Number.isFinite(data?.window_seconds)&&data.window_seconds>0?`${data.window_seconds}s 滚动窗口 · `:'';
  return window+(Number.isInteger(limit)&&limit>1?`${Number.isInteger(active)?Math.max(0,Math.min(limit,active)):0}/${limit} 路处理中 · `:'');
}
export function translationOptions(value){
  let vocabulary;try{vocabulary=normalizeVocabulary(value?.vocabulary);}catch{vocabulary=normalizeVocabulary();}
  return {enabled:value?.enabled===true,target_language:Object.hasOwn(TRANSLATION_LANGUAGES,value?.target_language)?value.target_language:'zh-CN',mode:Object.hasOwn(TRANSLATION_MODES,value?.mode)?value.mode:'full',vocabulary};
}
export function matchingSubtitle(utterance,translation,options){
  return translation?.items?.find(row=>row.utterance_id===utterance.id&&row.source_text===utterance.text&&row.target_language===options.target_language&&row.mode===options.mode&&(!row.speaker||row.speaker===utterance.speaker)&&(options.mode==='full'||JSON.stringify(row.vocabulary)===JSON.stringify(options.vocabulary)));
}
export function highlightOriginal(utterance,translation,options){
  const item=options.enabled&&options.mode!=='full'&&matchingSubtitle(utterance,translation,options),chars=Array.from(utterance.text);
  if(item?.status!=='translated')return esc(utterance.text);
  const terms=(item.glosses||[]).filter(g=>Number.isInteger(g.start)&&Number.isInteger(g.end)&&g.start>=0&&g.end>g.start&&g.end<=chars.length&&chars.slice(g.start,g.end).join('')===g.term).sort((a,b)=>a.start-b.start||b.end-a.end);
  let out='',offset=0;
  for(const g of terms){if(g.start<offset)continue;out+=esc(chars.slice(offset,g.start).join(''))+`<mark class="vocabulary-term" title="${esc(g.meaning)}">${esc(g.term)}</mark>`;offset=g.end;}
  return out+esc(chars.slice(offset).join(''));
}
export function subtitleLine(utterance,translation,options){
  if(!options.enabled)return '';
  const item=matchingSubtitle(utterance,translation,options);
  const ready=item?.status==='translated'&&typeof item.text==='string'&&(options.mode==='vocabulary'||item.text.trim());
  const failed=item?.status==='error';
  const state=ready?'translated':failed?'error':'pending';
  const note=ready?(options.mode==='vocabulary'?'仅生词 · 原句保持英文':item.same_language?'原文即目标语言':TRANSLATION_LANGUAGES[options.target_language]+' · 译文'):failed?(item.error==='timeout'?'字幕处理超时，原文保留':item.error==='source_too_long'?'原句过长，原文保留':'字幕处理失败，原文保留'):options.mode==='vocabulary'?'正在挑选值得提示的词…':'等待译文…';
  const delay=ready&&Number.isFinite(item.after_asr_ms)?`<span class="subtitle-latency">定稿后 +${(Math.max(0,item.after_asr_ms)/1000).toFixed(1)}s</span>`:'';
  const glosses=ready&&options.mode!=='full'?(item.glosses||[]).slice(0,3):[];
  const wordHTML=glosses.length?`<div class="vocabulary-glosses">${glosses.map(g=>`<div class="vocabulary-gloss"><b>${esc(g.term)}</b><span>${esc(g.meaning)}</span>${g.explanation?`<small>${esc(g.explanation)}</small>`:''}</div>`).join('')}</div>`:ready&&options.mode==='vocabulary'?'<span class="vocabulary-empty">这句暂无需要提示的词。</span>':'';
  return `<div class="subtitle-line ${state}" data-translation-state="${state}"><div class="subtitle-meta"><span>${esc(note)}</span>${delay}</div>${ready&&options.mode!=='vocabulary'?`<p data-qa-source="translation">${esc(item.text)}</p>`:''}${wordHTML}</div>`;
}
export function installSubtitleUI({document=globalThis.document,getView,getOptions,setOptions,post,onView,onRender,toast,getStorage=()=>globalThis.localStorage}){
  const $=id=>document.getElementById(id);let busy=false;
  function render(){
    const options=getOptions(),view=getView(),data=view?.translation;
    $('translationToggle').setAttribute('aria-checked',String(options.enabled));
    $('translationToggle').textContent=options.enabled?'字幕翻译已开启':'开启字幕翻译';
    $('translationToggle').disabled=busy;$('translationTarget').disabled=busy;$('translationTarget').value=options.target_language;
    $('translationMode').disabled=busy;$('translationMode').value=options.mode;
    $('vocabularySave').disabled=busy;$('vocabularyButton').disabled=busy;$('profileVocabularyButton').disabled=busy;
    $('vocabularySummary').textContent='词汇背景：'+VOCABULARY_LEVELS[options.vocabulary.level]+(options.vocabulary.estimated_size!==null?' · 自报约 '+options.vocabulary.estimated_size+' 词':'')+' · 已知词 '+options.vocabulary.known_words.length+' 项';
    const retry=options.enabled&&view&&(data?.errors>0||data?.pending>0&&!data?.running);
    $('translationRetry').hidden=!retry;$('translationRetry').disabled=busy;
    $('translationRetry').textContent=data?.errors?'重试失败字幕':'继续翻译';
    $('translationStatus').textContent=(options.enabled?concurrencyNote(data):'')+(busy?'正在更新字幕设置…':!options.enabled?'原文照常显示；开启后合并短窗、结合上下文翻译已有和新增定稿字幕，不播放语音。':!view?'已就绪；下一课堂将显示原文与译文，不播放语音。':data?.errors?`${data.errors} 句翻译未完成；原文保留，可手动重试。`:data?.pending?data.running?`${data.pending} 句等待译文；转写与学习提示继续更新。`:'翻译任务未运行，点击“继续翻译”恢复。':view.transcripts?.length?'译文已更新 · 仅文字，不播放语音':'等待定稿原文；语音识别完成后按滚动窗口翻译。');
  }
  async function change(next){
    if(busy)return;busy=true;render();
    try{
      const view=getView();
      if(view){const updated=await post('/v1/sessions/'+view.session.id+'/translation',next);setOptions(translationOptions(updated.session.translation));onView(updated);}
      else setOptions(translationOptions(next));
      onRender();return {ok:true};
    }catch(e){toast(e.message||'字幕设置未更新，请稍后重试。',true);return {ok:false,error:e.message};}
    finally{busy=false;render();}
  }
  $('translationToggle').onclick=()=>change({...getOptions(),enabled:!getOptions().enabled});
  $('translationTarget').onchange=()=>change({...getOptions(),target_language:$('translationTarget').value});
  $('translationMode').onchange=()=>change({...getOptions(),mode:$('translationMode').value});
  function openVocabulary(){
    const profile=getOptions().vocabulary;$('englishVocabLevel').value=profile.level;$('englishVocabSize').value=profile.estimated_size===null?'':String(profile.estimated_size);$('knownEnglishWords').value=profile.known_words.join('、');$('vocabularyError').textContent='';$('vocabularyDialog').showModal();
  }
  $('vocabularyButton').onclick=$('profileVocabularyButton').onclick=openVocabulary;
  $('vocabularyForm').onsubmit=async e=>{
    e.preventDefault();if(busy)return;$('vocabularyError').textContent='';
    try{
      const vocabulary=vocabularyFromFields($('englishVocabLevel').value,$('englishVocabSize').value,$('knownEnglishWords').value);
      const result=await change({...getOptions(),vocabulary});
      if(!result?.ok){$('vocabularyError').textContent=result?.error||'保存未完成，请重试。';return;}
      let cached=true;try{getStorage().setItem(VOCABULARY_STORAGE_KEY,JSON.stringify(vocabulary));}catch{cached=false;}
      $('vocabularyDialog').close();toast(cached?'已更新英语词汇背景；专业知识档案保持不变。':'已应用于当前页面；浏览器未保留默认词汇背景。',!cached);
    }catch(e){$('vocabularyError').textContent=e.message;}
  };
  $('translationRetry').onclick=async()=>{
    if(busy||!getOptions().enabled||!getView())return;busy=true;render();
    try{onView(await post('/v1/sessions/'+getView().session.id+'/translation/retry'));onRender();}catch(e){toast(e.message,true);}finally{busy=false;render();}
  };
  render();return {render};
}
