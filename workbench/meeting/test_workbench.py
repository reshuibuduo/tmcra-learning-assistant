import importlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from .ui_access import Vault
class VaultTests(unittest.TestCase):
 def test_single_use(self):
  v=Vault();t=v.issue({'sid':'a'},60);self.assertEqual(v.consume(t),{'sid':'a'});self.assertIsNone(v.consume(t))
 def test_expiry(self):
  now=[0];v=Vault(clock=lambda:now[0]);t=v.issue({'x':1},5);now[0]=6;self.assertIsNone(v.get(t))
 def test_bound_source(self):
  v=Vault();t=v.issue({'sid':'a'},60);self.assertIsNone(v.consume(t,{'sid':'b'}));self.assertEqual(v.consume(t,{'sid':'a'}),{'sid':'a'})
 def test_capacity(self):
  v=Vault(limit=1);v.issue({},60)
  with self.assertRaises(ValueError):v.issue({},60)
class WorkbenchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();r=Path(self.tmp.name);(r/'secrets').mkdir()
  for n in ['meeting_token','openai_api_key','tmcra_api_key','volc_api_key']:(r/'secrets'/n).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(r),'TMCRA_BASE_URL':'http://127.0.0.1:22109'});self.env.start()
  sys.modules.pop('meeting.service',None);self.s=importlib.import_module('meeting.service');self.client=TestClient(self.s.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def session(self):
  sid='a'*32;s=self.s.Session({'id':sid,'title':'test','mode':'recording_replay','status':'recording','sources':['microphone'],'seconds':30,'errors':[],'created_at':time.time()});s.save();self.s.SESSIONS[sid]=s;return sid,s
 def test_assets_and_brand(self):
  for path in ['/','/assets/workbench.css','/assets/workbench.js','/assets/pcm-worklet.js','/assets/lecture-sample.wav','/assets/tmcra-mark.png']:
   self.assertEqual(self.client.get(path).status_code,200,path)
  self.assertIn('学习工作台',self.client.get('/').text)
 def test_api_remains_authenticated(self):self.assertEqual(self.client.get('/v1/sessions').status_code,401)
 def test_one_time_browser_login(self):
  issued=self.client.post('/v1/browser-login',headers=self.auth).json();self.assertEqual(issued['expires_in'],90)
  response=self.client.post('/v1/browser-login/exchange',json={'code':issued['code']});token=response.json()['access_token']
  self.assertNotEqual(token,'TEST');self.assertEqual(self.client.get('/v1/sessions',headers={'Authorization':'Bearer '+token}).status_code,200)
  self.assertEqual(self.client.post('/v1/browser-login/exchange',json={'code':issued['code']}).status_code,401)
 def test_browser_token_cannot_mint_login_codes(self):
  token=self.s.BROWSER_TOKENS.issue({'browser':True},60)
  self.assertEqual(self.client.post('/v1/browser-login',headers={'Authorization':'Bearer '+token}).status_code,403)
 def test_bad_exchange_rejected(self):self.assertEqual(self.client.post('/v1/browser-login/exchange',json={'code':'x'*32}).status_code,401)
 def test_audio_ticket_roundtrip_and_replay_denial(self):
  sid,s=self.session();ticket=self.client.post('/v1/sessions/'+sid+'/audio-ticket',headers=self.auth,json={}).json()['ticket']
  protocols=['tmcra.audio.v1','ticket.'+ticket];url='/v1/sessions/'+sid+'/audio/microphone'
  with self.client.websocket_connect(url,headers={'origin':'http://testserver'},subprotocols=protocols) as ws:
   self.assertEqual(ws.accepted_subprotocol,'tmcra.audio.v1');ws.send_bytes(b'\0\0');ws.send_text('end');self.assertEqual(ws.receive_json()['bytes'],2)
  with self.assertRaises(WebSocketDisconnect):
   with self.client.websocket_connect(url,headers={'origin':'http://testserver'},subprotocols=protocols):pass
 def test_cross_origin_ticket_rejected(self):
  sid,s=self.session();ticket=self.client.post('/v1/sessions/'+sid+'/audio-ticket',headers=self.auth,json={}).json()['ticket']
  with self.assertRaises(WebSocketDisconnect):
   with self.client.websocket_connect('/v1/sessions/'+sid+'/audio/microphone',headers={'origin':'https://unrelated.invalid'},subprotocols=['tmcra.audio.v1','ticket.'+ticket]):pass
 def test_finished_session_gets_no_audio_ticket(self):
  sid,s=self.session();s.meta['status']='completed'
  self.assertEqual(self.client.post('/v1/sessions/'+sid+'/audio-ticket',headers=self.auth,json={}).status_code,409)
 def test_profile_only_updates_session(self):
  sid,s=self.session();r=self.client.post('/v1/sessions/'+sid+'/profile',headers=self.auth,json={'level':'technical','known_concepts':['导数'],'muted_concepts':['softmax']})
  self.assertEqual(r.status_code,200);self.assertEqual(s.meta['learner_profile']['known_concepts'],['导数']);self.assertEqual(s.sync['jobs'],[])
 def test_oversized_concept_rejected(self):
  sid,s=self.session();self.assertEqual(self.client.post('/v1/sessions/'+sid+'/profile',headers=self.auth,json={'known_concepts':['字'*81]}).status_code,422)
 def test_health_explicit_capture_and_no_memory_write(self):
  h=self.client.get('/healthz').json();self.assertTrue(h['capture_enabled']);self.assertFalse(h['tmcra_write']);self.assertFalse(h['memory_recall_available']);self.assertEqual(h['ui_version'],'workbench-1')
if __name__=='__main__':unittest.main()
