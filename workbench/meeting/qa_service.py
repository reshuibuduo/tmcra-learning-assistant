"""Read-only classroom viewer + explicit questions; never owns an audio session."""
import asyncio,hmac,os,re
from pathlib import Path
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from .ui_access import Vault
from .pipeline import Memory
from .settings import settings
from .deepseek import provider_key
from .question_models import SubtitleQuestion,QuestionLoginCode
from .selection_qa import QuestionAnswerer,public_history,StaleSelection,RequestConflict,QuestionBusy,InvalidAnswer,ProviderUnavailable
from .selection_stream import StreamingQuestionAnswerer,event_response

ROOT=Path(os.environ['MEETING_ROOT'])
TOKEN=(ROOT/'secrets/meeting_token').read_text().strip()
KEY=provider_key(ROOT,settings())
LOGIN=Vault();TOKENS=Vault();CLIENTS={}

async def auth(request:Request):
 value=request.headers.get('authorization','')
 if hmac.compare_digest(value,'Bearer '+TOKEN):return
 if value.startswith('Bearer ') and TOKENS.get(value[7:])=={'browser':True}:return
 raise HTTPException(401,'unauthorized')

async def upstream(path):
 # Only callers below construct these fixed local paths; no user URLs or writes.
 async with httpx.AsyncClient(trust_env=False,timeout=8) as client:
  response=await client.get('http://127.0.0.1:18081'+path,headers={'Authorization':'Bearer '+TOKEN})
 if response.status_code!=200:raise HTTPException(response.status_code,'课堂暂时未就绪，请稍后重试。')
 return response.json()

def valid_sid(sid):
 if not re.fullmatch('[0-9a-f]{32}',sid):raise HTTPException(404,'session not found')
 return sid

@asynccontextmanager
async def lifespan(app):
 yield
 await asyncio.gather(*(m.close() for m in CLIENTS.values()),return_exceptions=True)

app=FastAPI(title='TMCRA 字幕划线提问',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/assets',StaticFiles(directory=Path(__file__).parent/'static'),name='assets')

@app.get('/')
async def page():return HTMLResponse((Path(__file__).parent/'dashboard.html').read_text())

@app.get('/healthz')
async def health():
 data=await upstream('/healthz')
 return {**data,'qa_review_only':True,'subtitle_questions':True,'subtitle_question_streaming':True,'capture_enabled':False,
         'tmcra_write':False,'memory_recall_available':False,'explicit_learning_import':False}

@app.post('/v1/browser-login',dependencies=[Depends(auth)])
async def login(request:Request):
 if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+TOKEN):raise HTTPException(403,'master access required')
 return {'code':LOGIN.issue({'login':True},90),'expires_in':90}

@app.post('/v1/browser-login/exchange')
async def exchange(body:QuestionLoginCode):
 if LOGIN.consume(body.code)!={'login':True}:raise HTTPException(401,'expired or invalid login code')
 return {'access_token':TOKENS.issue({'browser':True},28800),'expires_in':28800}

@app.get('/v1/sessions',dependencies=[Depends(auth)])
async def listing():return await upstream('/v1/sessions')

@app.get('/v1/sessions/{sid}',dependencies=[Depends(auth)])
async def view(sid:str):
 sid=valid_sid(sid);data=await upstream('/v1/sessions/'+sid)
 data['questions']=public_history(ROOT/'state/meetings'/sid)
 return data

@app.post('/v1/sessions/{sid}/questions',dependencies=[Depends(auth)])
async def question(sid:str,body:SubtitleQuestion):
 sid=valid_sid(sid)
 if not body.question.strip() or not body.quote.strip():raise HTTPException(422,'请填写问题并划选字幕。')
 data=await upstream('/v1/sessions/'+sid)
 if sid not in CLIENTS:
  run=ROOT/'state/selection-qa'/sid;run.mkdir(parents=True,exist_ok=True)
  CLIENTS[sid]=Memory(run,KEY)
 engine=QuestionAnswerer(ROOT/'state/meetings'/sid,CLIENTS[sid].response)
 try:return await engine.ask(body.model_dump(),data['transcripts'],data.get('translation',{}),data['session'].get('learner_profile'))
 except (StaleSelection,RequestConflict) as e:raise HTTPException(409,str(e))
 except QuestionBusy as e:raise HTTPException(429,str(e))
 except asyncio.TimeoutError:raise HTTPException(504,'这次回答超时，请重试。')
 except ProviderUnavailable as e:raise HTTPException(e.status_code,str(e))
 except (InvalidAnswer,ValueError):raise HTTPException(502,'回答未通过原文引用检查，请重试。')
 except Exception:raise HTTPException(502,'提问服务暂时未返回有效结果，请重试。')

@app.post('/v1/sessions/{sid}/questions/stream',dependencies=[Depends(auth)])
async def question_stream(sid:str,body:SubtitleQuestion):
 sid=valid_sid(sid)
 if not body.question.strip() or not body.quote.strip():raise HTTPException(422,'请填写问题并划选字幕。')
 data=await upstream('/v1/sessions/'+sid)
 if sid not in CLIENTS:
  run=ROOT/'state/selection-qa'/sid;run.mkdir(parents=True,exist_ok=True)
  CLIENTS[sid]=Memory(run,KEY)
 engine=StreamingQuestionAnswerer(ROOT/'state/meetings'/sid,CLIENTS[sid].response,CLIENTS[sid].stream_response)
 return event_response(engine,body.model_dump(),data['transcripts'],data.get('translation',{}),data['session'].get('learner_profile'))
