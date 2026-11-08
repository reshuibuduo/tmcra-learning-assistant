import asyncio,gzip,json,struct,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from starlette.websockets import WebSocketDisconnect
from .audio_upload import append_frame,OffsetConflict
from . import test_manual_capture as capture_tests
from . import volc_asr
from .resilient_asr import run_channel

def frame(offset,data):return struct.pack('>Q',offset)+data

class DurableAppendTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'audio.pcm';self.path.touch()
 def tearDown(self):self.tmp.cleanup()
 def test_duplicate_after_ack_loss_does_not_append_twice(self):
  self.assertEqual(append_frame(self.path,frame(0,b'abcd')),4);self.assertEqual(append_frame(self.path,frame(0,b'abcd')),4);self.assertEqual(self.path.read_bytes(),b'abcd')
 def test_partial_prefix_is_verified_and_only_missing_suffix_appended(self):
  self.path.write_bytes(b'ab');self.assertEqual(append_frame(self.path,frame(0,b'abcdef')),6);self.assertEqual(self.path.read_bytes(),b'abcdef')
 def test_conflict_and_gap_preserve_original(self):
  self.path.write_bytes(b'abcd')
  for offset,data in [(0,b'efgh'),(6,b'ij')]:
   with self.assertRaises(OffsetConflict):append_frame(self.path,frame(offset,data))
  self.assertEqual(self.path.read_bytes(),b'abcd')
 def test_invalid_offset_length_and_oversize_rejected(self):
  for data in [b'',frame(1,b'ab'),frame(0,b'a'),frame(0,b'x'*48002)]:
   with self.assertRaises(ValueError):append_frame(self.path,data)
 def test_durable_flush_precedes_ack_return(self):
  with patch('meeting.audio_upload.os.fsync') as sync:self.assertEqual(append_frame(self.path,frame(0,b'ab')),2);sync.assert_called_once()

class UploadAPITests(unittest.TestCase):
 setUp=capture_tests.ManualCaptureTests.setUp
 tearDown=capture_tests.ManualCaptureTests.tearDown
 session=capture_tests.ManualCaptureTests.session
 def ws(self,sid):return self.client.websocket_connect('/v1/sessions/'+sid+'/audio/microphone',headers=self.auth,subprotocols=['tmcra.audio.v2'])
 def test_disconnect_reconnect_keeps_bytes_and_manual_lifecycle(self):
  sid,s=self.session()
  with self.ws(sid) as ws:
   self.assertEqual(ws.receive_json(),{'type':'ready','bytes':0});ws.send_bytes(frame(0,b'abcd'));self.assertEqual(ws.receive_json(),{'type':'ack','bytes':4})
  for _ in range(100):
   if not s.connections:break
   time.sleep(.005)
  self.assertEqual(s.meta['status'],'recording');self.assertFalse((s.run/'CAPTURE_STOPPED').exists())
  self.assertEqual(s.view()['capture']['state'],'disconnected')
  with self.ws(sid) as ws:
   self.assertEqual(ws.receive_json()['bytes'],4);ws.send_bytes(frame(0,b'abcd'));self.assertEqual(ws.receive_json()['bytes'],4)
   ws.send_bytes(frame(4,b'ef'));self.assertEqual(ws.receive_json()['bytes'],6)
  self.assertEqual((s.run/'microphone.pcm').read_bytes(),b'abcdef')
 def test_conflict_does_not_overwrite_existing_audio(self):
  sid,s=self.session();(s.run/'microphone.pcm').write_bytes(b'abcd')
  with self.ws(sid) as ws:
   ws.receive_json();ws.send_bytes(frame(0,b'xxxx'));self.assertEqual(ws.receive_json()['type'],'conflict')
  self.assertEqual((s.run/'microphone.pcm').read_bytes(),b'abcd')
 def test_resume_is_authenticated_and_preserves_transcripts_and_audio(self):
  sid,s=self.session();(s.run/'microphone.pcm').write_bytes(b'abcd');s.memory.utterances=[{'id':'saved','text':'原字幕','source':'microphone','end_ms':0}];s.meta['status']='interrupted';s.save()
  self.assertEqual(self.client.post('/v1/sessions/'+sid+'/resume').status_code,401)
  with patch.object(s,'start',new=AsyncMock()):
   d=self.client.post('/v1/sessions/'+sid+'/resume',headers=self.auth).json()
  self.assertEqual(d['session']['status'],'recording');self.assertEqual(d['transcripts'][0]['text'],'原字幕');self.assertEqual((s.run/'microphone.pcm').read_bytes(),b'abcd')
 def test_reload_counts_existing_audio_instead_of_zero(self):
  sid,s=self.session();(s.run/'microphone.pcm').write_bytes(b'abcd');s.save();del self.s.SESSIONS[sid]
  loaded=self.s.get_session(sid);self.assertEqual(loaded.bytes['microphone'],4);self.assertEqual(loaded.meta['status'],'interrupted');self.assertEqual(loaded.channels,[])
 def test_stale_connected_socket_is_not_reported_as_receiving(self):
  _,s=self.session();(s.run/'microphone.pcm').write_bytes(b'ab');s.connections.add('microphone');s.last_audio['microphone']=time.time()-20
  self.assertEqual(s.view()['capture']['state'],'stalled')
 def test_asr_failure_does_not_stop_or_block_capture_start(self):
  sid,s=self.session()
  async def run():
   with patch.object(self.s.VolcChannel,'run_stream',new=AsyncMock(side_effect=RuntimeError('VOLC_ASR_ERROR_45000081'))):
    await s.start();await asyncio.sleep(.01)
   self.assertEqual(s.meta['status'],'recording');self.assertFalse((s.run/'CAPTURE_STOPPED').exists())
   await asyncio.gather(*s.channels,return_exceptions=True)
  asyncio.run(run())

class ASRRecoveryTests(unittest.IsolatedAsyncioTestCase):
 async def test_receiver_failure_interrupts_an_idle_audio_iterator(self):
  payload=gzip.compress(b'{}');ack=bytes([0x11,0x90,0x11,0])+struct.pack('>I',len(payload))+payload
  err=bytes([0x11,0xf0,0x11,0])+struct.pack('>II',45000081,len(payload))+payload
  closed=[]
  class WS:
   async def __aenter__(self):return self
   async def __aexit__(self,*args):closed.append('socket')
   async def send(self,data):pass
   async def recv(self):return ack
   async def __aiter__(self):await asyncio.sleep(.01);yield err
  async def chunks():
   try:await asyncio.sleep(60);yield b'ab'
   finally:closed.append('chunks')
  with tempfile.TemporaryDirectory() as d,patch.object(volc_asr.websockets,'connect',return_value=WS()):
   with self.assertRaisesRegex(RuntimeError,'45000081'):
    await asyncio.wait_for(volc_asr.stream_pcm(chunks(),{'asr_endpoint':next(iter(volc_asr.ENDPOINTS)),'asr_resource':'TEST'},'TEST',Path(d)),.3)
  self.assertIn('chunks',closed);self.assertIn('socket',closed)
 async def test_retry_replays_unconfirmed_tail_and_retains_existing_rows(self):
  with tempfile.TemporaryDirectory() as d:
   run=Path(d);pcm=run/'microphone.pcm';pcm.write_bytes(b'\x01\0'*24000);(run/'CAPTURE_STOPPED').touch()
   original={'id':'original','text':'原字幕','source':'microphone','end_ms':100};rows=[original.copy()];memory=types.SimpleNamespace(utterances=rows,add=lambda x:rows.append(x));attempts=[]
   async def fake(chunks,cfg,key,out,add,connected,source):
    connected();got=[]
    if not attempts:
     got.append(await chunks.__anext__());attempts.append(got);await chunks.aclose();raise RuntimeError('VOLC_ASR_ERROR_45000081')
    async for data in chunks:got.append(data)
    attempts.append(got)
    add({'id':'new','source':source,'speaker':'speaker_1','text':'补回的字幕','start_ms':0,'end_ms':200,'audio_start_wall':0,'audio_end_wall':0})
   with patch.object(volc_asr,'stream_pcm',new=fake):await run_channel(volc_asr.VolcChannel(run,'microphone','TEST',memory,None),retry_seconds=.001)
   checkpoint=json.loads((run/'asr-checkpoint-microphone.json').read_text())
   self.assertEqual(checkpoint['byte_offset'],pcm.stat().st_size);self.assertEqual(len(attempts),2);self.assertEqual(rows[0],original);self.assertEqual(rows[1]['start_ms'],100);self.assertEqual(rows[1]['end_ms'],300)
   self.assertEqual(json.loads((run/'asr-state-microphone.json').read_text())['state'],'completed')
 async def test_idle_asr_connection_finishes_before_provider_wait_timeout_without_stopping_capture(self):
  with tempfile.TemporaryDirectory() as d:
   run=Path(d);(run/'microphone.pcm').write_bytes(b'\x01\0'*4800);calls=[]
   async def fake(chunks,*args):
    calls.append([data async for data in chunks]);(run/'CAPTURE_STOPPED').touch()
   with patch.object(volc_asr,'stream_pcm',new=fake):await run_channel(volc_asr.VolcChannel(run,'microphone','TEST',types.SimpleNamespace(add=lambda x:None),None),idle_seconds=.02)
   self.assertEqual(len(calls),1);self.assertTrue(calls[0]);self.assertEqual(json.loads((run/'asr-checkpoint-microphone.json').read_text())['byte_offset'],9600)

if __name__=='__main__':unittest.main()
