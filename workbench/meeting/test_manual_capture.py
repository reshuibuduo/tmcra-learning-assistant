import asyncio,importlib,json,os,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from . import volc_asr

class ManualCaptureTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'secrets').mkdir()
  for name in ['meeting_token','openai_api_key','tmcra_api_key','volc_api_key']:(self.root/'secrets'/name).write_text('TEST')
  self.env=patch.dict(os.environ,MEETING_ROOT=str(self.root),TMCRA_BASE_URL='http://127.0.0.1:22109',TMCRA_MEMORY_BYPASS='1');self.env.start()
  sys.modules.pop('meeting.service',None);self.s=importlib.import_module('meeting.service');self.client=TestClient(self.s.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def session(self,seconds=None):
  sid='b'*32;s=self.s.Session({'id':sid,'title':'test','mode':'live_capture','status':'recording','sources':['microphone'],'seconds':seconds,'errors':[],'created_at':time.time()});s.save();self.s.SESSIONS[sid]=s;return sid,s
 def test_manual_default_is_null(self):self.assertIsNone(self.s.NewSession().seconds)
 def test_live_legacy_duration_is_normalized_to_manual(self):
  with patch.object(self.s.Session,'start',new=AsyncMock()):
   data=self.client.post('/v1/sessions',headers=self.auth,json={'mode':'live_capture','seconds':900}).json()
  self.assertIsNone(data['session']['seconds'])
 def test_explicit_replay_can_exceed_fifteen_minutes(self):self.assertEqual(self.s.NewSession(mode='recording_replay',seconds=7200).seconds,7200)
 def test_manual_watchdog_does_not_schedule_stop(self):
  _,s=self.session();s.finish=AsyncMock()
  async def check():
   with patch.object(self.s.asyncio,'sleep',new=AsyncMock()) as sleep:
    await s.watchdog();sleep.assert_not_awaited();s.finish.assert_not_awaited()
  asyncio.run(check())
 def test_audio_accepted_beyond_old_byte_budget(self):
  sid,s=self.session();s.bytes['microphone']=900*48000
  with self.client.websocket_connect('/v1/sessions/'+sid+'/audio/microphone',headers=self.auth) as ws:
   ws.send_bytes(b'\x01\x00'*9600);ws.send_text('end');self.assertEqual(ws.receive_json()['bytes'],900*48000+19200)
 def test_packet_size_validation_retained(self):
  sid,s=self.session()
  with self.assertRaises(WebSocketDisconnect):
   with self.client.websocket_connect('/v1/sessions/'+sid+'/audio/microphone',headers=self.auth) as ws:
    ws.send_bytes(b'\0'*48002);ws.receive_json()
 def test_finite_replay_budget_still_enforced(self):
  sid,s=self.session(10);s.meta['mode']='recording_replay';s.bytes['microphone']=10*48000
  with self.assertRaises(WebSocketDisconnect):
   with self.client.websocket_connect('/v1/sessions/'+sid+'/audio/microphone',headers=self.auth) as ws:
    ws.send_bytes(b'\0\0');ws.receive_json()
 def test_manual_finish_finalizes_and_marks_capture_stopped(self):
  sid,s=self.session();self.client.post('/v1/sessions/'+sid+'/finish',headers=self.auth)
  for _ in range(100):
   if s.meta['status']=='completed':break
   time.sleep(.005)
  self.assertEqual(s.meta['status'],'completed');self.assertTrue((s.run/'CAPTURE_STOPPED').exists())
 def test_continuation_links_existing_session(self):
  sid,_=self.session()
  with patch.object(self.s.Session,'start',new=AsyncMock()):
   r=self.client.post('/v1/sessions',headers=self.auth,json={'continuation_of':sid});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['session']['continuation_of'],sid)
 def test_invalid_continuation_rejected(self):
  for value,code in [('../outside',422),('c'*32,404)]:
   self.assertEqual(self.client.post('/v1/sessions',headers=self.auth,json={'continuation_of':value}).status_code,code)
 def test_asr_reads_after_virtual_two_hours_until_stop_marker(self):
  run=self.root/'stream';run.mkdir();pcm=run/'microphone.pcm';pcm.write_bytes(b'\x01\x00'*4800);clock=[0];seen=[]
  async def stream(chunks,cfg,key,out,add,connected,source):
   connected();seen.append(await chunks.__anext__());clock[0]=7200
   with pcm.open('ab') as f:f.write(b'\x02\x00'*4800)
   seen.append(await chunks.__anext__());(run/'CAPTURE_STOPPED').touch()
   with self.assertRaises(StopAsyncIteration):await chunks.__anext__()
  async def check():
   with patch.object(volc_asr,'stream_pcm',new=stream),patch.object(volc_asr,'time',types.SimpleNamespace(monotonic=lambda:clock[0])):
    await volc_asr.VolcChannel(run,'microphone','TEST',types.SimpleNamespace(add=lambda row:None),None).run_stream()
  asyncio.run(check());self.assertEqual(len(seen),2);self.assertTrue(all(seen))

if __name__=='__main__':unittest.main()
