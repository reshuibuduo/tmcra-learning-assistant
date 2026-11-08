import asyncio,copy,time,unittest
from unittest.mock import AsyncMock
import httpx
from .subtitles import options
from .parallel_subtitles import ParallelSubtitleTranslator,validate_individually

def rows(n=12):
 return [{'id':str(i),'text':'Gradient example '+str(i),'speaker':'speaker_0','source':'fixture','start_ms':i*1000,'end_ms':i*1000+800,'completed_wall':time.time(),'definite':True} for i in range(n)]
def answer(data):
 return {'target_language':data['target_language'],'items':[{'id':u['id'],'translation':'' if data['mode']=='vocabulary' else '梯度示例 '+u['id'],'same_language':False,'glosses':[]} for u in data['items']]}
async def drain(t,data,limit=100):
 for _ in range(limit):
  await t.step(lambda:data)
  if not t.snapshot(data)['pending'] and not t.jobs:return
  await asyncio.sleep(.005)
 raise AssertionError('queue did not drain')

class ParallelTests(unittest.IsolatedAsyncioTestCase):
 async def test_four_requests_overlap_and_bound(self):
  gate=asyncio.Event();active=0;peak=0;calls=[]
  async def respond(p,d,s,n):
   nonlocal active,peak
   active+=1;peak=max(peak,active);calls.append([u['id'] for u in d['items']])
   try:await gate.wait();return answer(d)
   finally:active-=1
  data=rows();t=ParallelSubtitleTranslator(respond,{'enabled':True})
  await t.step(lambda:data);self.assertEqual(peak,4);self.assertEqual(calls[0],['11']);self.assertEqual(calls[1],['0','1','2'])
  await t.step(lambda:data);self.assertEqual(len(calls),4)
  gate.set();await drain(t,data);self.assertEqual(t.metrics['peak_active_requests'],4);self.assertEqual(active,0)
  self.assertTrue(all(len(c)<=3 for c in calls));self.assertEqual(len([x for c in calls for x in c]),12)
 async def test_latest_not_blocked_by_slow_backlog(self):
  gate=asyncio.Event();calls=[]
  async def respond(p,d,s,n):
   ids=[u['id'] for u in d['items']];calls.append(ids)
   if len(ids)>1:await gate.wait()
   return answer(d)
  data=rows(10);t=ParallelSubtitleTranslator(respond,{'enabled':True});await t.step(lambda:data)
  data.append({**rows(1)[0],'id':'new','text':'New concept.'});await t.step(lambda:data)
  self.assertIn(['new'],calls);self.assertEqual(t.matching(data[-1])['status'],'translated')
  gate.set();await drain(t,data)
 async def test_order_and_raw_input_unchanged(self):
  data=rows(20);original=copy.deepcopy(data)
  async def respond(p,d,s,n):await asyncio.sleep(.002*(int(d['items'][0]['id'])%4));return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True});await drain(t,data)
  self.assertEqual([r['utterance_id'] for r in t.snapshot(data)['items']],[r['id'] for r in data]);self.assertEqual(data,original)
  self.assertTrue(all(r['request_seconds']>=0 and r['after_asr_ms']>=0 for r in t.records.values()))
 async def test_concurrent_step_claims_do_not_duplicate(self):
  data=rows(9);respond=AsyncMock(side_effect=lambda p,d,s,n:answer(d));t=ParallelSubtitleTranslator(respond,{'enabled':True})
  await asyncio.gather(*[t.step(lambda:data) for _ in range(5)]);await drain(t,data)
  ids=[u['id'] for call in respond.call_args_list for u in call.args[1]['items']];self.assertEqual(len(ids),len(set(ids)));self.assertEqual(len(ids),9)
 async def test_stale_correction_discarded(self):
  data=rows(1);gate=asyncio.Event()
  async def respond(p,d,s,n):await gate.wait();return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True});await t.step(lambda:data);data[0]['text']='Corrected concept.';gate.set();await asyncio.gather(*t.jobs)
  self.assertEqual(t.snapshot(data)['pending'],1);self.assertEqual(t.metrics['stale_results_discarded'],1)
  await drain(t,data);self.assertEqual(t.records['0']['source_text'],'Corrected concept.')
 async def test_removed_row_not_published(self):
  data=rows(1);gate=asyncio.Event()
  async def respond(p,d,s,n):await gate.wait();return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True});await t.step(lambda:data);data.clear();gate.set();await asyncio.gather(*t.jobs);await t.step(lambda:data)
  self.assertEqual(t.records,{});self.assertEqual(t.metrics['stale_results_discarded'],1)
 async def test_mode_change_cancels_old_work(self):
  data=rows();gate=asyncio.Event()
  async def respond(p,d,s,n):await gate.wait();return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True});await t.step(lambda:data);t.configure({'enabled':True,'target_language':'en'});await asyncio.sleep(.01)
  self.assertEqual(t.metrics['active_requests'],0);self.assertFalse(t.records);gate.set();await drain(t,data)
  self.assertTrue(all(r['target_language']=='en' for r in t.records.values()))
 async def test_pause_close_leave_no_running_requests(self):
  data=rows();respond=AsyncMock(side_effect=lambda *a:None)
  async def slow(*a):await asyncio.sleep(100)
  t=ParallelSubtitleTranslator(slow,{'enabled':True});await t.step(lambda:data);await t.close();t.configure({'enabled':False});await t.step(lambda:data)
  self.assertEqual(t.metrics['active_requests'],0);self.assertEqual(t.jobs,{});self.assertFalse(t.records)
 async def test_cached_success_skips_request(self):
  data=rows(1);respond=AsyncMock(side_effect=lambda p,d,s,n:answer(d));t=ParallelSubtitleTranslator(respond,{'enabled':True});await drain(t,data)
  cached=t.snapshot(data);responder=AsyncMock();reopened=ParallelSubtitleTranslator(responder,{'enabled':True},cached);await reopened.step(lambda:data);responder.assert_not_awaited()
 async def test_partial_and_disabled_do_not_call_provider(self):
  data=rows(1);responder=AsyncMock();t=ParallelSubtitleTranslator(responder);await t.step(lambda:data);t.configure({'enabled':True});data[0]['definite']=False;await t.step(lambda:data);responder.assert_not_awaited()
 async def test_single_invalid_row_does_not_fail_batch(self):
  data=rows(4)
  async def respond(p,d,s,n):
   out=answer(d)
   for x in out['items']:
    if x['id']=='1':x['translation']=''
   return out
  t=ParallelSubtitleTranslator(respond,{'enabled':True});await drain(t,data)
  self.assertEqual(t.snapshot(data)['errors'],1);self.assertEqual(t.records['1']['error_detail'],'empty_translation')
  self.assertTrue(all(t.records[x]['status']=='translated' for x in ('0','2','3')))
 async def test_transient_429_retries_once(self):
  data=rows(1);calls=0
  async def respond(p,d,s,n):
   nonlocal calls
   calls+=1
   if calls==1:raise httpx.HTTPStatusError('rate',request=httpx.Request('POST','https://example.invalid'),response=httpx.Response(429,headers={'retry-after':'.01'}))
   return answer(d)
  t=ParallelSubtitleTranslator(respond,{'enabled':True},retry_base=.01);await drain(t,data)
  self.assertEqual(calls,2);self.assertEqual(t.metrics['retries'],1);self.assertEqual(t.snapshot(data)['errors'],0)
 async def test_timeout_is_bounded_and_visible(self):
  data=rows(1)
  async def slow(*a):await asyncio.sleep(1)
  t=ParallelSubtitleTranslator(slow,{'enabled':True},deadline=.01,retry_base=.01);await drain(t,data)
  self.assertEqual(t.metrics['requests_started'],2);self.assertEqual(t.records['0']['error'],'timeout');self.assertEqual(t.metrics['active_requests'],0)
 async def test_401_does_not_retry(self):
  data=rows(1);exc=httpx.HTTPStatusError('auth',request=httpx.Request('POST','https://example.invalid'),response=httpx.Response(401));respond=AsyncMock(side_effect=exc)
  t=ParallelSubtitleTranslator(respond,{'enabled':True},retry_base=.001);await drain(t,data);self.assertEqual(respond.await_count,1);self.assertEqual(t.snapshot(data)['errors'],1)
 async def test_validation_failure_requires_explicit_retry(self):
  data=rows(1);respond=AsyncMock(return_value={});t=ParallelSubtitleTranslator(respond,{'enabled':True});await drain(t,data);await t.step(lambda:data);self.assertEqual(respond.await_count,1)
  respond.side_effect=lambda p,d,s,n:answer(d);t.configure(t.config,retry=True);await drain(t,data);self.assertEqual(respond.await_count,2);self.assertEqual(t.snapshot(data)['errors'],0)
 async def test_vocabulary_shortcut_and_oversized(self):
  data=rows(2);data[0]['text']='纯中文句子。';data[1]['text']='a'*4001;respond=AsyncMock();t=ParallelSubtitleTranslator(respond,{'enabled':True,'mode':'vocabulary'});await drain(t,data)
  respond.assert_not_awaited();self.assertEqual(t.records['0']['text'],'');self.assertEqual(t.records['1']['error'],'source_too_long')
 async def test_retry_after_caps_and_close_cancels_wait(self):
  data=rows(1);exc=httpx.HTTPStatusError('rate',request=httpx.Request('POST','https://example.invalid'),response=httpx.Response(429,headers={'retry-after':'9999'}));t=ParallelSubtitleTranslator(AsyncMock(side_effect=exc),{'enabled':True})
  await t.step(lambda:data);self.assertGreater(t.snapshot(data)['retry_after_seconds'],29);self.assertLessEqual(t.snapshot(data)['retry_after_seconds'],30);await t.close();self.assertEqual(t.metrics['active_requests'],0)

class IndividualValidationTests(unittest.TestCase):
 def test_invalid_envelope_affects_only_selected(self):
  r=rows(2);self.assertEqual(set(validate_individually({},r,options())),{'0','1'})
 def test_duplicate_missing_and_unknown_id(self):
  r=rows(3);a=answer({'items':r,'target_language':'zh-CN','mode':'full'});a['items']=[a['items'][0],a['items'][0],a['items'][2],{'id':'unknown'}]
  out=validate_individually(a,r,options());self.assertIn('error',out['0']);self.assertIn('error',out['1']);self.assertEqual(out['2']['text'],'梯度示例 2');self.assertNotIn('unknown',out)
 def test_ungrounded_gloss_is_not_weakened(self):
  r=rows(1);a=answer({'items':r,'target_language':'zh-CN','mode':'assisted'});a['items'][0]['glosses']=[{'term':'hallucination','meaning':'幻觉','explanation':''}]
  self.assertEqual(validate_individually(a,r,options({'mode':'assisted'}))['0']['error'],'term_not_grounded_in_source')
 def test_concurrency_bounds(self):
  for n in (0,9,True,2.5):
   with self.assertRaises(ValueError):ParallelSubtitleTranslator(AsyncMock(),concurrency=n)
