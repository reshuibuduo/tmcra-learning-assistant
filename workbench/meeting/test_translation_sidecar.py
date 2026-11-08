import asyncio,hashlib,importlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from .test_parallel_subtitles import rows,answer

class SidecarTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.sid='a'*32;self.source=self.root/'state/meetings'/self.sid;self.source.mkdir(parents=True);(self.root/'secrets').mkdir()
  for name in ('meeting_token','openai_api_key'):(self.root/'secrets'/name).write_text('TEST_ONLY')
  self.data=rows(8);(self.source/'session.json').write_text(json.dumps({'id':self.sid,'title':'fixture','status':'recording','seconds':None,'translation':{'enabled':True}}));(self.source/'transcripts.json').write_text(json.dumps(self.data))
  self.before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()};self.calls=[]
  async def response(instance,p,d,s,n):self.calls.append(n);await asyncio.sleep(.01);return answer(d)
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(self.root),'PARALLEL_SESSION_ID':self.sid});self.env.start()
  self.mock=patch('meeting.pipeline.Memory.response',new=response);self.mock.start();sys.modules.pop('meeting.translation_sidecar',None);self.s=importlib.import_module('meeting.translation_sidecar');self.auth={'Authorization':'Bearer TEST_ONLY'}
  self.client=TestClient(self.s.app);self.client.__enter__()
 def tearDown(self):
  self.client.__exit__(None,None,None);self.mock.stop();self.env.stop();sys.modules.pop('meeting.translation_sidecar',None);self.tmp.cleanup()
 def ready(self):
  for _ in range(100):
   view=self.client.get('/v1/parallel',headers=self.auth).json()
   if not view['translation']['pending']:return view
   time.sleep(.015)
  self.fail('sidecar did not drain')
 def test_parallel_read_only_source(self):
  v=self.ready();self.assertEqual(v['translation']['parallelism'],4);self.assertFalse(v['capture_modified']);self.assertEqual(v['session']['status'],'recording')
  self.assertEqual(self.before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()});self.assertTrue((self.s.OUT/'translations.json').exists());self.assertEqual(set(self.calls),{'subtitle_translation'})
 def test_auth_required(self):
  for method,path in [('get','/v1/parallel'),('post','/v1/parallel/options'),('post','/v1/parallel/retry'),('post','/v1/browser-login')]:self.assertEqual(getattr(self.client,method)(path).status_code,401)
 def test_one_use_browser_login_and_master_scope(self):
  code=self.client.post('/v1/browser-login',headers=self.auth).json()['code'];r=self.client.post('/v1/browser-login/exchange',json={'code':code});self.assertEqual(r.status_code,200)
  auth={'Authorization':'Bearer '+r.json()['access_token']};self.assertEqual(self.client.get('/v1/parallel',headers=auth).status_code,200);self.assertEqual(self.client.post('/v1/browser-login',headers=auth).status_code,403);self.assertEqual(self.client.post('/v1/browser-login/exchange',json={'code':code}).status_code,401)
 def test_no_capture_routes(self):
  for path in ('/v1/sessions','/v1/audio','/v1/sessions/'+self.sid+'/stop'):
   self.assertEqual(self.client.post(path,headers=self.auth).status_code,404)
 def test_pause_and_resume_only_translation(self):
  r=self.client.post('/v1/parallel/options',headers=self.auth,json={'enabled':False});self.assertFalse(r.json()['translation']['options']['enabled']);self.assertEqual(self.client.post('/v1/parallel/retry',headers=self.auth).status_code,409)
  count=len(self.calls);time.sleep(.2);self.assertEqual(len(self.calls),count);self.assertEqual(json.loads((self.source/'session.json').read_text())['status'],'recording')
  self.client.post('/v1/parallel/options',headers=self.auth,json={'enabled':True,'target_language':'en'});v=self.ready();self.assertTrue(all(x['target_language']=='en' for x in v['translation']['items']))
 def test_strict_options(self):
  for body in ({'enabled':'true'},{'target_language':'xx'},{'mode':'voice'},{'capture':False}):self.assertEqual(self.client.post('/v1/parallel/options',headers=self.auth,json=body).status_code,422)
 def test_detects_new_source_without_modifying_it(self):
  self.ready();self.data.append({**rows(1)[0],'id':'new','text':'New source data.'});p=self.source/'transcripts.json';p.write_text(json.dumps(self.data));h=hashlib.sha256(p.read_bytes()).hexdigest()
  for _ in range(100):
   v=self.client.get('/v1/parallel',headers=self.auth).json()
   if len(v['transcripts'])==9 and not v['translation']['pending']:break
   time.sleep(.015)
  self.assertEqual(len(v['transcripts']),9);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),h)
 def test_page_has_no_external_assets_and_cache(self):
  r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn("frame-ancestors 'none'",r.headers['content-security-policy']);self.assertEqual(r.headers['cache-control'],'no-store');self.assertIn('原录音工作台',r.text);self.assertNotIn('TEST_ONLY',r.text)
 def test_own_options_survive_original_serial_switch_off(self):
  self.ready();p=self.source/'session.json';m=json.loads(p.read_text());m['translation']['enabled']=False;p.write_text(json.dumps(m))
  reopened=self.s.Runner();self.assertTrue(reopened.engine.config['enabled']);self.assertTrue(json.loads((self.s.OUT/'options.json').read_text())['enabled'])
