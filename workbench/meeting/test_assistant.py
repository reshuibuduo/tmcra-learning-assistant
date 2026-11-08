import asyncio
import copy
import gzip
import importlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
from .volc_asr import packet,decode,request_body,Segments,stream_pcm
from .coach import Coach,validate_cards,native_records
from .pipeline import Memory
from .settings import settings

def row(text='预算五百。',start=0,end=1000,speaker=0,definite=True):
    return {'start_time':start,'end_time':end,'text':text,'definite':definite,'additions':{'speaker_id':speaker}}

class AsrTests(unittest.TestCase):
    def test_request_packet(self):
        raw=packet(1,request_body(),1,is_json=True)
        self.assertEqual(raw[:4],bytes.fromhex('11111100'))
        self.assertEqual(struct.unpack('>iI',raw[4:12]),(1,len(raw)-12))
        body=json.loads(gzip.decompress(raw[12:]))
        self.assertEqual(body['audio']['rate'],16000)
        for field in ['enable_nonstream','enable_speaker_info','show_utterances']:
            self.assertTrue(body['request'][field])
        self.assertFalse(body['request']['enable_ddc'])

    def test_last_packet_negative_sequence(self):
        raw=packet(2,b'\0\0',4,final=True)
        self.assertEqual(raw[1],0x23);self.assertEqual(struct.unpack('>i',raw[4:8])[0],-4)

    def test_server_decode(self):
        body={'result':{'utterances':[row()]}}
        payload=gzip.compress(json.dumps(body).encode())
        raw=bytes.fromhex('11931100')+struct.pack('>iI',-4,len(payload))+payload
        decoded,final=decode(raw)
        self.assertEqual(decoded,body);self.assertTrue(final)

    def test_error_response_redacts_vendor_body(self):
        data=b'credentials should never be echoed'
        raw=bytes.fromhex('11f01000')+struct.pack('>II',45000010,len(data))+data
        with self.assertRaisesRegex(RuntimeError,'^VOLC_ASR_ERROR_45000010$'):decode(raw)

    def test_malformed_frame_rejected(self):
        for raw in [b'',b'12345678',bytes.fromhex('11911000')+struct.pack('>iI',1,999)+b'{}']:
            with self.assertRaises((ValueError,struct.error)):decode(raw)

    def test_partial_does_not_enter_memory(self):
        s=Segments('session')
        self.assertEqual(s.accept({'result':{'text':'预','utterances':[row(definite=False)]}},.5),[])
        self.assertEqual(s.all(),[])

    def test_final_speakers_are_anonymous(self):
        s=Segments('session',start_wall=100)
        changed=s.accept({'result':{'utterances':[row(),row('另一个人。',1100,2000,1)]}},2.8)
        self.assertEqual([u['speaker'] for u in changed],['speaker_0','speaker_1'])
        self.assertEqual(changed[0]['audio_start_wall'],100)

    def test_duplicate_final_ignored(self):
        s=Segments('session');body={'result':{'utterances':[row()]}}
        s.accept(body,2);self.assertEqual(s.accept(body,3),[])

    def test_revision_replaces_overlap(self):
        s=Segments('session');s.accept({'result':{'utterances':[row()]}},2)
        changes=s.accept({'result':{'utterances':[row('预算改为五百。',0,1200)]}},3)
        self.assertEqual(len(s.all()),1);self.assertEqual(len(changes[0]['supersedes_utterance_ids']),1)

    def test_unknown_speaker_not_guessed(self):
        s=Segments('session');u=row();u.pop('additions')
        self.assertEqual(s.accept({'result':{'utterances':[u]}},2)[0]['speaker'],'unknown')

    def test_observed_missing_utterance_start_uses_word_boundary(self):
        s=Segments('session');u=row();u.pop('start_time');u['words']=[{'start_time':40,'end_time':200,'text':'预'}]
        out=s.accept({'result':{'utterances':[u]}},2)[0]
        self.assertEqual(out['start_ms'],40);self.assertEqual(out['timestamp_source'],'word_start_fallback')

    def test_handshake_without_asr_auth_sends_no_audio(self):
        async def exercise():
            data=b'authentication failed'
            error=bytes.fromhex('11f01000')+struct.pack('>II',45000010,len(data))+data
            ws=AsyncMock();ws.recv.return_value=error
            async def chunks():
                self.fail('audio consumed before ASR initialization')
                yield b'\0\0'
            connected=MagicMock()
            with tempfile.TemporaryDirectory() as td,patch('meeting.volc_asr.websockets.connect') as connect:
                connect.return_value.__aenter__.return_value=ws
                with self.assertRaisesRegex(RuntimeError,'VOLC_ASR_ERROR_45000010'):
                    await stream_pcm(chunks(),settings(),'TEST',Path(td),on_connected=connected)
                connected.assert_not_called();self.assertEqual(ws.send.await_count,1);self.assertIsNone(connect.call_args.kwargs['proxy'])
        asyncio.run(exercise())

class MemoryTests(unittest.TestCase):
    def test_memory_upsert_and_partial_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            m=Memory(Path(td),'TEST')
            u={'id':'a','text':'三百','speaker':'speaker_0','audio_start_wall':1,'definite':True}
            m.add(u);m.add(u);self.assertEqual(m.revision,1)
            m.add({**u,'text':'五百'});self.assertEqual(len(m.utterances),1)
            m.add({**u,'id':'b','definite':False});self.assertEqual(len(m.utterances),1)

    def test_superseded_segments_removed(self):
        with tempfile.TemporaryDirectory() as td:
            m=Memory(Path(td),'TEST');m.add({'id':'a','text':'三百','audio_start_wall':1})
            m.add({'id':'b','text':'五百','audio_start_wall':1,'supersedes_utterance_ids':['a']})
            self.assertEqual([x['id'] for x in m.utterances],['b'])

    def test_openai_model_routing(self):
        response=MagicMock();response.status_code=200;response.headers={}
        response.json.return_value={'status':'completed','output':[{'content':[{'type':'output_text','text':'{}'}]}]}
        client=AsyncMock();client.post.return_value=response
        async def exercise():
            with tempfile.TemporaryDirectory() as td,patch('meeting.pipeline.httpx.AsyncClient') as factory:
                factory.return_value.__aenter__.return_value=client
                m=Memory(Path(td),'TEST')
                for operation,model in [('recall_plan','gpt-6-luna'),('grounded_reminder','gpt-6.1-sol')]:
                    await m.response('test',{}, {'type':'object'},operation)
                    payload=client.post.call_args.kwargs['json']
                    self.assertEqual(payload['model'],model);self.assertEqual(payload['reasoning'],{'effort':'low'})
                    self.assertFalse(payload['store'])
        asyncio.run(exercise())

class CoachTests(unittest.TestCase):
    def setUp(self):
        self.transcript=[{'id':'u','text':'谈论softmax','definite':True}]
        self.records=[{'source_id':'s','text':'已学习方差相加。','learning_state':'studied'}]
        self.answer={'action':'show','cards':[{'kind':'knowledge_bridge','title':'联系方差','body':'结合方差理解缩放。','follow_up':'为什么？','utterance_ids':['u'],'citations':[{'source_id':'s','quote':'方差相加'}]}]}
        self.plan={'action':'retrieve','topic_key':'softmax','queries':['方差与softmax有什么关系？']}

    def test_valid_grounded_card(self):
        card=validate_cards(self.answer,self.transcript,self.records)[0]
        self.assertEqual(card['display_mode'],'quiet_card')

    def test_invented_evidence_rejected(self):
        self.answer['cards'][0]['citations'][0]['source_id']='fake'
        with self.assertRaises(ValueError):validate_cards(self.answer,self.transcript,self.records)

    def test_nonverbatim_quote_rejected(self):
        self.answer['cards'][0]['citations'][0]['quote']='已经完全掌握'
        with self.assertRaises(ValueError):validate_cards(self.answer,self.transcript,self.records)

    def test_mention_is_not_learning(self):
        self.records[0]['learning_state']='unknown'
        with self.assertRaisesRegex(ValueError,'no verified learning'):validate_cards(self.answer,self.transcript,self.records)

    def test_partial_evidence_rejected(self):
        self.transcript[0]['definite']=False
        with self.assertRaises(ValueError):validate_cards(self.answer,self.transcript,self.records)

    def test_empty_retrieval_stays_silent(self):
        async def exercise():
            respond=AsyncMock(return_value=self.plan);c=Coach(respond,AsyncMock(return_value=[]))
            result=await c.run(self.transcript)
            self.assertEqual(result['reason'],'no_memory_evidence');self.assertEqual(respond.await_count,1)
        asyncio.run(exercise())

    def test_duplicate_and_cooldown(self):
        async def exercise():
            respond=AsyncMock(side_effect=[self.plan,self.answer]);c=Coach(respond,AsyncMock(return_value=self.records))
            await c.run(self.transcript)
            self.assertEqual((await c.run(self.transcript))['reason'],'unchanged_transcript')
            self.transcript[0]['text']='新的讨论'
            self.assertEqual((await c.run(self.transcript))['reason'],'cooldown')
            self.assertEqual(respond.await_count,2)
        asyncio.run(exercise())

    def test_stale_context_not_shown(self):
        async def exercise():
            c=Coach(AsyncMock(side_effect=[self.plan,self.answer]),AsyncMock(return_value=self.records))
            self.assertEqual((await c.run(self.transcript,1,lambda:2))['reason'],'stale_context')
        asyncio.run(exercise())

    def test_excess_queries_rejected(self):
        self.plan['queries']=['one','two','three']
        async def exercise():
            c=Coach(AsyncMock(return_value=self.plan),AsyncMock(return_value=[]))
            with self.assertRaises(ValueError):await c.run(self.transcript)
        asyncio.run(exercise())

    def test_native_records_do_not_invent_learning_state(self):
        rows=native_records({'query_id':'q','evidence':{'evidence_windows':[{'text':'原文'}]},'summary':'derived'},'test')
        self.assertEqual(rows[0]['learning_state'],'unknown');self.assertNotIn('derived',str(rows))

class ServiceTests(unittest.TestCase):
    def test_disabled_capture_and_no_write_candidate(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'secrets').mkdir()
            for name in ['meeting_token','openai_api_key','tmcra_api_key','volc_api_key']:
                (root/'secrets'/name).write_text('TEST')
            with patch.dict(os.environ,{'MEETING_ROOT':td,'TMCRA_BASE_URL':'http://127.0.0.1:22109'}):
                import sys
                sys.modules.pop('meeting.service',None)
                service=importlib.import_module('meeting.service')
                with patch('meeting.service.settings',return_value={**settings(),'capture_enabled':False}),TestClient(service.app) as client:
                    health=client.get('/healthz').json()
                    self.assertFalse(health['capture_enabled']);self.assertFalse(health['tmcra_write'])
                    self.assertEqual(client.post('/v1/sessions',json={},headers={'Authorization':'Bearer TEST'}).status_code,409)

if __name__=='__main__':unittest.main()
