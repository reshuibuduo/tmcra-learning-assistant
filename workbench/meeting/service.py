from __future__ import annotations
import asyncio,hashlib,hmac,json,os,re,time,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI,HTTPException,Request,WebSocket,WebSocketDisconnect,Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .pipeline import Memory,Channel,QUESTIONS,RECALL_SCHEMA,atomic,append,validate_facts
from .settings import settings
from .volc_asr import VolcChannel
from .coach import Coach,native_records
from .concepts import ConceptExplainer,profile as normalize_profile
from .subtitles import options as translation_options
from .parallel_subtitles import ParallelSubtitleTranslator
from .deepseek import provider_key
from .rolling import WindowGate,context_current
from typing import Literal,Optional
from .ui_access import Vault
from .learning import LearningLibrary,SCOPE as LEARNING_SCOPE
from .audio_upload import append_frame,OffsetConflict,capture_state
from .question_models import SubtitleQuestion
from .selection_qa import QuestionAnswerer,public_history,StaleSelection,RequestConflict,QuestionBusy,InvalidAnswer,ProviderUnavailable
from .selection_stream import StreamingQuestionAnswerer,event_response
ROOT=Path(os.environ['MEETING_ROOT']);STATE=ROOT/'state/meetings';STATE.mkdir(parents=True,exist_ok=True)
TOKEN=(ROOT/'secrets/meeting_token').read_text().strip()
LLM_KEY=provider_key(ROOT,settings())
VOLC=(ROOT/'secrets/volc_api_key').read_text().strip()
TMCRA_TOKEN=(ROOT/'secrets/tmcra_api_key').read_text().strip()
TMCRA=os.environ.get('TMCRA_BASE_URL','https://127.0.0.1:22109').rstrip('/')
from urllib.parse import urlsplit
if urlsplit(TMCRA).scheme not in {'http','https'} or urlsplit(TMCRA).hostname not in {'127.0.0.1','localhost','::1'}:
 raise RuntimeError('TMCRA_BASE_URL must identify the local native service')
TMCRA_VERIFY=os.environ.get('TMCRA_CA_FILE') or (str(ROOT/'secrets/server.crt') if TMCRA.startswith('https://') else True)
SESSIONS={};TASKS=set()
LOGIN_CODES=Vault();BROWSER_TOKENS=Vault();AUDIO_TICKETS=Vault()
def background(coro):
 t=asyncio.create_task(coro);TASKS.add(t);t.add_done_callback(TASKS.discard);return t
async def auth(request:Request):
 value=request.headers.get('authorization','')
 if hmac.compare_digest(value,'Bearer '+TOKEN):return
 if value.startswith('Bearer ') and BROWSER_TOKENS.get(value[7:])=={'browser':True}:return
 raise HTTPException(401,'unauthorized')
async def tc(method,path,**kwargs):
 if not settings().get('memory_recall_enabled',True):raise RuntimeError('TMCRA memory bypass is enabled')
 async with httpx.AsyncClient(verify=TMCRA_VERIFY,timeout=180,trust_env=False) as c:
  r=await c.request(method,TMCRA+path,headers={'Authorization':'Bearer '+TMCRA_TOKEN,**kwargs.pop('headers',{})},**kwargs)
  if r.status_code>=400:raise RuntimeError(f'TMCRA {r.status_code}: '+r.text[:500])
  return r.json()
LIBRARY=LearningLibrary(ROOT,tc)
def recall_scopes():
 if not settings().get('memory_recall_enabled',True):return []
 return list(dict.fromkeys(settings()['learner_memory_scopes']+([LEARNING_SCOPE] if LIBRARY.available() else [])))
class LearningPreview(BaseModel):
 filename:str=Field(min_length=1,max_length=200)
 content:str=Field(min_length=1,max_length=8*1024*1024)
class LearningCommit(BaseModel):
 digest:str=Field(min_length=64,max_length=64)
 selected_ids:list[str]=Field(min_length=1,max_length=50)
 learning_state:Literal['discussed','studied','reviewed']='discussed'
 topics:list[str]=Field(default_factory=list,max_length=20)
 confirmed:Literal[True]
class LearnerProfile(BaseModel):
 level:Literal['intro','standard','technical','unknown']='unknown'
 known_concepts:list[str]=Field(default_factory=list,max_length=40)
 muted_concepts:list[str]=Field(default_factory=list,max_length=40)
class ExplanationInput(BaseModel):
 texts:list[str]=Field(min_length=1,max_length=8)
 learner_profile:LearnerProfile=Field(default_factory=LearnerProfile)
class LoginCode(BaseModel):
 code:str=Field(min_length=20,max_length=100)
class AudioTicketInput(BaseModel):
 source:Literal['microphone','feishu-output']='microphone'
class EnglishVocabulary(BaseModel):
 level:Literal['unknown','basic','intermediate','advanced']='unknown'
 estimated_size:Optional[int]=Field(default=None,ge=0,le=50000,strict=True)
 known_words:list[str]=Field(default_factory=list,max_length=200)
 model_config={'extra':'forbid'}
class TranslationOptions(BaseModel):
 enabled:bool=Field(default=False,strict=True)
 target_language:Literal['zh-CN','en']='zh-CN'
 mode:Literal['full','assisted','vocabulary']='full'
 vocabulary:EnglishVocabulary=Field(default_factory=EnglishVocabulary)
 model_config={'extra':'forbid'}
class NewSession(BaseModel):
 title:str=Field(default='独立音频测试',max_length=120)
 sources:list[str]=Field(default=['microphone'],min_length=1,max_length=2)
 mode:str=Field(default='live_capture',pattern='^(live_capture|recording_replay|text_fixture)$')
 seconds:Optional[int]=Field(default=None,ge=10)
 continuation_of:Optional[str]=Field(default=None,pattern=r'^[0-9a-f]{32}$')
 meeting_date:str=Field(default_factory=lambda:datetime.now(timezone(timedelta(hours=8))).date().isoformat(),pattern=r'^\d{4}-\d{2}-\d{2}$')
 assistant_enabled:bool=False
 translation:TranslationOptions=Field(default_factory=TranslationOptions)
 learner_profile:LearnerProfile=Field(default_factory=LearnerProfile)
class Questions(BaseModel):
 questions:list[str]=Field(default=QUESTIONS,min_length=1,max_length=8)
class TextFixture(BaseModel):
 texts:list[str]=Field(min_length=1,max_length=30)
class Session:
 def __init__(self,meta):
  self.meta=meta;self.id=meta['id'];self.run=STATE/self.id;self.run.mkdir(exist_ok=True)
  self.memory=Memory(self.run,LLM_KEY);self.channels=[];self.worker=None;self.sync_task=None;self.connections=set();self.last_audio={};self.bytes={s:(self.run/(s+'.pcm')).stat().st_size if (self.run/(s+'.pcm')).exists() else 0 for s in meta['sources']};self.sync_lock=asyncio.Lock();self.finish_lock=asyncio.Lock()
  self.coach=Coach(self.memory.response,self.retrieve_learning)
  self.question_answerer=StreamingQuestionAnswerer(self.run,self.memory.response,self.memory.stream_response)
  self.explainer=ConceptExplainer(self.memory.response);self.concept_task=None;self.recall_task=None
  cached=json.loads((self.run/'translations.json').read_text()) if (self.run/'translations.json').exists() else None
  self.translator=ParallelSubtitleTranslator(self.memory.response,meta.get('translation'),cached,settings().get('translation_deadline_seconds',12),concurrency=4,window_seconds=settings().get('translation_window_seconds',0),batch_size=settings().get('translation_batch_size',6),context_size=settings().get('translation_context_rows',12));self.translation_task=None;self.translation_lock=asyncio.Lock()
  self.learning_task=None;self.learning_gate=WindowGate(settings().get('learning_window_seconds',4),settings().get('learning_max_wait_seconds',8),settings().get('learning_min_new_chars',24))
  self.sync=json.loads((self.run/'tmcra-sync.json').read_text()) if (self.run/'tmcra-sync.json').exists() else {'synced_ids':[],'jobs':[],'last_error':None}
  if (self.run/'transcripts.json').exists():self.memory.utterances=json.loads((self.run/'transcripts.json').read_text())
  if (self.run/'memory.json').exists():self.memory.facts=json.loads((self.run/'memory.json').read_text())
 def save(self):atomic(self.run/'session.json',self.meta)
 async def start(self):
  self.save();self.worker=background(self.memory.worker()) if settings().get('memory_recall_enabled',True) else None
  if self.meta['mode']!='text_fixture':
   for source in self.meta['sources']:
    (self.run/(source+'.pcm')).touch();append(self.run/'capture.jsonl',{'event':'mic_started' if source=='microphone' else 'tap_started','wall_time':time.time()})
    self.channels.append(background(VolcChannel(self.run,source,VOLC,self.memory,(self.meta['seconds']+30) if self.meta['seconds'] is not None else None).run_stream()))
   # Audio upload/storage must not depend on provider availability.
  self.sync_task=background(self.sync_loop());background(self.watchdog())
  if self.translator.config['enabled']:self.translation_task=background(self.translation_loop())
  if self.meta.get('assistant_enabled'):
   if settings().get('rolling_learning_enabled'):self.learning_task=background(self.learning_loop())
   else:self.concept_task=background(self.concept_loop());self.recall_task=background(self.recall_loop())
 async def set_translation(self,config,retry=False):
  async with self.translation_lock:
   # Cancel the old language request before publishing a new configuration.
   task=self.translation_task
   if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
   await self.translator.close()
   self.translator.configure(config,retry);self.meta['translation']=dict(self.translator.config);self.save()
   atomic(self.run/'translations.json',self.translator.snapshot(self.memory.utterances))
   if self.translator.config['enabled']:self.translation_task=background(self.translation_loop())
   return self.view()
 async def translation_loop(self):
  try:
   while self.translator.config['enabled']:
    changed=await self.translator.step(lambda:self.memory.utterances,flush=self.meta['status']!='recording')
    if changed:atomic(self.run/'translations.json',self.translator.snapshot(self.memory.utterances))
    if self.meta['status'] not in ('recording','finishing') and not self.translator.snapshot(self.memory.utterances)['pending']:break
    await asyncio.sleep(.15)
  finally:
   await self.translator.close()
   atomic(self.run/'translations.json',self.translator.snapshot(self.memory.utterances))
 async def learning_loop(self):
  while True:
   await asyncio.sleep(.2)
   done=self.meta['status'] not in ('recording','finishing')
   window=self.learning_gate.take(self.memory.utterances,flush=self.meta['status']!='recording')
   if not window:
    if done:break
    continue
   transcript=window['transcript'];revision=self.memory.revision;started=time.monotonic();cfg=settings()
   current=lambda:revision if context_current(transcript,self.memory.utterances,started) else -1
   concept_on=cfg.get('concept_enabled',False) and started-self.explainer.last_shown>=cfg['concept_cooldown_seconds']
   recall_on=bool(recall_scopes()) and started-self.coach.last_shown>=cfg['reminder_cooldown_seconds']
   meta={'time':time.time(),'revision':revision,'window_wait_seconds':window['window_wait_seconds'],
    'input_ids':[u['id'] for u in window['delta']],'context_rows':len(transcript),'delta_rows':len(window['delta'])}
   if not concept_on and not recall_on:
    meta['skip']='cooldown_or_disabled';append(self.run/'learning-windows.jsonl',meta);continue
   try:
    branches=[]
    if concept_on:branches.append(('concept',self.explainer.run(transcript,self.meta.get('learner_profile'),revision,current)))
    if recall_on:branches.append(('recall',self.coach.run(transcript,revision,current)))
    # Separate decisions share the same window and run concurrently. A failed
    # concept validation must not suppress a valid memory reminder (or vice versa).
    async def publish(kind,coro):
     try:result=await coro
     except asyncio.CancelledError:raise
     except Exception as exc:
      meta[kind+'_error']=type(exc).__name__
      if isinstance(exc,ValueError):meta[kind+'_validation_error']=str(exc)[:120]
      return
     result['updated_at']=time.time()
     target='concept' if kind=='concept' else 'coach'
     atomic(self.run/(target+'.json'),result)
     if result['action']=='show':append(self.run/(target+'-history.jsonl'),result)
     meta[kind+'_action']=result['action'];meta[kind+'_reason']=result.get('reason')
     meta[kind+'_processing_seconds']=round(time.monotonic()-started,3)
    await asyncio.gather(*(publish(kind,coro) for kind,coro in branches))
    meta['completed_wall']=time.time()
    times=[u.get('completed_wall') for u in window['delta'] if isinstance(u.get('completed_wall'),(float,int))]
    if times:
     meta['first_delta_to_result_seconds']=round(time.time()-min(times),3)
     meta['last_delta_to_result_seconds']=round(time.time()-max(times),3)
   except asyncio.CancelledError:raise
   except Exception as exc:
    meta['error']=type(exc).__name__;append(self.run/'coach-errors.jsonl',{'error':type(exc).__name__,'at':time.time()})
   finally:
    meta['processing_seconds']=round(time.monotonic()-started,3);append(self.run/'learning-windows.jsonl',meta)

 async def recall_loop(self):
  seen=None
  while self.meta['status'] in ('recording','finishing'):
   await asyncio.sleep(.75)
   revision=self.memory.revision
   if not recall_scopes() or not self.memory.utterances or revision==seen:continue
   seen=revision
   try:
    result=await self.coach.run(list(self.memory.utterances),revision,lambda:self.memory.revision)
    result['updated_at']=time.time();atomic(self.run/'coach.json',result)
    if result['action']=='show':append(self.run/'coach-history.jsonl',result)
   except Exception as e:append(self.run/'coach-errors.jsonl',{'error':type(e).__name__,'at':time.time()})
 async def concept_loop(self):
  seen=-1
  while self.meta['status'] in ('recording','finishing'):
   await asyncio.sleep(.4)
   revision=self.memory.revision
   if not self.memory.utterances or revision==seen:continue
   seen=revision
   try:await self.explain(self.meta.get('learner_profile'))
   except Exception as e:append(self.run/'concept-errors.jsonl',{'error':type(e).__name__,'at':time.time()})
 async def explain(self,learner=None,manual=False):
  revision=self.memory.revision
  explainer=ConceptExplainer(self.memory.response) if manual else self.explainer
  result=await explainer.run(list(self.memory.utterances),learner,revision,lambda:self.memory.revision)
  result['updated_at']=time.time();atomic(self.run/'concept.json',result)
  if result['action']=='show':append(self.run/'concept-history.jsonl',result)
  return result
 async def watchdog(self):
  if self.meta['seconds'] is None:return
  await asyncio.sleep(self.meta['seconds']+35)
  if self.meta['status']=='recording':await self.finish()
 async def sync_loop(self):
  while self.meta['status']=='recording':
   await asyncio.sleep(15)
   try:await self.sync_tmcra()
   except Exception as e:self.sync['last_error']=str(e)[:500];atomic(self.run/'tmcra-sync.json',self.sync)
 async def sync_tmcra(self):
  if not settings()['tmcra_write']:return
  async with self.sync_lock:
   pending=[u for u in self.memory.utterances if u['id'] not in self.sync['synced_ids']]
   if not pending:return
   # One meeting is one native TMCRA scope. IDs and timestamps remain source-linked.
   messages=[{'message_id':u['id'],'role':'user','content':u['text'],'timestamp':datetime.fromtimestamp(u['audio_start_wall'],timezone.utc).isoformat(),'metadata':{'capture_source':u['source'],'speaker_label':u['speaker'],'speaker_identification':u.get('speaker_identification','unknown'),'meeting_id':self.id,'origin_mode':self.meta['mode']}} for u in pending]
   idem=hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
   payload={'session_id':self.id,'messages':messages,'consistency':'read_your_writes','slow_policy':'deferred','metadata':{'source':'meeting-audio-bridge','title':self.meta['title'],'origin_mode':self.meta['mode']}}
   result=await tc('POST',f'/v1/scopes/meeting-{self.id}/ingest',json=payload,headers={'Idempotency-Key':idem})
   job_id=result['job_id'];append(self.run/'tmcra-jobs.jsonl',{'job_id':job_id,'idempotency_key':idem,'message_ids':[u['id'] for u in pending]})
   end=time.monotonic()+240
   while True:
    job=await tc('GET','/v1/jobs/'+job_id)
    if job['status'] in ('succeeded','completed'):break
    if job['status'] in ('failed','cancelled','quarantined'):raise RuntimeError('TMCRA job '+job_id+' '+job['status']+': '+json.dumps(job.get('error'))[:350])
    if time.monotonic()>end:raise TimeoutError('TMCRA ingest still pending: '+job_id)
    await asyncio.sleep(1)
   self.sync['synced_ids'] += [u['id'] for u in pending];self.sync['jobs'].append({'job_id':job_id,'status':job['status']});self.sync['last_error']=None;atomic(self.run/'tmcra-sync.json',self.sync)
 async def finish(self):
  async with self.finish_lock:
   if self.meta['status']=='completed':return
   self.meta['status']='finishing';self.save();(self.run/'CAPTURE_STOPPED').touch()
   results=await asyncio.gather(*self.channels,return_exceptions=True)
   errors=[type(x).__name__+': '+str(x)[:200] for x in results if isinstance(x,BaseException)]
   self.memory.stopping=True;self.memory.event.set()
   if self.worker:await self.worker
   try:await self.sync_tmcra()
   except Exception as e:errors.append(str(e)[:500])
   self.meta.update(status='completed' if not errors else 'error',errors=errors,finished_at=time.time(),audio_bytes=self.bytes);self.save()
 def view(self):
  concept=json.loads((self.run/'concept.json').read_text()) if (self.run/'concept.json').exists() else None
  history=[json.loads(line) for line in (self.run/'concept-history.jsonl').read_text().splitlines()[-100:]] if (self.run/'concept-history.jsonl').exists() else ([concept] if concept and concept.get('action')=='show' else [])
  coach=json.loads((self.run/'coach.json').read_text()) if (self.run/'coach.json').exists() else None
  return {'session':self.meta,'capture':capture_state(self),'asr':{source:json.loads((self.run/('asr-state-'+source+'.json')).read_text()) for source in self.meta['sources'] if (self.run/('asr-state-'+source+'.json')).exists()},'transcripts':self.memory.utterances,'memory':self.memory.facts,'tmcra':self.sync,'concept':concept,'concept_history':history,'coach':coach,'translation':self.translator.snapshot(self.memory.utterances,bool(self.translation_task and not self.translation_task.done())),'questions':public_history(self.run)}
 async def retrieve_learning(self,query):
  rows=[]
  for scope in recall_scopes():
   if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',scope):raise ValueError('invalid configured learner scope')
   result=await tc('POST',f'/v1/scopes/{scope}/recall',json={'query':query,'query_time':datetime.now(timezone.utc).isoformat(),'evidence_mode':'raw','recall_profile':'interactive','response_projection':'full','max_windows':8,'debug':False})
   rows.extend(LIBRARY.enrich(result) if scope==LEARNING_SCOPE else native_records(result,scope))
  return rows

def get_session(sid):
 if not re.fullmatch('[0-9a-f]{32}',sid):raise HTTPException(404,'session not found')
 if sid not in SESSIONS:
  p=STATE/sid/'session.json'
  if not p.exists():raise HTTPException(404,'session not found')
  meta=json.loads(p.read_text())
  if meta['status'] in ('recording','finishing'):meta.update(status='interrupted',errors=['server restarted; capture is not resumed automatically'])
  SESSIONS[sid]=Session(meta)
 return SESSIONS[sid]

@asynccontextmanager
async def lifespan(app):
 if settings().get('memory_recall_enabled',True):LIBRARY.resume()
 yield
 await LIBRARY.close()
 for s in list(SESSIONS.values()):
  if s.meta['status'] in ('recording','finishing'):
   s.meta.update(status='interrupted',interruption_reason='service_shutdown_no_automatic_microphone_resume');s.save()
 tasks=list(TASKS)
 for t in tasks:t.cancel()
 await asyncio.gather(*tasks,return_exceptions=True)
 await asyncio.gather(*(s.memory.close() for s in SESSIONS.values()),return_exceptions=True)
app=FastAPI(title='独立会议记忆 / TMCRA',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/assets',StaticFiles(directory=Path(__file__).parent/'static'),name='assets')
@app.get('/healthz')
async def health():return {'status':'ok','service':'tmcra-learning-assistant','release':'0.1.0','capture_location':'browser_or_Mac','diarization':'anonymous_session_clusters','asr_resource':settings()['asr_resource'],'reasoning_model':settings()['reasoning_model'],'capture_enabled':settings()['capture_enabled'],'tmcra_write':settings()['tmcra_write'],'concept_enabled':settings()['concept_enabled'],'memory_recall_available':bool(recall_scopes()),'explicit_learning_import':settings().get('memory_recall_enabled',True),'memory_bypass':not settings().get('memory_recall_enabled',True),'subtitle_translation':True,'subtitle_parallelism':4,'subtitle_attachment':'per_session_automatic','subtitle_modes':['full','assisted','vocabulary'],'ui_version':'workbench-1','llm_provider':settings().get('llm_provider','openai'),'llm_direct':settings().get('llm_provider')=='deepseek','translation_window_seconds':settings().get('translation_window_seconds',0),'learning_window_seconds':settings().get('learning_window_seconds',0),'rolling_learning':settings().get('rolling_learning_enabled',False),'subtitle_questions':True,'subtitle_question_streaming':True,'resumable_audio':True}
@app.get('/')
async def page():return HTMLResponse((Path(__file__).parent/'dashboard.html').read_text())
@app.post('/v1/browser-login',dependencies=[Depends(auth)])
async def browser_login(request:Request):
 if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+TOKEN):raise HTTPException(403,'master access required')
 return {'code':LOGIN_CODES.issue({'login':True},90),'expires_in':90}
@app.post('/v1/browser-login/exchange')
async def exchange_login(body:LoginCode):
 if LOGIN_CODES.consume(body.code)!={'login':True}:raise HTTPException(401,'expired or invalid login code')
 return {'access_token':BROWSER_TOKENS.issue({'browser':True},28800),'expires_in':28800}
@app.get('/v1/sessions',dependencies=[Depends(auth)])
async def listing():return {'sessions':sorted([json.loads(p.read_text()) for p in STATE.glob('*/session.json')],key=lambda m:m['created_at'],reverse=True)}
@app.get('/v1/learning/imports',dependencies=[Depends(auth)])
async def learning_listing():return {'imports':LIBRARY.listing(),'scope':LEARNING_SCOPE}
@app.post('/v1/learning/preview',dependencies=[Depends(auth)])
async def learning_preview(body:LearningPreview):
 try:return LIBRARY.preview(body.filename,body.content)
 except ValueError as e:raise HTTPException(422,str(e))
@app.post('/v1/learning/previews/{draft_id}/commit',dependencies=[Depends(auth)])
async def learning_commit(draft_id:str,body:LearningCommit):
 if not settings().get('memory_recall_enabled',True):raise HTTPException(409,'记忆支线已暂停；字幕与学习提示可独立使用')
 try:return await LIBRARY.commit(draft_id,body.digest,body.selected_ids,body.learning_state,body.topics,body.confirmed)
 except ValueError as e:raise HTTPException(422,str(e))
@app.get('/v1/learning/imports/{import_id}',dependencies=[Depends(auth)])
async def learning_source(import_id:str):
 try:return LIBRARY.get(import_id)
 except ValueError as e:raise HTTPException(404,str(e))
@app.post('/v1/learning/imports/{import_id}/recheck',dependencies=[Depends(auth)])
async def learning_recheck(import_id:str):
 if not settings().get('memory_recall_enabled',True):raise HTTPException(409,'记忆支线已暂停')
 try:
  row=LIBRARY.get(import_id)
  if row['status']!='ready':LIBRARY.start(import_id)
  return {'status':row['status']}
 except ValueError as e:raise HTTPException(404,str(e))
@app.post('/v1/sessions',dependencies=[Depends(auth)])
async def new(body:NewSession):
 try:normalize_profile(body.learner_profile.model_dump());translation_options(body.translation.model_dump())
 except ValueError as e:raise HTTPException(422,str(e))
 if body.mode=='live_capture' and not settings()['capture_enabled']:raise HTTPException(409,'live capture is disabled in this isolated candidate')
 if set(body.sources)-{'microphone','feishu-output'} or len(set(body.sources))!=len(body.sources):raise HTTPException(422,'invalid source')
 if sum(s.meta['status'] in ('recording','finishing') for s in SESSIONS.values())>=2:raise HTTPException(429,'two session limit')
 if body.mode=='live_capture':body.seconds=None
 if body.continuation_of and not (STATE/body.continuation_of/'session.json').exists():raise HTTPException(404,'prior session not found')
 sid=uuid.uuid4().hex;s=Session({**body.model_dump(),'id':sid,'created_at':time.time(),'status':'recording','errors':[]});SESSIONS[sid]=s
 try:await s.start()
 except Exception as e:
  for t in s.channels:t.cancel()
  if s.worker:s.worker.cancel()
  s.meta.update(status='error',errors=[type(e).__name__]);s.save();raise HTTPException(502,'ASR initialization failed: '+type(e).__name__)
 return s.view()
@app.get('/v1/sessions/{sid}',dependencies=[Depends(auth)])
async def view(sid:str):return get_session(sid).view()
@app.post('/v1/sessions/{sid}/coach',dependencies=[Depends(auth)])
async def coach(sid:str):
 if not settings().get('memory_recall_enabled',True):return {'action':'silent','reason':'memory_bypass','cards':[]}
 s=get_session(sid);revision=s.memory.revision
 result=await s.coach.run(list(s.memory.utterances),revision,lambda:s.memory.revision)
 atomic(s.run/'coach.json',result);return result
@app.post('/v1/sessions/{sid}/explain',dependencies=[Depends(auth)])
async def explain_session(sid:str,body:LearnerProfile):
 try:return await get_session(sid).explain(normalize_profile(body.model_dump()),manual=True)
 except ValueError as e:raise HTTPException(422,str(e))
@app.post('/v1/sessions/{sid}/profile',dependencies=[Depends(auth)])
async def set_profile(sid:str,body:LearnerProfile):
 try:learner=normalize_profile(body.model_dump())
 except ValueError as e:raise HTTPException(422,str(e))
 s=get_session(sid);s.meta['learner_profile']=learner;s.save();return {'learner_profile':learner}
@app.post('/v1/sessions/{sid}/questions',dependencies=[Depends(auth)])
async def subtitle_question(sid:str,body:SubtitleQuestion):
 if not body.question.strip() or not body.quote.strip():raise HTTPException(422,'请填写问题并划选字幕。')
 s=get_session(sid)
 try:return await s.question_answerer.ask(body.model_dump(),list(s.memory.utterances),s.translator.snapshot(s.memory.utterances),s.meta.get('learner_profile'))
 except (StaleSelection,RequestConflict) as e:raise HTTPException(409,str(e))
 except QuestionBusy as e:raise HTTPException(429,str(e))
 except asyncio.TimeoutError:raise HTTPException(504,'这次回答超时，请重试。')
 except ProviderUnavailable as e:raise HTTPException(e.status_code,str(e))
 except (InvalidAnswer,ValueError):raise HTTPException(502,'回答未通过原文引用检查，请重试。')
 except Exception:raise HTTPException(502,'提问服务暂时未返回有效结果，请重试。')
@app.post('/v1/sessions/{sid}/questions/stream',dependencies=[Depends(auth)])
async def subtitle_question_stream(sid:str,body:SubtitleQuestion):
 if not body.question.strip() or not body.quote.strip():raise HTTPException(422,'请填写问题并划选字幕。')
 s=get_session(sid)
 return event_response(s.question_answerer,body.model_dump(),list(s.memory.utterances),s.translator.snapshot(s.memory.utterances),s.meta.get('learner_profile'))
@app.post('/v1/sessions/{sid}/translation',dependencies=[Depends(auth)])
async def set_translation(sid:str,body:TranslationOptions):
 try:config=translation_options(body.model_dump())
 except ValueError as e:raise HTTPException(422,str(e))
 return await get_session(sid).set_translation(config)
@app.post('/v1/sessions/{sid}/translation/retry',dependencies=[Depends(auth)])
async def retry_translation(sid:str):
 s=get_session(sid)
 if not s.translator.config['enabled']:raise HTTPException(409,'subtitle translation is disabled')
 return await s.set_translation(s.translator.config,retry=True)
@app.post('/v1/sessions/{sid}/audio-ticket',dependencies=[Depends(auth)])
async def audio_ticket(sid:str,body:AudioTicketInput):
 s=get_session(sid)
 if s.meta['status']!='recording' or s.meta['mode']=='text_fixture' or body.source not in s.meta['sources']:raise HTTPException(409,'audio session is not open')
 return {'ticket':AUDIO_TICKETS.issue({'sid':sid,'source':body.source},60),'expires_in':60}
@app.post('/v1/explain',dependencies=[Depends(auth)])
async def explain_fixture(body:ExplanationInput):
 if any(not t.strip() or len(t)>4000 for t in body.texts):raise HTTPException(422,'invalid text fixture')
 run=ROOT/'state/concept-tests'/uuid.uuid4().hex;run.mkdir(parents=True)
 rows=[{'id':'fixture-'+str(i),'text':t,'definite':True} for i,t in enumerate(body.texts)]
 memory=Memory(run,LLM_KEY)
 try:
  learner=normalize_profile(body.learner_profile.model_dump())
  result=await ConceptExplainer(memory.response).run(rows,learner)
  atomic(run/'result.json',result);return result
 except ValueError as e:raise HTTPException(422,str(e))
 except Exception as e:raise HTTPException(502,type(e).__name__)
 finally:await memory.close()
@app.post('/v1/sessions/{sid}/resume',dependencies=[Depends(auth)])
async def resume(sid:str):
 s=get_session(sid)
 if s.meta['mode']!='live_capture':raise HTTPException(409,'只能继续麦克风课堂')
 if s.connections:raise HTTPException(409,'这场录音还有正在上传的连接，请先检查原录音页')
 if s.meta['status']=='finishing':raise HTTPException(409,'正在保存，请稍后再继续')
 # This route is called only by the explicit Continue button; no microphone API.
 if s.meta['status']=='recording' and any(not t.done() for t in s.channels):return s.view()
 for t in s.channels:
  if not t.done():t.cancel()
 await asyncio.gather(*s.channels,return_exceptions=True)
 s.channels=[];s.memory.stopping=False
 marker=s.run/'CAPTURE_STOPPED'
 if marker.exists():marker.unlink()
 s.meta.update(status='recording',seconds=None,errors=[],resumed_at=time.time())
 s.meta.pop('finished_at',None);s.save();await s.start()
 append(s.run/'capture.jsonl',{'event':'manual_resume','wall_time':time.time(),'preserved_bytes':s.bytes})
 return s.view()

@app.websocket('/v1/sessions/{sid}/audio/{source}')
async def audio(ws:WebSocket,sid:str,source:str):
 legacy=hmac.compare_digest(ws.headers.get('authorization',''),'Bearer '+TOKEN)
 protocols=[p.strip() for p in ws.headers.get('sec-websocket-protocol','').split(',')]
 v2='tmcra.audio.v2' in protocols
 protocol='tmcra.audio.v2' if v2 else None
 if not legacy:
  origin=urlsplit(ws.headers.get('origin',''))
  if origin.scheme not in {'http','https'} or origin.netloc!=ws.headers.get('host'):await ws.close(code=1008);return
  tickets=[p[7:] for p in protocols if p.startswith('ticket.')]
  if not any(p in protocols for p in ['tmcra.audio.v1','tmcra.audio.v2']) or len(tickets)!=1 or AUDIO_TICKETS.consume(tickets[0],{'sid':sid,'source':source}) is None:await ws.close(code=1008);return
  protocol='tmcra.audio.v2' if v2 else 'tmcra.audio.v1'
 try:s=get_session(sid)
 except HTTPException:await ws.close(code=1008);return
 if s.meta['mode']=='text_fixture' or s.meta['status']!='recording' or source not in s.meta['sources'] or source in s.connections:await ws.close(code=1008);return
 await ws.accept(subprotocol=protocol);s.connections.add(source);reason='peer_disconnected';path=s.run/(source+'.pcm');path.touch(exist_ok=True)
 append(s.run/'upload-events.jsonl',{'event':'connected','source':source,'protocol':protocol,'time':time.time(),'bytes':path.stat().st_size})
 try:
  if v2:await ws.send_json({'type':'ready','bytes':path.stat().st_size})
  with path.open('ab',buffering=0) as f:
   while s.meta['status']=='recording':
    message=await asyncio.wait_for(ws.receive(),30)
    if message['type']=='websocket.disconnect':reason='peer_disconnected_'+str(message.get('code'));break
    data=message.get('bytes')
    if data is None:
     if message.get('text')=='end':reason='end_transport';break
     await ws.close(code=1003);reason='invalid_message';break
    if v2:
     if s.meta['seconds'] is not None and int.from_bytes(data[:8],'big')+max(0,len(data)-8)>s.meta['seconds']*48000:await ws.close(code=1009);reason='replay_budget';break
     try:s.bytes[source]=append_frame(path,data)
     except OffsetConflict:
      await ws.send_json({'type':'conflict','bytes':path.stat().st_size});await ws.close(code=1008);reason='offset_conflict';break
     except ValueError:await ws.close(code=1009);reason='invalid_frame';break
     s.last_audio[source]=time.time()
     await ws.send_json({'type':'ack','bytes':s.bytes[source]})
    else:
     if not data or len(data)%2 or len(data)>48000 or (s.meta['seconds'] is not None and s.bytes[source]+len(data)>s.meta['seconds']*48000):await ws.close(code=1009);reason='invalid_frame';break
     f.write(data);s.bytes[source]+=len(data);s.last_audio[source]=time.time()
   if reason=='end_transport':await ws.send_json({'type':'received','bytes':s.bytes[source]})
 except asyncio.TimeoutError:reason='no_audio_30s'
 except (WebSocketDisconnect,RuntimeError):reason='transport_closed'
 finally:
  s.connections.discard(source)
  append(s.run/'upload-events.jsonl',{'event':'disconnected','source':source,'time':time.time(),'bytes':path.stat().st_size,'reason':reason})
  try:await ws.close()
  except RuntimeError:pass
@app.post('/v1/sessions/{sid}/finish',dependencies=[Depends(auth)])
async def finish(sid:str):
 s=get_session(sid)
 if s.meta['status'] in ('recording','interrupted','error'):background(s.finish())
 return {'id':sid,'status':s.meta['status']}
@app.post('/v1/sessions/{sid}/text-fixture',dependencies=[Depends(auth)])
async def fixture(sid:str,body:TextFixture):
 s=get_session(sid)
 if s.meta['mode']!='text_fixture' or s.meta['status']!='recording':raise HTTPException(409,'not a text fixture')
 for text in body.texts:
  if len(text)>4000:raise HTTPException(422,'text too long')
  s.memory.add({'id':'fixture:'+uuid.uuid4().hex,'source':'text_fixture','speaker':'fixture_author','text':text,'audio_start_wall':time.time(),'audio_end_wall':time.time(),'completed_wall':time.time(),'segment_source':'text_fixture_not_audio'})
 return {'accepted':len(body.texts)}
@app.post('/v1/sessions/{sid}/recall',dependencies=[Depends(auth)])
async def recall(sid:str,body:Questions):
 if not settings().get('memory_recall_enabled',True):raise HTTPException(409,'记忆支线已暂停')
 s=get_session(sid)
 if any(len(q)>2000 for q in body.questions):raise HTTPException(422,'question too long')
 try:
  await s.sync_tmcra();evidence=[]
  for q in body.questions:
   result=await tc('POST',f'/v1/scopes/meeting-{sid}/recall',json={'query':q,'query_time':datetime.now(timezone.utc).isoformat(),'evidence_mode':'raw','recall_profile':'interactive','response_projection':'full','max_windows':8,'debug':False})
   evidence.append({'question':q,'result':result})
  from .grounding import payload,validate,ANSWER_SCHEMA,PROMPT
  rows=payload(evidence)
  answer=await s.memory.response(PROMPT,{'retrieved':rows},ANSWER_SCHEMA,'tmcra_grounded_recall')
  validate(answer['answers'],rows)
  result={'backend':'tmcra-native-bge','citation_type':'tmcra_query_id_and_verbatim_quotes','session_id':sid,'answers':answer['answers'],'retrieval':evidence,'updated_at':time.time()};atomic(s.run/'tmcra-recall.json',result);return result
 except Exception as e:raise HTTPException(502,type(e).__name__+': '+str(e)[:500])
