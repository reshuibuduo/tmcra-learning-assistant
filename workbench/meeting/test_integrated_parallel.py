import asyncio,importlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from .test_parallel_subtitles import rows,answer
from .parallel_subtitles import ParallelSubtitleTranslator

class IntegratedParallelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'secrets').mkdir()
  for name in ('meeting_token','openai_api_key','tmcra_api_key','volc_api_key'):(self.root/'secrets'/name).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(self.root),'TMCRA_BASE_URL':'http://127.0.0.1:22109','TMCRA_MEMORY_BYPASS':'1'});self.env.start();sys.modules.pop('meeting.service',None);self.s=importlib.import_module('meeting.service')
  self.active=0;self.peak=0;self.calls=[];self.delay=.04
  async def response(instance,p,d,sc,n):
   self.active+=1;self.peak=max(self.peak,self.active);self.calls.append(d)
   try:await asyncio.sleep(self.delay);return answer(d)
   finally:self.active-=1
  self.mock=patch('meeting.pipeline.Memory.response',new=response);self.mock.start();self.client=TestClient(self.s.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
 def tearDown(self):
  self.client.__exit__(None,None,None);self.mock.stop();self.env.stop();self.tmp.cleanup()
 def create(self):
  v=self.client.post('/v1/sessions',headers=self.auth,json={'mode':'text_fixture','sources':['microphone'],'translation':{'enabled':True}}).json();sid=v['session']['id'];session=self.s.SESSIONS[sid];session.memory.utterances=rows(12);return sid,session
 def wait(self,sid):
  for _ in range(150):
   v=self.client.get('/v1/sessions/'+sid,headers=self.auth).json()
   if not v['translation']['pending']:return v
   time.sleep(.01)
  self.fail('integrated queue not drained')
 def test_each_new_session_uses_parallel_engine_automatically(self):
  sid,s=self.create();self.assertIsInstance(s.translator,ParallelSubtitleTranslator);v=self.wait(sid)
  self.assertEqual(v['translation']['parallelism'],4);self.assertEqual(self.peak,4);self.assertEqual(v['translation']['errors'],0)
  self.assertEqual([x['utterance_id'] for x in v['translation']['items']],[x['id'] for x in s.memory.utterances]);self.assertEqual(s.sync['jobs'],[])
 def test_second_session_needs_no_rebind_and_separate_records(self):
  a,one=self.create();b,two=self.create();self.assertNotEqual(a,b);self.assertIsNot(one.translator,two.translator);self.wait(a);self.wait(b)
  self.assertEqual(len(one.translator.records),12);self.assertEqual(len(two.translator.records),12);self.assertNotEqual(one.run,two.run)
 def test_pause_cancels_all_workers_without_stopping_session(self):
  self.delay=2;sid,s=self.create()
  for _ in range(100):
   if self.active==4:break
   time.sleep(.01)
  self.assertEqual(self.active,4);r=self.client.post('/v1/sessions/'+sid+'/translation',headers=self.auth,json={'enabled':False})
  self.assertEqual(r.status_code,200);self.assertEqual(self.active,0);self.assertFalse(s.translator.jobs);self.assertEqual(s.meta['status'],'recording');self.assertFalse((s.run/'CAPTURE_STOPPED').exists())
 def test_language_change_cancels_old_generation(self):
  self.delay=.15;sid,s=self.create();self.client.post('/v1/sessions/'+sid+'/translation',headers=self.auth,json={'enabled':True,'target_language':'en'});v=self.wait(sid)
  self.assertTrue(all(x['target_language']=='en' for x in v['translation']['items']));self.assertEqual(v['translation']['errors'],0)
 def test_health_declares_integration_and_bypass(self):
  h=self.client.get('/healthz').json();self.assertEqual(h['subtitle_parallelism'],4);self.assertEqual(h['subtitle_attachment'],'per_session_automatic');self.assertTrue(h['memory_bypass']);self.assertFalse(h['tmcra_write'])
 def test_fresh_state_contains_no_imported_user_history(self):
  self.assertEqual(self.client.get('/v1/sessions',headers=self.auth).json(),{'sessions':[]})
 def test_finished_session_keeps_translations_persisted(self):
  sid,s=self.create();v=self.wait(sid);r=self.client.post('/v1/sessions/'+sid+'/finish',headers=self.auth);self.assertEqual(r.status_code,200)
  for _ in range(100):
   if s.meta['status']=='completed':break
   time.sleep(.01)
  self.assertEqual(s.meta['status'],'completed');saved=json.loads((s.run/'translations.json').read_text());self.assertEqual(saved['parallelism'],4);self.assertEqual(saved['pending'],0)
