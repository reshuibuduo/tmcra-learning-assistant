export const VOCABULARY_STORAGE_KEY='tmcraEnglishVocabulary.v1';
export const VOCABULARY_LEVELS={unknown:'未指定',basic:'基础',intermediate:'中等',advanced:'较高'};
export function normalizeVocabulary(value={}){
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!['level','estimated_size','known_words'].includes(k)))throw Error('英语词汇档案格式有误。');
  const level=value.level??'unknown',size=value.estimated_size??null,words=value.known_words??[];
  if(!Object.hasOwn(VOCABULARY_LEVELS,level))throw Error('请选择有效的词汇水平。');
  if(size!==null&&(!Number.isInteger(size)||size<0||size>50000))throw Error('估计词汇量应为 0～50,000 的整数，也可以留空。');
  if(!Array.isArray(words)||words.length>200||words.some(w=>typeof w!=='string'||!w.trim()||[...w.trim()].length>80||/[\r\n]/u.test(w)))throw Error('已知词最多 200 项，每项 1～80 字。');
  return {level,estimated_size:size,known_words:[...new Set(words.map(w=>w.trim().toLowerCase()))]};
}
export function readVocabulary(getStorage=()=>globalThis.localStorage){
  try{const value=getStorage()?.getItem(VOCABULARY_STORAGE_KEY);if(value)return normalizeVocabulary(JSON.parse(value));}catch{}
  return normalizeVocabulary();
}
export function vocabularyFromFields(level,size,words){
  if(size.trim()&&!/^\d+$/u.test(size.trim()))throw Error('词汇量请填写整数，或留空。');
  return normalizeVocabulary({level,estimated_size:size.trim()?Number(size.trim()):null,known_words:words.split(/[,，、\n]/u).map(w=>w.trim()).filter(Boolean)});
}
