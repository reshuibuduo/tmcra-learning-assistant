import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
from .pipeline import Memory
from .deepseek_stream import respond_stream
from .selection_stream import StreamingQuestionAnswerer,event_response
from .selection_qa import public_history,StaleSelection,RequestConflict,InvalidAnswer,ProviderUnavailable
from .stream_json import partial_fields,strict_json
from .test_selection_qa import ROWS,REQUEST,ANSWER
from . import test_question_api as api_tests

CONFIG={'reasoning_model':'deepseek-flash','reasoning_timeout_seconds':5,'max_output_tokens':1000,'llm_provider':'deepseek'}

def ordered_answer(**changes):return dict({'answer':ANSWER['answer'],'example':ANSWER['example'],'status':'answered','citations':ANSWER['citations']},**changes)

async def fragments(answer=None):
    answer=answer or ordered_answer()
    for char in json.dumps(answer,ensure_ascii=False):yield {'type':'delta','text':char}
    yield {'type':'result','answer':answer}

class PrefixTests(unittest.TestCase):
    def test_every_character_boundary(self):
        value='中"文\\换行\n😀';text=json.dumps({'answer':value,'example':''})
        previous=''
        for i in range(len(text)+1):
            current=partial_fields(text[:i]).get('answer','')
            self.assertTrue(current.startswith(previous),(i,previous,current));self.assertTrue(value.startswith(current));previous=current
        self.assertEqual(previous,value)
    def test_nested_and_key_like_text_not_exposed(self):
        s=json.dumps({'citations':[{'quote':'"answer":"假的"'}],'answer':'真的','example':''},ensure_ascii=False)
        self.assertEqual(partial_fields(s),{'answer':'真的','example':''})
    def test_incomplete_nested_value_not_exposed(self):self.assertEqual(partial_fields('{"citations":[{"answer":"假的'),{})
    def test_split_surrogate_waits(self):
        self.assertEqual(partial_fields('{"answer":"\\ud83d'),{'answer':''})
        self.assertEqual(partial_fields('{"answer":"\\ud83d\\ude00'),{'answer':'😀'})
    def test_duplicate_key_rejected(self):
        for text in ['{"answer":"一","answer":"二"}','{"outer":{"x":1,"x":2}}']:
            with self.assertRaises(ValueError):strict_json(text)
        with self.assertRaises(ValueError):partial_fields('{"answer":"一","answer":"二"}')
    def test_oversized_output_and_answer_rejected(self):
        with self.assertRaises(ValueError):partial_fields(' '*32769)
        with self.assertRaises(ValueError):partial_fields('{"answer":"'+'字'*1601)
    def test_invalid_escape_rejected(self):
        with self.assertRaises(ValueError):partial_fields('{"answer":"\\x')
    def test_no_syntax_leaks(self):self.assertEqual(partial_fields('{"answer":'),{})

class StreamEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.calls=0
        async def response(*args):self.calls+=1;return ANSWER
        async def stream(*args):
            self.calls+=1
            async for event in fragments():yield event
        self.engine=StreamingQuestionAnswerer(self.root,response,stream)
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_many_real_deltas_then_verified_commit(self):
        events=[]
        async for event in self.engine.stream(REQUEST,ROWS,{},None):
            if event['type']!='done':self.assertEqual(public_history(self.root),[])
            events.append(event)
        self.assertEqual(events[0]['type'],'start');self.assertEqual(events[-1]['type'],'done')
        text=''.join(e['text'] for e in events if e['type']=='answer_delta');self.assertEqual(text,ANSWER['answer'])
        done=events[-1]['answer'];self.assertGreater(done['stream_updates'],10);self.assertEqual(done['memory_writes'],0);self.assertLessEqual(done['first_text_seconds'],done['elapsed_seconds'])
        self.assertEqual(len(public_history(self.root)),1)
    async def test_first_text_is_available_before_provider_finishes(self):
        gate=asyncio.Event()
        async def stream(*args):
            text=json.dumps(ordered_answer(),ensure_ascii=False)
            yield {'type':'delta','text':text[:13]}
            await gate.wait()
            yield {'type':'delta','text':text[13:]};yield {'type':'result','answer':ordered_answer()}
        engine=StreamingQuestionAnswerer(self.root,self.engine.respond,stream,deadline=1);it=engine.stream(REQUEST,ROWS,{},None)
        self.assertEqual((await it.__anext__())['type'],'start')
        event=await it.__anext__();self.assertEqual(event['type'],'answer_delta');self.assertFalse(gate.is_set());self.assertEqual(public_history(self.root),[])
        gate.set();events=[e async for e in it];self.assertEqual(events[-1]['type'],'done')
    async def test_cached_retry_is_explicit_and_makes_no_model_call(self):
        _=[e async for e in self.engine.stream(REQUEST,ROWS,{},None)]
        events=[e async for e in self.engine.stream(REQUEST,[],{},None)]
        self.assertEqual([e['type'] for e in events],['start','done']);self.assertTrue(events[-1]['cached']);self.assertEqual(self.calls,1)
    async def test_batch_and_stream_share_idempotency(self):
        await self.engine.ask(REQUEST,ROWS,{},None)
        events=[e async for e in self.engine.stream(REQUEST,[],{},None)];self.assertTrue(events[-1]['cached']);self.assertEqual(self.calls,1)
    async def test_request_conflict(self):
        _=[e async for e in self.engine.stream(REQUEST,ROWS,{},None)]
        with self.assertRaises(RequestConflict):_=[e async for e in self.engine.stream(dict(REQUEST,question='另一个问题'),ROWS,{},None)]
    async def test_wrong_quote_has_no_provider_call(self):
        with self.assertRaises(StaleSelection):_=[e async for e in self.engine.stream(dict(REQUEST,quote='假的'),ROWS,{},None)]
        self.assertEqual(self.calls,0)
    async def test_bad_reference_does_not_save_partial(self):
        bad=ordered_answer(citations=[{'utterance_id':'1','quote':'编造引用'}])
        self.engine.stream_respond=lambda *args:fragments(bad)
        with self.assertRaises(InvalidAnswer):_=[e async for e in self.engine.stream(REQUEST,ROWS,{},None)]
        self.assertEqual(public_history(self.root),[])
    async def test_disconnect_releases_lock_and_closes_provider(self):
        closed=[]
        async def stream(*args):
            try:yield {'type':'delta','text':'{"answer":"先出字'};await asyncio.sleep(10)
            finally:closed.append(True)
        self.engine.stream_respond=stream;it=self.engine.stream(REQUEST,ROWS,{},None)
        await it.__anext__();await it.__anext__();await it.aclose()
        self.assertEqual(closed,[True]);self.assertEqual(public_history(self.root),[])
        self.assertEqual((await self.engine.ask(REQUEST,ROWS,{},None))['status'],'answered')
    async def test_timeout_does_not_commit(self):
        async def stream(*args):yield {'type':'delta','text':'{"answer":"先出字'};await asyncio.sleep(10)
        self.engine.stream_respond=stream;self.engine.deadline=.02
        with self.assertRaises(asyncio.TimeoutError):_=[e async for e in self.engine.stream(REQUEST,ROWS,{},None)]
        self.assertEqual(public_history(self.root),[])
    async def test_incomplete_stream_is_not_final(self):
        async def stream(*args):yield {'type':'delta','text':'{"answer":"部分'}
        self.engine.stream_respond=stream
        with self.assertRaises(InvalidAnswer):_=[e async for e in self.engine.stream(REQUEST,ROWS,{},None)]
        self.assertEqual(public_history(self.root),[])
    async def test_402_keeps_semantic_code_in_sse(self):
        async def stream(*args):
            httpx.Response(402,request=httpx.Request('POST','https://api.deepseek.com/chat/completions')).raise_for_status()
            yield
        self.engine.stream_respond=stream
        response=event_response(self.engine,REQUEST,ROWS,{},None);events=[]
        async for b in response.body_iterator:events.append(json.loads(b.decode()[6:]))
        self.assertEqual(events[-1]['type'],'error');self.assertEqual(events[-1]['code'],402);self.assertIn('余额不足',events[-1]['message'])
    async def test_transcript_and_learning_background_are_not_mutated(self):
        before=json.dumps(ROWS);learner={'level':'intro','known_concepts':['函数']}
        _=[e async for e in self.engine.stream(REQUEST,ROWS,{},learner)]
        self.assertEqual(before,json.dumps(ROWS));self.assertEqual(learner['level'],'intro')

class BytesStream(httpx.AsyncByteStream):
    def __init__(self,parts):self.parts=parts;self.closed=False
    async def __aiter__(self):
        for part in self.parts:yield part
    async def aclose(self):self.closed=True

class ProviderStreamTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,finish='stop',done=True,status=200,content=None):
        with tempfile.TemporaryDirectory() as d:
            m=Memory(Path(d),'TEST_SECRET');logs=[];payloads=[]
            content=content or json.dumps(ordered_answer(),ensure_ascii=False)
            def frame(piece,reason=None,usage=None):return ('data: '+json.dumps({'id':'fixture','choices':[{'delta':{'content':piece},'finish_reason':reason}],'usage':usage},ensure_ascii=False)+'\n\n').encode()
            raw=frame(content[:20])+frame(content[20:])+frame('',finish,{'total_tokens':5})+(b'data: [DONE]\n\n' if done else b'')
            wire=BytesStream([raw[i:i+3] for i in range(0,len(raw),3)])
            def handler(request):payloads.append(json.loads(request.content));return httpx.Response(status,stream=wire)
            m.deepseek_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
            try:
                events=[x async for x in respond_stream(m,'task',{},__import__('meeting.selection_qa',fromlist=['SCHEMA']).SCHEMA,'subtitle_question',CONFIG,lambda p,x:logs.append((p.name,x)))]
                self.assertTrue(payloads[0]['stream']);self.assertEqual(payloads[0]['thinking'],{'type':'disabled'})
                self.assertGreater(len(events),1);self.assertEqual(events[-1]['type'],'result');self.assertTrue(wire.closed)
                self.assertNotIn('TEST_SECRET',json.dumps(logs));self.assertNotIn(ANSWER['answer'],json.dumps(logs,ensure_ascii=False))
                request_log=next(x for n,x in logs if n=='api-requests.jsonl');self.assertTrue(request_log['stream']);self.assertIn('first_token_seconds',request_log)
                self.assertEqual(next(x for n,x in logs if n=='api-usage.jsonl')['usage'],{'total_tokens':5})
            finally:await m.close()
    async def test_wire_stream_unicode_chunks_and_final_usage(self):await self.exercise()
    async def test_missing_done_rejected(self):
        with self.assertRaises(ValueError):await self.exercise(done=False)
    async def test_truncated_finish_rejected(self):
        with self.assertRaises(ValueError):await self.exercise(finish='length')
    async def test_provider_402_not_retried_or_fallback(self):
        with self.assertRaises(httpx.HTTPStatusError):await self.exercise(status=402)
    async def test_duplicate_json_not_accepted(self):
        with self.assertRaises(ValueError):await self.exercise(content='{"answer":"一","answer":"二"}')

class MainStreamAPITests(unittest.TestCase):
    setUp=api_tests.QuestionAPITests.setUp
    tearDown=api_tests.QuestionAPITests.tearDown
    def test_stream_route_has_auth(self):self.assertEqual(self.client.post(self.url+'/stream',json=REQUEST).status_code,401)
    def test_stream_route_and_saved_history(self):
        self.session.question_answerer.stream_respond=lambda *args:fragments()
        response=self.client.post(self.url+'/stream',json=REQUEST,headers=self.auth)
        self.assertEqual(response.status_code,200);self.assertIn('text/event-stream',response.headers['content-type'])
        self.assertEqual(response.headers['x-accel-buffering'],'no')
        events=[json.loads(x[6:]) for x in response.text.strip().split('\n\n')]
        self.assertEqual(events[-1]['type'],'done');self.assertGreater(sum(e['type']=='answer_delta' for e in events),1)
        self.assertEqual(self.session.meta['status'],'completed');self.assertIsNone(self.session.meta['seconds'])
    def test_stream_stale_error_not_success(self):
        response=self.client.post(self.url+'/stream',json=dict(REQUEST,quote='假的'),headers=self.auth)
        event=json.loads(response.text.strip()[6:]);self.assertEqual(event['type'],'error');self.assertEqual(event['code'],409)

class CompanionStreamAPITests(unittest.TestCase):
    setUp=api_tests.CompanionTests.setUp
    tearDown=api_tests.CompanionTests.tearDown
    def test_companion_stream_uses_canonical_get_and_no_capture(self):
        data={'transcripts':ROWS,'translation':{},'session':{'learner_profile':{'level':'intro'}}}
        with patch.object(self.s,'upstream',new=AsyncMock(return_value=data)) as up,patch.object(self.s.Memory,'stream_response',new=lambda *args:fragments()):
            response=self.client.post('/v1/sessions/'+'b'*32+'/questions/stream',headers=self.auth,json=REQUEST)
            events=[json.loads(x[6:]) for x in response.text.strip().split('\n\n')]
            self.assertEqual(events[-1]['type'],'done');up.assert_awaited_once_with('/v1/sessions/'+'b'*32)

if __name__=='__main__':unittest.main()
