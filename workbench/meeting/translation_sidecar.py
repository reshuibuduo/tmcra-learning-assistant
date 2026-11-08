"""Read-only capture attachment. Owns translations, never microphone/ASR lifecycle."""
import asyncio,hmac,json,os,re,time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .pipeline import Memory,atomic
from .subtitles import options
from .parallel_subtitles import ParallelSubtitleTranslator
from .settings import settings
from .deepseek import provider_key
from .ui_access import Vault

ROOT=Path(os.environ['MEETING_ROOT']);SID=os.environ['PARALLEL_SESSION_ID']
if not re.fullmatch('[0-9a-f]{32}',SID):raise RuntimeError('invalid source session')
SOURCE=ROOT/'state/meetings'/SID
OUT=ROOT/'state/parallel-translations'/SID;OUT.mkdir(parents=True,exist_ok=True,mode=0o700)
TOKEN=(ROOT/'secrets/meeting_token').read_text().strip();KEY=provider_key(ROOT,settings())
LOGIN=Vault();TOKENS=Vault();RUNNER=None

class Runner:
 def __init__(self):
  self.meta={};self.rows=[];self.mtime=None;self.last_error=None;self.updated_at=None;self.task=None
  self.read_source()
  config=json.loads((OUT/'options.json').read_text()) if (OUT/'options.json').exists() else self.meta.get('translation',{})
  cached=json.loads((OUT/'translations.json').read_text()) if (OUT/'translations.json').exists() else json.loads((SOURCE/'translations.json').read_text()) if (SOURCE/'translations.json').exists() else {}
  # Carry verified successes only. Failed source rows get one new parallel attempt.
  cached['items']=[x for x in cached.get('items',[]) if x.get('status')=='translated']
  self.memory=Memory(OUT,KEY)
  self.engine=ParallelSubtitleTranslator(self.memory.response,config,cached,concurrency=4)
  atomic(OUT/'options.json',self.engine.config)
 def read_source(self):
  self.meta=json.loads((SOURCE/'session.json').read_text());p=SOURCE/'transcripts.json'
  stamp=(p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
  if stamp!=self.mtime:self.rows=json.loads(p.read_text()) if p.exists() else [];self.mtime=stamp
  self.last_error=None;self.updated_at=time.time()
 def view(self):
  return {'session':{k:self.meta.get(k) for k in ('id','title','status','seconds','created_at','continuation_of')},
   'transcripts':self.rows,'translation':self.engine.snapshot(self.rows,bool(self.task and not self.task.done())),
   'source_updated_at':self.updated_at,'source_error':self.last_error,'capture_control':'original_workbench','capture_modified':False}
 async def loop(self):
  try:
   while True:
    try:self.read_source()
    except (OSError,ValueError):self.last_error='source_temporarily_unavailable';await asyncio.sleep(.5);continue
    try:
     changed=await self.engine.step(lambda:self.rows)
     if changed:atomic(OUT/'translations.json',self.engine.snapshot(self.rows,True))
    except asyncio.CancelledError:raise
    except Exception as e:
     self.last_error='translation_scheduler_'+type(e).__name__
     atomic(OUT/'scheduler-error.json',{'type':type(e).__name__,'at':time.time()})
    await asyncio.sleep(.15)
  finally:await self.engine.close()
 async def configure(self,config,retry=False):
  async with self.engine.lock:
   await self.engine.close();self.engine.configure(config,retry)
   atomic(OUT/'options.json',self.engine.config);atomic(OUT/'translations.json',self.engine.snapshot(self.rows,True))
  return self.view()

async def auth(request:Request):
 value=request.headers.get('authorization','')
 if hmac.compare_digest(value,'Bearer '+TOKEN):return
 if value.startswith('Bearer ') and TOKENS.get(value[7:])=={'sid':SID}:return
 raise HTTPException(401,'unauthorized')
@asynccontextmanager
async def lifespan(app):
 global RUNNER
 RUNNER=Runner();RUNNER.task=asyncio.create_task(RUNNER.loop())
 yield
 RUNNER.task.cancel();await asyncio.gather(RUNNER.task,return_exceptions=True)
app=FastAPI(title='四路并行字幕',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/assets',StaticFiles(directory=Path(__file__).parent/'static'),name='assets')
@app.middleware('http')
async def headers(request,call_next):
 response=await call_next(request);response.headers['Cache-Control']='no-store'
 response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
 return response
@app.get('/')
async def page():return HTMLResponse((Path(__file__).parent/'parallel.html').read_text())
@app.get('/healthz')
async def health():return {'status':'ok','service':'parallel-subtitle-sidecar','parallelism':4,'capture_modified':False,'tmcra_write':False}
@app.post('/v1/browser-login',dependencies=[Depends(auth)])
async def login(request:Request):
 if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+TOKEN):raise HTTPException(403,'master access required')
 return {'code':LOGIN.issue({'sid':SID},90),'expires_in':90}
class Code(BaseModel):code:str=Field(min_length=20,max_length=100)
@app.post('/v1/browser-login/exchange')
async def exchange(body:Code):
 if LOGIN.consume(body.code)!={'sid':SID}:raise HTTPException(401,'expired login')
 return {'access_token':TOKENS.issue({'sid':SID},28800),'expires_in':28800}
@app.get('/v1/parallel',dependencies=[Depends(auth)])
async def view():return RUNNER.view()
@app.post('/v1/parallel/options',dependencies=[Depends(auth)])
async def configure(body:dict):
 try:config=options(body)
 except (ValueError,TypeError):raise HTTPException(422,'invalid translation options')
 return await RUNNER.configure(config)
@app.post('/v1/parallel/retry',dependencies=[Depends(auth)])
async def retry():
 if not RUNNER.engine.config['enabled']:raise HTTPException(409,'translation is paused')
 return await RUNNER.configure(RUNNER.engine.config,retry=True)
