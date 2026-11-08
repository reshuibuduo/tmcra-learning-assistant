"""Explicit, previewed learning-history imports into the isolated personal library.

No filesystem discovery, hooks, or model calls during parsing. Imported messages
are evidence, never instructions. A successful ingest job, not an HTTP receipt,
makes an import available for recall.
"""
from __future__ import annotations
import asyncio,hashlib,json,re,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from .pipeline import atomic

SCOPE='learner-background'
SUCCESS={'succeeded','completed'}
TERMINAL={'failed','cancelled','quarantined'}
LEARNING_STATES={'discussed','studied','reviewed'}
SECRET=re.compile(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|ark-[A-Za-z0-9-]{30,})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def content(value):
    if isinstance(value,str):return value.strip()
    if isinstance(value,list):return '\n'.join(filter(None,(content(v) for v in value))).strip()
    if isinstance(value,dict):
        if value.get('type') not in (None,'text','input_text','output_text'):return ''
        return content(value.get('text',value.get('parts','')))
    return ''

def stamp(value):
    try:
        if isinstance(value,(int,float)):return datetime.fromtimestamp(value,timezone.utc).isoformat()
        if isinstance(value,str) and value.strip():
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    except (ValueError,OverflowError,OSError):pass
    return None

def clean_user(text):
    for tag in ('in-app-browser-context','environment_context','turn_aborted','instructions'):
        text=re.sub(r'<'+tag+r'\b[^>]*>.*?</'+tag+r'>\s*','',text,flags=re.S|re.I)
    return text.strip()

def parse_file(filename,raw):
    if not isinstance(raw,str) or len(raw.encode())>8*1024*1024:raise ValueError('文件需为 UTF-8 文本，且小于 8 MB')
    if SECRET.search(raw):raise ValueError('文件含疑似 API 凭证或私钥，请先移除再导入')
    warnings=[];groups=[];excluded=0
    def group(title,provider,items,external_id=''):
        nonlocal excluded
        rows=[];seen=set()
        for i,item in enumerate(items):
            if not isinstance(item,dict):raise ValueError('消息条目需为 JSON 对象')
            role=item.get('role',item.get('sender',''))
            if not isinstance(role,str):raise ValueError('消息角色格式错误')
            role={'human':'user','bot':'assistant'}.get(role,role)
            if role not in ('user','assistant') or item.get('phase') not in (None,'','final','final_answer') or item.get('channel') in ('analysis','commentary'):
                excluded+=1;continue
            text=content(item.get('content') or item.get('text',''))
            if role=='user':text=clean_user(text)
            if not text:excluded+=1;continue
            if len(text)>200000:raise ValueError('单条消息超过 200000 字符，请拆分文件')
            when=stamp(item.get('timestamp',item.get('created_at',item.get('create_time'))))
            # Deduplicate only source IDs, not repeated questions at distinct positions.
            key=item.get('id');key=str(key) if isinstance(key,(str,int)) else None
            if key and key in seen:continue
            if key:seen.add(key)
            rows.append({'role':role,'content':text,'timestamp':when,'source_index':i,'source_id':str(key or i)})
        if rows:
            identity={'provider':provider,'external_id':str(external_id),'messages':rows}
            groups.append({'id':digest(identity)[:32],'title':str(title or Path(filename).stem)[:120],
                           'provider':provider,'source_file':Path(filename).name[:160],'messages':rows,
                           'learning_date':next((r['timestamp'] for r in rows if r['timestamp']),None)})
    def one(obj):
        if not isinstance(obj,dict):raise ValueError('不支持的聊天 JSON 结构')
        if isinstance(obj.get('mapping'),dict):
            mapping=obj['mapping'];node=obj.get('current_node')
            if not node:
                leaves=[k for k,v in mapping.items() if not v.get('children')]
                if len(leaves)!=1:raise ValueError('ChatGPT 多分支导出缺少 current_node，请先导出要保留的分支')
                node=leaves[0]
            chain=[];visited=set()
            while node:
                if not isinstance(node,str):raise ValueError('ChatGPT 分支标识格式错误')
                if node in visited or node not in mapping:raise ValueError('ChatGPT 对话分支损坏')
                visited.add(node);n=mapping[node];m=n.get('message')
                if m:
                    if m.get('channel') in ('analysis','commentary') or m.get('metadata',{}).get('is_visually_hidden_from_conversation'):pass
                    else:chain.append({'id':m.get('id',node),'role':m.get('author',{}).get('role'),'content':m.get('content'), 'timestamp':m.get('create_time')})
                node=n.get('parent')
            group(obj.get('title'), 'chatgpt',reversed(chain),obj.get('conversation_id',obj.get('id','')))
        elif isinstance(obj.get('chat_messages'),list):
            group(obj.get('name',obj.get('title')),'claude',obj['chat_messages'],obj.get('uuid',''))
        elif isinstance(obj.get('messages'),list):
            group(obj.get('title',obj.get('name')),'chat_export',obj['messages'],obj.get('id',''))
        else:raise ValueError('JSON 需包含 ChatGPT mapping、Claude chat_messages 或通用 messages')
    suffix=Path(filename).suffix.lower()
    if suffix=='.jsonl':
        records=[]
        for i,line in enumerate(raw.splitlines()):
            if not line.strip():continue
            try:
                record=json.loads(line)
                if not isinstance(record,dict):raise ValueError(f'JSONL 第 {i+1} 行需为对象')
                records.append(record)
            except json.JSONDecodeError:raise ValueError(f'JSONL 第 {i+1} 行格式错误，未进行部分导入')
        items=[];session_id='';provider='codex';title=Path(filename).stem
        for row in records:
            payload=row.get('payload',{})
            if row.get('type')=='session_meta':session_id=payload.get('id','')
            elif row.get('type')=='response_item' and payload.get('type')=='message':
                items.append({**payload,'timestamp':row.get('timestamp')})
            elif row.get('type') in ('user','assistant') and isinstance(row.get('message'),dict):
                provider='claude_code';items.append({**row['message'],'id':row.get('uuid'),'timestamp':row.get('timestamp')})
        group(title,provider,items,session_id)
    elif suffix=='.json':
        try:data=json.loads(raw)
        except json.JSONDecodeError:raise ValueError('JSON 格式错误')
        if isinstance(data,dict) and isinstance(data.get('conversations'),list):data=data['conversations']
        for obj in data if isinstance(data,list) else [data]:one(obj)
    elif suffix in ('.md','.txt','.srt','.vtt'):
        # Preserve unstructured notes/transcripts verbatim; do not invent speaker roles.
        group(Path(filename).stem,'transcript' if suffix in ('.srt','.vtt') else 'notes',
              [{'role':'user','content':raw,'id':'document'}])
        warnings.append('文本按原文资料保存；没有据此认定作者、说话人身份或掌握程度。')
    else:raise ValueError('请选择 .jsonl / .json / .md / .txt / .srt / .vtt；音频请先在工作台上传转写')
    if not groups:raise ValueError('未找到可导入的用户/助手正文')
    for g in groups:g['source_sha256']=hashlib.sha256(raw.encode()).hexdigest()
    if len(groups)>50 or sum(len(g['messages']) for g in groups)>3000 or sum(len(m['content']) for g in groups for m in g['messages'])>1500000:
        raise ValueError('一次最多 50 段对话、3000 条消息、150 万字符，请拆分后导入')
    if excluded:warnings.append(f'已排除 {excluded} 条系统、工具、分析/过程消息或空消息；保留用户正文与助手最终答复。')
    if any(g['learning_date'] is None for g in groups):warnings.append('部分材料没有日期，将保留“日期未知”，不把导入时间当作学习时间。')
    return groups,warnings

class LearningLibrary:
    def __init__(self,root,transport):
        self.root=Path(root)/'state/learning';self.root.mkdir(parents=True,exist_ok=True)
        self.transport=transport;self.tasks={};self.lock=None;self.job_slots=None
        self.summaries={r['id']:self.summary(r) for r in self.all()}
    def path(self,kind,key):
        if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('无效资料标识')
        return self.root/(kind+'-'+key+'.json')
    def all(self):return [json.loads(p.read_text()) for p in self.root.glob('import-*.json')]
    @staticmethod
    def summary(row):return {k:row.get(k) for k in ('id','title','provider','source_file','learning_date','learning_state','topics','status','created_at','message_count','last_error')}
    def save(self,row):
        atomic(self.path('import',row['id']),row);self.summaries[row['id']]=self.summary(row)
    def available(self):return any(r['status']=='ready' for r in self.summaries.values())
    def listing(self):
        return [dict(r) for r in sorted(self.summaries.values(),key=lambda r:r['created_at'],reverse=True)]
    def preview(self,filename,raw):
        groups,warnings=parse_file(filename,raw);key=uuid.uuid4().hex
        draft={'id':key,'digest':digest(groups),'conversations':groups,'warnings':warnings,'expires_at':time.time()+1800}
        atomic(self.path('draft',key),draft);return draft
    def get(self,key):
        p=self.path('import',key)
        if not p.exists():raise ValueError('资料不存在')
        return json.loads(p.read_text())
    def start(self,key):
        if key not in self.tasks or self.tasks[key].done():self.tasks[key]=asyncio.create_task(self.process(key))
    async def commit(self,key,preview_digest,selected,learning_state,topics,confirmed):
        if confirmed is not True:raise ValueError('需要明确确认后写入')
        if learning_state not in LEARNING_STATES:raise ValueError('不支持该学习状态；导入不代表掌握')
        if len(topics)>20 or any(not isinstance(t,str) or not t.strip() or len(t)>80 for t in topics):raise ValueError('主题最多 20 个，每个最多 80 字')
        p=self.path('draft',key)
        if not p.exists():raise ValueError('请先预览资料')
        draft=json.loads(p.read_text())
        if draft['expires_at']<time.time() or preview_digest!=draft['digest']:raise ValueError('预览已过期或内容不匹配，请重新预览')
        choices={g['id']:g for g in draft['conversations']}
        if not selected or len(set(selected))!=len(selected) or not set(selected)<=choices.keys():raise ValueError('请选择预览中的对话')
        ids=[]
        if self.lock is None:self.lock=asyncio.Lock()
        async with self.lock:
            for key in selected:
                out=self.path('import',key)
                if not out.exists():
                    row={**choices[key],'learning_state':learning_state,'topics':list(dict.fromkeys(topics)),
                         'created_at':time.time(),'message_count':len(choices[key]['messages']),
                         'status':'queued','jobs':{},'last_error':None,'confirmed':True}
                    self.save(row)
                if self.get(key)['status']!='ready':self.start(key)
                ids.append(key)
        return {'ids':ids,'imports':[r for r in self.listing() if r['id'] in ids]}
    async def process(self,key):
        if self.job_slots is None:self.job_slots=asyncio.Semaphore(1)
        async with self.job_slots:await self._process(key)
    async def _process(self,key):
        row=self.get(key);row['status']='indexing';row['last_error']=None;self.save(row)
        try:
            for offset in range(0,len(row['messages']),250):
                batch=[]
                for i,m in enumerate(row['messages'][offset:offset+250],offset):
                    batch.append({'message_id':f'learning-{key}-{i}','role':m['role'],'content':m['content'],
                        'timestamp':m['timestamp'] or '1970-01-01T00:00:00+00:00',
                        'metadata':{'learning_import_id':key,'learning_state':row['learning_state'],
                                    'source_provider':row['provider'],'source_index':m['source_index'],
                                    'timestamp_known':bool(m['timestamp']),'origin_kind':'unknown'}})
                slot=str(offset);job_id=row['jobs'].get(slot)
                if not job_id:
                    body={'session_id':'learning-'+key,'messages':batch,'consistency':'read_your_writes','slow_policy':'deferred',
                          'metadata':{'source':'explicit-learning-import','import_id':key,'title':row['title'],'topics':row['topics'],'learning_state':row['learning_state']}}
                    result=await self.transport('POST',f'/v1/scopes/{SCOPE}/ingest',json=body,headers={'Idempotency-Key':digest(body)})
                    job_id=result['job_id'];row['jobs'][slot]=job_id;self.save(row)
                deadline=time.monotonic()+1800
                while True:
                    job=await self.transport('GET','/v1/jobs/'+job_id)
                    if job['status'] in SUCCESS:break
                    if job['status'] in TERMINAL:raise RuntimeError('记忆索引任务失败；原文保留，可检查后重试')
                    if time.monotonic()>deadline:raise TimeoutError('索引仍未完成，请稍后重试检查')
                    await asyncio.sleep(2)
            row.update(status='ready',ready_at=time.time(),last_error=None)
        except asyncio.CancelledError:
            row['status']='queued';self.save(row);raise
        except Exception as error:
            row.update(status='error',last_error=type(error).__name__+'：写入尚未确认，请重试检查')
        self.save(row)
    async def close(self):
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
    def resume(self):
        for row in self.all():
            if row['status'] in ('queued','indexing'):self.start(row['id'])
    def enrich(self,result):
        """Attach trusted import lineage only after a native successful retrieval.

        No keyword/fixture recall fallback. Ambiguous or unmatched evidence is
        omitted rather than being described as the user's learning history.
        """
        records=[];ready=[r for r in self.listing() if r['status']=='ready'];documents={}
        for i,w in enumerate(result.get('evidence',{}).get('evidence_windows',[])):
            text=w.get('text','')
            if not isinstance(text,str) or not text.strip():continue
            matches=[]
            for summary in ready:
                if w.get('session_id') and w['session_id']!='learning-'+summary['id']:continue
                key=summary['id']
                if key not in documents:documents[key]=self.get(key)
                row=documents[key]
                messages=[m for m in row['messages'] if min(len(text.strip()),len(m['content']))>=12 and (text.strip() in m['content'] or m['content'] in text)]
                if messages:matches.append((row,messages))
            if len(matches)!=1:continue
            row,messages=matches[0]
            # Context is original turn adjacency, not a generated claim of mastery.
            source_index=row['messages'].index(messages[0]);context=row['messages'][max(0,source_index-2):source_index+3]
            records.append({'source_id':f'{SCOPE}:{row["id"]}:{i}','text':text,'scope':SCOPE,'learning_state':row['learning_state'],
                            'import_id':row['id'],'title':row['title'],'provider':row['provider'],'learning_date':row['learning_date'],
                            'topics':row['topics'],'discussion_context':context,
                            'source_url':'/v1/learning/imports/'+row['id'],'source_indices':[m['source_index'] for m in messages]})
        return records
