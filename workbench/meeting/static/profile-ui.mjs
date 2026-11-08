import {PROFILE_PROMPT,LEVEL_LABELS,parseProfileReply,validateProfile} from './profile-transfer.mjs';
import {parseConcepts} from './audio-utils.mjs';

export async function copyPrompt({clipboard,document,textarea,details}){
  try{if(clipboard?.writeText){await clipboard.writeText(PROFILE_PROMPT);return true;}}catch{}
  // WebView clipboard APIs vary. The fallback still runs only on a user click.
  details.open=true;textarea.focus();textarea.select();
  try{return document.execCommand?.('copy')===true;}catch{return false;}
}
export function installProfileUI({document=globalThis.document,clipboard=()=>globalThis.navigator?.clipboard,getProfile,onCommit,onSaved}){
  const $=id=>document.getElementById(id),dialog=$('profileDialog');
  let draft=null,previewInput='',busy=false,openedProfile=null;
  $('profilePromptText').value=PROFILE_PROMPT;
  function resetPreview(){draft=null;previewInput='';$('profileImportPreview').hidden=true;$('profileImportConfirm').disabled=true;$('profileImportError').textContent='';}
  function fill(profile){$('profileLevel').value=profile.level;$('knownConcepts').value=profile.known_concepts.join('、');$('mutedConcepts').value=profile.muted_concepts.join('、');}
  function reset(){resetPreview();$('profileReply').value='';$('profileCopyStatus').textContent='';$('profilePromptDetails').open=false;$('profileError').textContent='';}
  function setBusy(value){busy=value;for(const el of dialog.querySelectorAll('button,input,textarea,select'))el.disabled=value;$('profileImportConfirm').disabled=value||!draft;}
  function preview(){
    resetPreview();
    try{
      draft=parseProfileReply($('profileReply').value);previewInput=$('profileReply').value;
      const previous=getProfile();
      for(const [id,field] of [['Level','level'],['Known','known_concepts'],['Muted','muted_concepts']]){
        const label=p=>field==='level'?LEVEL_LABELS[p.level]:(p[field].join('、')||'（空）');
        $('profileBefore'+id).textContent=label(previous);$('profileAfter'+id).textContent=label(draft);
      }
      $('profileImportPreview').hidden=false;$('profileImportConfirm').disabled=false;
      $('profileImportConfirm').focus();
    }catch(e){$('profileImportError').textContent=e.message;}
  }
  async function save(next,errorId){
    if(busy)return;setBusy(true);$(errorId).textContent='';
    try{
      const result=await onCommit(validateProfile(next));
      fill(result.profile);dialog.close();reset();onSaved(result);
    }catch(e){$(errorId).textContent=e.message||'保存未完成，原档案保留。';}
    finally{setBusy(false);}
  }
  $('profileCopyPrompt').onclick=async()=>{
    const button=$('profileCopyPrompt');button.disabled=true;
    try{
      let board;try{board=clipboard();}catch{}
      const ok=await copyPrompt({clipboard:board,document,textarea:$('profilePromptText'),details:$('profilePromptDetails')});
      $('profileCopyStatus').textContent=ok?'已复制。去其他 AI 粘贴，生成档案后回到这里。':'请在已选中的提示词上按 ⌘C / Ctrl+C 复制。';
    }finally{button.disabled=busy;}
  };
  $('profileReply').addEventListener('input',resetPreview);
  $('profileImportParse').onclick=preview;
  $('profileImportCancel').onclick=resetPreview;
  $('profileImportConfirm').onclick=async()=>{
    if(busy||!draft)return;
    if(previewInput!==$('profileReply').value){resetPreview();$('profileImportError').textContent='内容已变化，请重新解析预览。';return;}
    await save(draft,'profileImportError');
  };
  $('profileForm').onsubmit=async e=>{
    e.preventDefault();
    try{
      // Preserve imported concept names containing punctuation if not edited.
      const concepts=(id,field)=>openedProfile&&$(id).value===openedProfile[field].join('、')?openedProfile[field]:parseConcepts($(id).value);
      await save({level:$('profileLevel').value,known_concepts:concepts('knownConcepts','known_concepts'),muted_concepts:concepts('mutedConcepts','muted_concepts')},'profileError');
    }catch(error){$('profileError').textContent=error.message;}
  };
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  dialog.addEventListener('close',reset);
  return {open(){if(busy)return;reset();openedProfile=validateProfile(getProfile());fill(openedProfile);dialog.showModal();dialog.scrollTop=0;}};
}
