import importlib,json,os,sys,tempfile,time,unittest,httpx
from pathlib import Path
from unittest.mock import patch,AsyncMock
from fastapi.testclient import TestClient
from .test_selection_qa import ROWS,REQUEST,ANSWER

class QuestionAPITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();r=Path(self.tmp.name);(r/'secrets').mkdir()
  for n in ['meeting_token','openai_api_key','tmcra_api_key','volc_api_key']:(r/'secrets'/n).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(r),'TMCRA_BASE_URL':'http://127.0.0.1:22109'});self.env.start()
  sys.modules.pop('meeting.service',None);self.s=importlib.import_module('meeting.service');self.client=TestClient(self.s.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
  self.sid='b'*32;self.session=self.s.Session({'id':self.sid,'title':'fixture','status':'completed','mode':'text_fixture','sources':['microphone'],'seconds':None,'errors':[],'created_at':time.time(),'learner_profile':{'level':'intro'}})
  self.session.memory.utterances=ROWS;self.session.save();self.s.SESSIONS[self.sid]=self.session
  self.session.question_answerer.respond=AsyncMock(return_value=ANSWER)
  self.url='/v1/sessions/'+self.sid+'/questions'
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def test_auth_required(self):self.assertEqual(self.client.post(self.url,json=REQUEST).status_code,401)
 def test_real_route_returns_answer_and_history(self):
  response=self.client.post(self.url,json=REQUEST,headers=self.auth);self.assertEqual(response.status_code,200,response.text)
  data=self.client.get('/v1/sessions/'+self.sid,headers=self.auth).json();self.assertEqual(len(data['questions']),1);self.assertEqual(data['session']['status'],'completed');self.assertIsNone(data['session']['seconds'])
 def test_blank_question_rejected_without_call(self):
  self.assertEqual(self.client.post(self.url,json=dict(REQUEST,question='  '),headers=self.auth).status_code,422);self.session.question_answerer.respond.assert_not_awaited()
 def test_stale_quote_rejected(self):self.assertEqual(self.client.post(self.url,json=dict(REQUEST,quote='not present'),headers=self.auth).status_code,409)
 def test_request_fields_are_bounded(self):self.assertEqual(self.client.post(self.url,json=dict(REQUEST,question='字'*601),headers=self.auth).status_code,422)
 def test_client_cannot_supply_own_context_or_profile(self):self.assertEqual(self.client.post(self.url,json=dict(REQUEST,context=['fake']),headers=self.auth).status_code,422)
 def test_provider_402_is_actionable(self):
  r=httpx.Response(402,request=httpx.Request('POST','https://api.deepseek.com/chat/completions'))
  self.session.question_answerer.respond.side_effect=httpx.HTTPStatusError('provider body is not exposed',request=r.request,response=r)
  response=self.client.post(self.url,json=REQUEST,headers=self.auth)
  self.assertEqual(response.status_code,402);self.assertIn('余额不足',response.json()['detail'])

class CompanionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();r=Path(self.tmp.name);(r/'secrets').mkdir()
  for n in ['meeting_token','openai_api_key']:(r/'secrets'/n).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(r)});self.env.start();sys.modules.pop('meeting.qa_service',None);self.s=importlib.import_module('meeting.qa_service')
  self.client=TestClient(self.s.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def test_no_capture_or_finish_route(self):
  for url in ['/v1/sessions','/v1/sessions/'+'b'*32+'/finish','/v1/sessions/'+'b'*32+'/audio-ticket']:
   self.assertIn(self.client.post(url,headers=self.auth,json={}).status_code,[404,405])
 def test_readonly_health_is_explicit(self):
  with patch.object(self.s,'upstream',new=AsyncMock(return_value={'status':'ok','capture_enabled':True})):
   data=self.client.get('/healthz').json();self.assertTrue(data['qa_review_only']);self.assertFalse(data['capture_enabled'])
 def test_auth_is_not_bypassed(self):self.assertEqual(self.client.get('/v1/sessions').status_code,401)
 def test_companion_browser_login(self):
  code=self.client.post('/v1/browser-login',headers=self.auth).json()['code'];r=self.client.post('/v1/browser-login/exchange',json={'code':code});self.assertEqual(r.status_code,200)
  self.assertEqual(self.client.post('/v1/browser-login/exchange',json={'code':code}).status_code,401)

if __name__=='__main__':unittest.main()
