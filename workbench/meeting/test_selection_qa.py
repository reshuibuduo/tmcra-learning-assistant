import asyncio,json,tempfile,unittest,httpx
from pathlib import Path
from .selection_qa import QuestionAnswerer,question_context,validate_answer,public_history,StaleSelection,RequestConflict,InvalidAnswer,QuestionBusy,ProviderUnavailable

ROWS=[{'id':str(i),'text':text,'definite':True,'start_ms':i*1000,'speaker':'speaker_1'} for i,text in enumerate(['先看损失函数。','我们用梯度下降更新参数。','梯度指向函数增长最快的方向。','所以沿负梯度走一小步。','学习率控制每一步的长度。','继续后面的例子。'])]
REQUEST={'request_id':'qa-test-001','utterance_id':'1','source':'original','quote':'梯度下降','question':'为什么沿负梯度走？'}
ANSWER={'status':'answered','answer':'梯度指向上坡方向，反着走通常能让损失变小。','example':'像下山时朝脚下最陡的下坡方向迈一小步。','citations':[{'utterance_id':'1','quote':'我们用梯度下降更新参数。'}]}

class ContextTests(unittest.TestCase):
 def test_selected_old_row_not_last_window(self):
  rows=ROWS+[{'id':str(i),'text':'后来讨论别的内容。'} for i in range(6,50)]
  value=question_context(rows,{},REQUEST)
  self.assertEqual([r['id'] for r in value['context']],['0','1','2','3'])
 def test_exact_original_required(self):
  with self.assertRaises(StaleSelection):question_context(ROWS,{},dict(REQUEST,quote='虚构术语'))
 def test_missing_anchor_rejected(self):
  with self.assertRaises(StaleSelection):question_context(ROWS,{},dict(REQUEST,utterance_id='missing'))
 def test_unfinished_asr_not_accepted(self):
  with self.assertRaises(StaleSelection):question_context([dict(ROWS[1],definite=False)],{},REQUEST)
 def test_translated_selection(self):
  t={'items':[{'utterance_id':'1','status':'translated','source_text':ROWS[1]['text'],'text':'We use gradient descent to update parameters.'}]}
  p=question_context(ROWS,t,dict(REQUEST,source='translation',quote='gradient descent'))
  self.assertEqual(p['selected']['original_text'],ROWS[1]['text'])
 def test_translation_of_superseded_source_rejected(self):
  t={'items':[{'utterance_id':'1','status':'translated','source_text':'已经更正的旧原文','text':'gradient descent'}]}
  with self.assertRaises(StaleSelection):question_context(ROWS,t,dict(REQUEST,source='translation',quote='gradient descent'))
 def test_translation_cannot_quote_a_different_row(self):
  with self.assertRaises(StaleSelection):question_context(ROWS,{'items':[{'utterance_id':'2','status':'translated','text':'gradient descent'}]},dict(REQUEST,source='translation',quote='gradient descent'))
 def test_context_is_bounded_and_keeps_selection(self):
  rows=[{'id':str(i),'text':'x'*6000} for i in range(5)];rows[2]['text']='x'*4500+'SELECTED'+'y'*2000
  p=question_context(rows,{},dict(REQUEST,utterance_id='2',quote='SELECTED'))
  self.assertLessEqual(sum(len(r['text']) for r in p['context']),10000)
  self.assertIn('SELECTED',next(r['text'] for r in p['context'] if r['id']=='2'))
 def test_reference_fabrication_rejected(self):
  with self.assertRaises(InvalidAnswer):validate_answer(dict(ANSWER,citations=[{'utterance_id':'1','quote':'梯度一定保证全局最优。'}]),ROWS)
 def test_empty_answer_rejected(self):
  with self.assertRaises(InvalidAnswer):validate_answer(dict(ANSWER,answer=''),ROWS)
 def test_model_must_supply_source(self):
  with self.assertRaises(InvalidAnswer):validate_answer(dict(ANSWER,citations=[]),ROWS)

class QuestionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name);self.calls=[]
  async def respond(prompt,data,schema,name):self.calls.append((prompt,data,schema,name));return dict(ANSWER)
  self.engine=QuestionAnswerer(self.run,respond)
 async def asyncTearDown(self):self.tmp.cleanup()
 async def test_answer_uses_profile_and_no_recall(self):
  result=await self.engine.ask(REQUEST,ROWS,{}, {'level':'intro','known_concepts':['函数']})
  self.assertEqual(result['memory_queries'],0);self.assertEqual(result['memory_writes'],0)
  self.assertEqual(self.calls[0][1]['learner_profile']['known_concepts'],['函数'])
  self.assertEqual(self.calls[0][3],'subtitle_question')
 async def test_idempotent_retry_uses_saved_answer(self):
  a=await self.engine.ask(REQUEST,ROWS,{},None);b=await self.engine.ask(REQUEST,[],{},None)
  self.assertEqual(a,b);self.assertEqual(len(self.calls),1)
 async def test_id_reuse_for_different_question_rejected(self):
  await self.engine.ask(REQUEST,ROWS,{},None)
  with self.assertRaises(RequestConflict):await self.engine.ask(dict(REQUEST,question='不同问题'),ROWS,{},None)
 async def test_two_engine_instances_share_file_lock(self):
  started=asyncio.Event();release=asyncio.Event()
  async def respond(*args):started.set();await release.wait();self.calls.append(args);return dict(ANSWER)
  first=QuestionAnswerer(self.run,respond);second=QuestionAnswerer(self.run,respond)
  task=asyncio.create_task(first.ask(REQUEST,ROWS,{},None));await started.wait()
  duplicate=asyncio.create_task(second.ask(REQUEST,ROWS,{},None));await asyncio.sleep(.03);release.set()
  a,b=await asyncio.gather(task,duplicate);self.assertEqual(a,b);self.assertEqual(len(self.calls),1)
 async def test_timeout_does_not_persist_answer(self):
  async def slow(*args):await asyncio.sleep(.2)
  engine=QuestionAnswerer(self.run,slow,deadline=.01)
  with self.assertRaises(asyncio.TimeoutError):await engine.ask(REQUEST,ROWS,{},None)
  self.assertEqual(public_history(self.run),[])
 async def test_provider_balance_failure_is_explicit_and_not_saved(self):
  async def empty(*args):
   r=httpx.Response(402,request=httpx.Request('POST','https://api.deepseek.com/chat/completions'))
   r.raise_for_status()
  with self.assertRaises(ProviderUnavailable) as caught:await QuestionAnswerer(self.run,empty).ask(REQUEST,ROWS,{},None)
  self.assertEqual(caught.exception.status_code,402);self.assertIn('DeepSeek 余额不足',str(caught.exception))
  self.assertEqual(public_history(self.run),[])
 async def test_invalid_citation_does_not_persist(self):
  async def bad(*args):return dict(ANSWER,citations=[{'utterance_id':'wrong','quote':'fabricated'}])
  with self.assertRaises(InvalidAnswer):await QuestionAnswerer(self.run,bad).ask(REQUEST,ROWS,{},None)
  self.assertEqual(public_history(self.run),[])
 async def test_cancel_releases_lock(self):
  started=asyncio.Event()
  async def slow(*args):started.set();await asyncio.sleep(20)
  task=asyncio.create_task(QuestionAnswerer(self.run,slow).ask(REQUEST,ROWS,{},None));await started.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual((await self.engine.ask(REQUEST,ROWS,{},None))['status'],'answered')
 async def test_history_has_no_internal_fingerprint(self):
  await self.engine.ask(REQUEST,ROWS,{},None)
  self.assertNotIn('_fingerprint',public_history(self.run)[0])
 async def test_no_transcript_or_memory_mutation(self):
  before=json.dumps(ROWS);await self.engine.ask(REQUEST,ROWS,{},None)
  self.assertEqual(json.dumps(ROWS),before)
  self.assertEqual({p.name for p in self.run.iterdir()},{'questions.json','questions.lock'})

if __name__=='__main__':unittest.main()
