// No network or memory writes: parsing and preview stay in this browser.
export const PROFILE_STORAGE_KEY='tmcraLearnerProfile.v1';
export const MAX_REPLY_LENGTH=64000;
export const LEVEL_LABELS={unknown:'背景未指定 · 默认通俗解释',intro:'通俗 · 直觉和具体例子',standard:'标准 · 定义与应用',technical:'深入 · 公式和机制'};
export const PROFILE_PROMPT=`请根据你实际可见的历史对话、我提供的学习材料，以及我的回答、解释和解题过程，整理一份可迁移到学习助手的「学习背景档案」。

用途：
学习助手在听课或会议时，根据我的背景解释陌生概念，并联系我以前讨论、学习过的内容。请优先保证准确，不要为了让档案完整而猜测。

一、判断原则
1. 先简要说明你实际参考了哪些记录。不要假装能够访问未提供的聊天或完整账户历史。
2. 按领域和具体概念分别判断，允许知识分布不均匀。不要因为我讨论复杂项目，就默认我掌握相关基础。
3. 区分：曾经讨论或接触；自述学过但理解程度待验证；有证据表明能够正确解释；有证据表明能够独立应用；明确存在疑问、误解或前置知识缺口。
4. AI 给过解释、我说“明白了”、聊天很长，都不是掌握证据。我的正确复述、推导、解题或应用才是更强证据。
5. 提问本身也不代表不懂。若证据冲突、过旧或不足，标记“待验证”，不要强行下结论。
6. 使用具体概念名，避免把理解某个局部扩大成掌握整个学科。
7. 不输出密码、密钥、联系方式及无关个人信息。

二、输出可填写的档案
先输出一个合法 JSON，只包含以下三个字段：
{
  "level": "unknown",
  "known_concepts": [],
  "muted_concepts": []
}

字段规则：
- level 表示「偏好的解释方式」，不是智力或总体知识等级：
  intro：直觉、日常语言和具体例子；
  standard：定义、例子和应用；
  technical：公式、机制和推导，同时补齐必要的前置知识；
  unknown：没有足够证据判断偏好。
  优先依据我的明确偏好，不要仅凭讨论主题难度决定。
- known_concepts：只放有较强证据支持我已经理解的具体概念。系统可能不再主动解释这些概念，所以宁可少填，也不要把“接触过”填成“熟悉”。最多 40 项，每项不超过 80 个字符；没有充分证据就留空。
- muted_concepts：只放我明确要求暂时不要解释的概念，不要自行推断。没有明确要求时填 []；数量和长度限制同上。

三、在 JSON 后单独输出判断依据
用简短表格列出：概念｜当前判断｜来自我自己的证据｜记录日期或位置｜置信度。
证据必须来自真实可见记录，可以简短引用或准确概括；找不到日期就写“未知”，不要编造。
最后补充：
1. 我以前重点讨论过什么问题、采用过什么理解路径；
2. 哪些概念只是接触过，暂时不应加入 known_concepts；
3. 最值得补齐的 1～3 个前置知识，以及判断依据。
这些补充内容不要添加进上述 JSON。
证据不足时直接给保守版本，不必等待我回答额外问题。
最后提醒我确认、修正档案，再用于学习助手。`;

const fields=['level','known_concepts','muted_concepts'];
export function validateProfile(data){
  if(!data||typeof data!=='object'||Array.isArray(data))throw Error('档案必须是 JSON 对象，不是数组或文字。');
  if(Object.keys(data).length!==3||fields.some(k=>!Object.hasOwn(data,k)))throw Error('JSON 必须且只能包含 level、known_concepts、muted_concepts 三个字段；请将判断依据放在 JSON 外。');
  if(typeof data.level!=='string'||!Object.hasOwn(LEVEL_LABELS,data.level))throw Error('level 只能是 unknown、intro、standard 或 technical。');
  const out={level:data.level};
  for(const field of fields.slice(1)){
    const values=data[field],label=field==='known_concepts'?'熟悉概念':'暂不解释概念';
    if(!Array.isArray(values)||values.length>40)throw Error(label+'必须是数组，最多 40 项。');
    const seen=new Set();out[field]=[];
    for(const value of values){
      if(typeof value!=='string'||!value.trim()||[...value.trim()].length>80||/[\u0000-\u001f\u007f]/u.test(value))throw Error(label+'每项须为 1～80 字的单行概念名称。');
      const text=value.trim(),key=text.toLowerCase();
      if(!seen.has(key)){out[field].push(text);seen.add(key);}
    }
  }
  return out;
}

// Reject duplicate root keys instead of allowing JSON.parse's last value to win.
function uniqueKeys(raw){
  let depth=0;const seen=new Set();
  for(let i=0;i<raw.length;i++){
    const c=raw[i];
    if(c==='"'){
      const start=i;for(i++;i<raw.length;i++){if(raw[i]==='\\'){i++;continue;}if(raw[i]==='"')break;}
      let next=i+1;while(/\s/u.test(raw[next]||'')&&next<raw.length)next++;
      if(depth===1&&raw[next]===':'){
        const key=JSON.parse(raw.slice(start,i+1));if(seen.has(key))throw Error('JSON 含重复字段，请保留唯一版本后重试。');seen.add(key);
      }
    }else if(c==='{'||c==='[')depth++;else if(c==='}'||c===']')depth--;
  }
}
function objectBlocks(text){
  const blocks=[];let start=-1,depth=0,quoted=false,escaped=false;
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(start<0){if(c==='{'){start=i;depth=1;}continue;}
    if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;continue;}
    if(c==='"')quoted=true;
    else if(c==='{'){if(++depth>12)throw Error('JSON 嵌套过深，请仅粘贴档案的三个字段。');}
    else if(c==='}'&&--depth===0){blocks.push(text.slice(start,i+1));start=-1;if(blocks.length>16)throw Error('结果包含过多 JSON，请仅保留需要导入的档案。');}
  }
  if(start>=0&&/"(?:level|known_concepts|muted_concepts)"/u.test(text.slice(start)))throw Error('档案 JSON 不完整，请检查括号和引号，或重新复制完整结果。');
  return blocks;
}
export function parseProfileReply(text){
  if(typeof text!=='string'||!text.trim())throw Error('请先粘贴其他 AI 返回的结果。');
  if(text.length>MAX_REPLY_LENGTH)throw Error('结果过长，请仅粘贴档案 JSON 和必要说明（最多 64,000 字符）。');
  const trimmed=text.trim();let direct;
  try{direct={value:JSON.parse(trimmed)};}catch{}
  if(direct){uniqueKeys(trimmed);return validateProfile(direct.value);}
  const candidates=[];
  for(const raw of objectBlocks(trimmed)){
    let value;try{value=JSON.parse(raw);}catch{
      if(/"(?:level|known_concepts|muted_concepts)"/u.test(raw))throw Error('档案 JSON 格式有误，请使用双引号并去掉尾随逗号。');
      continue;
    }
    if(fields.some(k=>Object.hasOwn(value,k))){uniqueKeys(raw);candidates.push(value);}
  }
  if(candidates.length>1)throw Error('找到多份档案，请只保留你要采用的那一份 JSON 后重新预览。');
  if(!candidates.length)throw Error('未找到档案 JSON。请让 AI 按提示词返回三个字段，或手动填写下方档案。');
  return validateProfile(candidates[0]);
}

export function readDefaultProfile(getStorage=()=>globalThis.localStorage){
  try{const raw=getStorage()?.getItem(PROFILE_STORAGE_KEY);if(raw)return validateProfile(JSON.parse(raw));}catch{}
  return {level:'unknown',known_concepts:[],muted_concepts:[]};
}
export async function commitProfile(next,{storage,saveRemote}={}){
  const profile=validateProfile(next);
  // Remote failure leaves the local default and caller state unchanged.
  if(saveRemote)await saveRemote(profile);
  try{
    if(!storage)throw Error('storage unavailable');
    storage.setItem(PROFILE_STORAGE_KEY,JSON.stringify(profile));
    return {profile,localSaved:true};
  }catch{
    if(saveRemote)return {profile,localSaved:false};
    throw Error('浏览器未保存档案，请开启本站存储，或连接课堂后再保存。原档案未改变。');
  }
}
