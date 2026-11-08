import asyncio, copy, importlib, json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from .pipeline import Memory
from .deepseek import provider_key
from .parallel_subtitles import ParallelSubtitleTranslator
from .test_parallel_subtitles import rows, answer
from .rolling import WindowGate, context_current
from .concepts import validate, profile
from .coach import Coach

CONFIG={'llm_provider':'deepseek','planner_model':'deepseek-flash','reasoning_model':'deepseek-flash',
 'translation_model':'deepseek-flash','reasoning_timeout_seconds':30,'max_output_tokens':4000}
SCHEMA={'type':'object','additionalProperties':False,'properties':{'ok':{'type':'boolean'}},'required':['ok']}

class AdapterTests(unittest.IsolatedAsyncioTestCase):
 async def exercise(self, content='{"ok":true}', finish='stop', status=200, repeat=1):
  with tempfile.TemporaryDirectory() as td,patch('meeting.pipeline.settings',return_value=CONFIG),patch('meeting.deepseek.httpx.AsyncClient') as factory:
   client=AsyncMock();client.is_closed=False;factory.return_value=client
   response=httpx.Response(status,request=httpx.Request('POST','https://api.deepseek.com/chat/completions'),json={'choices':[{'finish_reason':finish,'message':{'content':content}}],'usage':{'prompt_cache_hit_tokens':12},'id':'test'})
   client.post.return_value=response;m=Memory(Path(td),'TEST_SECRET')
   for _ in range(repeat):result=await m.response('JSON task',{},SCHEMA,'learning_route')
   payload=client.post.call_args.kwargs['json'];self.assertEqual(payload['thinking'],{'type':'disabled'});self.assertEqual(payload['model'],'deepseek-flash')
   self.assertFalse(factory.call_args.kwargs['trust_env']);self.assertEqual(factory.call_count,1)
   self.assertNotIn('TEST_SECRET',(Path(td)/'api-requests.jsonl').read_text());self.assertNotIn('api.openai.com',client.post.call_args.args[0])
   await m.close();client.aclose.assert_awaited_once();return result
 async def test_direct_json_and_reuse(self):self.assertEqual(await self.exercise(repeat=2),{'ok':True})
 async def test_blank_rejected(self):
  with self.assertRaisesRegex(ValueError,'blank'):await self.exercise('   ')
 async def test_truncated_rejected(self):
  with self.assertRaisesRegex(ValueError,'incomplete'):await self.exercise(finish='length')
 async def test_missing_field_rejected(self):
  with self.assertRaisesRegex(ValueError,'schema'):await self.exercise('{}')
 async def test_wrong_type_rejected(self):
  with self.assertRaisesRegex(ValueError,'schema'):await self.exercise('{"ok":"true"}')
 async def test_extra_field_rejected(self):
  with self.assertRaisesRegex(ValueError,'schema'):await self.exercise('{"ok":true,"extra":1}')
 async def test_http_failure_not_fallback(self):
  with self.assertRaises(httpx.HTTPStatusError):await self.exercise(status=401)
 async def test_invalid_json_rejected(self):
  with self.assertRaises(json.JSONDecodeError):await self.exercise('not JSON')

class WindowTests(unittest.IsolatedAsyncioTestCase):
 async def test_subtitle_coalesces_and_retains_context(self):
  now=[0.];data=rows(1);calls=[]
  async def respond(p,d,s,n):calls.append(copy.deepcopy(d));return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True},window_seconds=1.2,clock=lambda:now[0])
  await t.step(lambda:data);self.assertEqual(len(calls),0)
  data.extend([{**rows(1)[0],'id':'b'},{**rows(1)[0],'id':'c'}]);now[0]=.8
  await t.step(lambda:data);self.assertEqual(len(calls),0)
  now[0]=1.21;await t.step(lambda:data);self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]['items']),3)
  data.append({**rows(1)[0],'id':'d'});now[0]=2;await t.step(lambda:data);now[0]=3.3;await t.step(lambda:data)
  self.assertEqual(len(calls),2);self.assertEqual(len(calls[1]['context']),3);await t.close()
 async def test_subtitle_finish_flushes_without_wait(self):
  calls=AsyncMock(side_effect=lambda p,d,s,n:answer(d));data=rows(1)
  t=ParallelSubtitleTranslator(calls,{'enabled':True},window_seconds=1.2)
  await t.step(lambda:data,flush=True);self.assertEqual(calls.await_count,1);await t.close()
 async def test_rolling_parallelism_and_no_duplicate_claims(self):
  data=rows(24);gate=asyncio.Event();calls=[]
  async def f(p,d,s,n):calls.extend(x['id'] for x in d['items']);await gate.wait();return answer(d)
  t=ParallelSubtitleTranslator(f,{'enabled':True},window_seconds=1.2)
  await t.step(lambda:data,flush=True);self.assertEqual(t.metrics['peak_active_requests'],4)
  await t.step(lambda:data,flush=True);self.assertEqual(len(calls),24);self.assertEqual(len(set(calls)),24)
  gate.set();await asyncio.gather(*t.jobs);await t.close()
 async def test_learning_gate_merges_updates_and_ignores_fillers(self):
  now=[0.];g=WindowGate(clock=lambda:now[0]);data=[{'id':'a','text':'嗯嗯'}]
  self.assertIsNone(g.take(data));self.assertEqual(g.metrics['ignored_rows'],1)
  data += [{'id':'b','text':'现在学习 Transformer 注意力的 Q 和 K。'}]
  self.assertIsNone(g.take(data));now[0]=2;data += [{'id':'c','text':'它们的点积经过 softmax 后给 V 加权。'}]
  self.assertIsNone(g.take(data));now[0]=4.1;w=g.take(data)
  self.assertEqual(len(w['delta']),2);self.assertEqual(g.metrics['windows'],1);self.assertIsNone(g.take(data))
 async def test_input_waited_during_request_does_not_wait_another_full_window(self):
  g=WindowGate();data=[{'id':'a','text':'这是等待处理的新的注意力机制讲解，我们继续讨论它为什么这样计算。','completed_wall':time.time()-5}]
  self.assertIsNotNone(g.take(data))
 async def test_short_concept_is_not_dropped_forever(self):
  now=[0.];g=WindowGate(clock=lambda:now[0]);data=[{'id':'a','text':'梯度下降'}]
  self.assertIsNone(g.take(data));now[0]=8.1;self.assertIsNotNone(g.take(data))
 async def test_short_correction_triggers_urgent_window(self):
  now=[0.];g=WindowGate(clock=lambda:now[0]);data=[{'id':'a','text':'预算改为五百元'}]
  self.assertIsNone(g.take(data));now[0]=1.1;self.assertIsNotNone(g.take(data))
 async def test_context_window_is_bounded(self):
  g=WindowGate();data=[{'id':str(i),'text':'有意义的概念'*400} for i in range(10)]
  w=g.take(data,flush=True);self.assertLessEqual(sum(len(u['text']) for u in w['transcript']),6000)
 async def test_short_substantive_flushes_at_max_wait(self):
  now=[0.];g=WindowGate(clock=lambda:now[0]);data=[{'id':'a','text':'现在讨论贝叶斯定理'}]
  self.assertIsNone(g.take(data));now[0]=4.1;self.assertIsNone(g.take(data));now[0]=8.1;self.assertIsNotNone(g.take(data))
 async def test_new_during_inflight_is_latest_snapshot_not_queue(self):
  g=WindowGate();data=rows(2);one=g.take(data,flush=True);data+=rows(6)[2:]
  two=g.take(data,flush=True);self.assertEqual(len(one['delta']),2);self.assertEqual(len(two['delta']),4);self.assertEqual(len(two['transcript']),6)
 async def test_partial_and_unchanged_no_window(self):
  g=WindowGate();data=rows(1);data[0]['definite']=False;self.assertIsNone(g.take(data,flush=True))
  data[0]['definite']=True;self.assertIsNotNone(g.take(data,flush=True));self.assertIsNone(g.take(data,flush=True))
 async def test_append_not_stale_but_correction_or_topic_break_is(self):
  data=rows(2);old=copy.deepcopy(data);start=time.monotonic();data.append({**rows(1)[0],'id':'b','text':'这是同一推导的下一步。'})
  self.assertTrue(context_current(old,data,start));data[-1]['text']='更正，前面的说法有误。';self.assertFalse(context_current(old,data,start))
  data=copy.deepcopy(old);data[0]['text']='corrected';self.assertFalse(context_current(old,data,start));self.assertFalse(context_current(old,old,start-30))
 async def test_concept_can_reference_earlier_row_in_window(self):
  data=[{'id':'a','text':'接下来用贝叶斯定理更新概率。'},{'id':'b','text':'请看这里的计算。'}]
  a={'action':'show','reason':'needed','cards':[{'concept':'贝叶斯定理','intuition':'用新线索更新概率。','example':'见到乌云后提高下雨概率。','connection':'这里更新先前判断。','utterance_id':'a','evidence_quote':data[0]['text']}]}
  self.assertEqual(validate(a,data,profile())[0]['concept'],'贝叶斯定理')
 async def test_provider_key_does_not_require_openai(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'secrets').mkdir();(p/'secrets/deepseek_api_key').write_text('TEST_DS')
   self.assertEqual(provider_key(p,CONFIG),'TEST_DS')

class RollingServiceTests(unittest.TestCase):
 def test_invalid_concept_does_not_block_independent_recall(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'secrets').mkdir()
   for n in ['meeting_token','deepseek_api_key','tmcra_api_key','volc_api_key']:(root/'secrets'/n).write_text('TEST')
   cfg=json.loads((Path(__file__).parent.parent/'ops/deepseek-config.json').read_text())
   f=root/'config.json';f.write_text(json.dumps(cfg))
   with patch.dict(os.environ,MEETING_ROOT=str(root),MEETING_CONFIG=str(f),TMCRA_MEMORY_BYPASS='1',TMCRA_BASE_URL='http://127.0.0.1:22109'):
    sys.modules.pop('meeting.service',None);s=importlib.import_module('meeting.service')
    session=s.Session.__new__(s.Session);session.meta={'status':'completed'};session.run=root
    session.memory=MagicMock(revision=1,utterances=rows(1))
    session.learning_gate=MagicMock();session.learning_gate.take.side_effect=[{'transcript':rows(1),'delta':rows(1),'window_wait_seconds':4},None]
    session.explainer=MagicMock(last_shown=-1e10);session.explainer.run=AsyncMock(side_effect=ValueError('invalid concept'))
    session.coach=MagicMock(last_shown=-1e10);session.coach.run=AsyncMock(return_value={'action':'show','reason':'relevant','cards':[]})
    with patch.object(s,'recall_scopes',return_value=['test']):asyncio.run(session.learning_loop())
    result=json.loads((root/'learning-windows.jsonl').read_text())
    self.assertTrue((root/'coach.json').exists(),result)
    self.assertEqual(json.loads((root/'coach.json').read_text())['action'],'show')
    self.assertEqual(result['concept_error'],'ValueError');self.assertEqual(result['recall_action'],'show')
 def test_automatic_learning_checks_once_for_multiple_rows(self):
  from fastapi.testclient import TestClient
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'secrets').mkdir()
   for n in ['meeting_token','deepseek_api_key','tmcra_api_key','volc_api_key']:(root/'secrets'/n).write_text('TEST')
   cfg=json.loads((Path(__file__).parent.parent/'ops/deepseek-config.json').read_text())
   cfg.update(learning_window_seconds=.1,learning_max_wait_seconds=.2,translation_window_seconds=.1)
   f=root/'config.json';f.write_text(json.dumps(cfg));calls=[]
   async def respond(memory,p,d,sc,n):
    calls.append((n,copy.deepcopy(d)))
    if n=='subtitle_translation':return answer(d)
    if n=='concept_explanation':
     u=d['transcript'][0]
     return {'action':'show','reason':'needed','cards':[{'concept':'贝叶斯定理','intuition':'用新线索更新概率。','example':'乌云使下雨更可能。','connection':'这里更新先前判断。','utterance_id':u['id'],'evidence_quote':u['text']}]}
    raise AssertionError('unexpected extra request '+n)
   with patch.dict(os.environ,MEETING_ROOT=str(root),MEETING_CONFIG=str(f),TMCRA_MEMORY_BYPASS='1',TMCRA_BASE_URL='http://127.0.0.1:22109'),patch('meeting.pipeline.Memory.response',new=respond):
    sys.modules.pop('meeting.service',None);s=importlib.import_module('meeting.service')
    with TestClient(s.app) as c:
     auth={'Authorization':'Bearer TEST'}
     v=c.post('/v1/sessions',headers=auth,json={'mode':'text_fixture','assistant_enabled':True,'translation':{'enabled':True}}).json();sid=v['session']['id']
     c.post('/v1/sessions/'+sid+'/text-fixture',headers=auth,json={'texts':['接下来我们用贝叶斯定理来更新这个概率。','新的证据会改变原先的概率判断。','请结合前面的例子理解这个更新过程。']})
     for _ in range(200):
      result=c.get('/v1/sessions/'+sid,headers=auth).json()
      if (result.get('concept') or {}).get('action')=='show' and result['translation']['pending']==0:break
      time.sleep(.01)
     self.assertEqual(result['concept']['action'],'show');self.assertEqual(result['translation']['pending'],0)
     self.assertEqual([x[0] for x in calls].count('concept_explanation'),1)
     self.assertEqual([x[0] for x in calls].count('subtitle_translation'),1)
     self.assertEqual(len(next(x[1]['transcript'] for x in calls if x[0]=='concept_explanation')),3)
     self.assertTrue(c.get('/healthz').json()['llm_direct'])

class GroundingRepairTests(unittest.IsolatedAsyncioTestCase):
 async def exercise(self,repair_valid):
  cfg=json.loads((Path(__file__).parent.parent/'ops/deepseek-config.json').read_text())
  transcript=[{'id':'fixture:1234567890abcdef','text':'解释softmax','definite':True}]
  records=[{'source_id':'source:long-id','text':'曾讨论过softmax归一化。','learning_state':'discussed'}]
  plan={'action':'retrieve','topic_key':'softmax','queries':['softmax']}
  valid={'action':'show','cards':[{'kind':'knowledge_bridge','title':'已有讨论','body':'联系之前的归一化讨论。','follow_up':'','utterance_ids':[transcript[0]['id']],'citations':[{'source_id':records[0]['source_id'],'quote':'softmax归一化'}]}]}
  invalid=copy.deepcopy(valid);invalid['cards'][0]['utterance_ids']=['invented']
  responder=AsyncMock(side_effect=[plan,invalid,valid if repair_valid else invalid]);coach=Coach(responder,AsyncMock(return_value=records))
  with patch('meeting.coach.settings',return_value=cfg):
   if repair_valid:
    result=await coach.run(transcript);self.assertEqual(result['action'],'show');self.assertEqual(result['grounding_repairs'],1)
   else:
    with self.assertRaisesRegex(ValueError,'unverified transcript'):await coach.run(transcript)
    self.assertEqual(coach.last_shown,-float('inf'))
  self.assertEqual(responder.await_count,3);self.assertEqual(responder.call_args.args[3],'grounded_reminder_repair')
  self.assertEqual(responder.call_args.args[1]['validation_error'],'unverified transcript reference')
 async def test_one_repair_preserves_evidence_validation(self):await self.exercise(True)
 async def test_repair_is_bounded_and_invalid_result_stays_hidden(self):await self.exercise(False)
