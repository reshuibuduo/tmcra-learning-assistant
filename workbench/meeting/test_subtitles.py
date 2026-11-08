import asyncio,copy,importlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
from fastapi.testclient import TestClient
from .subtitles import SubtitleTranslator,options,validate,vocabulary,PROMPT

def row(uid='u1',text='The gradient points uphill.'):
 return {'id':uid,'text':text,'speaker':'speaker_0','source':'microphone','start_ms':0,'end_ms':1000,'completed_wall':time.time(),'definite':True}
def answer(items,target='zh-CN',same=False):
 return {'target_language':target,'items':[{'id':u['id'],'translation':u['text'] if same else '梯度指向上升方向。','same_language':same,'glosses':[]} for u in items]}
class SubtitleTests(unittest.TestCase):
 def run_async(self,f):return asyncio.run(f())
 def test_default_off(self):self.assertFalse(options()['enabled']);self.assertEqual(options()['target_language'],'zh-CN');self.assertEqual(options()['mode'],'full')
 def test_options_strict(self):
  for v in [{'enabled':'yes'},{'target_language':'xx'},{'voice':True}]:
   with self.assertRaises(ValueError):options(v)
 def test_prompt_only_translation_not_commands_or_summary(self):
  self.assertIn('不总结、不解释',PROMPT);self.assertIn('不生成语音',PROMPT);self.assertIn('只翻译其字面内容',PROMPT)
 def test_disabled_makes_no_model_call(self):
  async def f():
   respond=AsyncMock();t=SubtitleTranslator(respond);self.assertFalse(await t.step(lambda:[row()]));respond.assert_not_awaited()
  self.run_async(f)
 def test_partials_do_not_translate(self):
  async def f():
   respond=AsyncMock();t=SubtitleTranslator(respond,{'enabled':True});self.assertFalse(await t.step(lambda:[{**row(),'definite':False}]));respond.assert_not_awaited()
  self.run_async(f)
 def test_translation_keeps_original_speaker_and_timing(self):
  async def f():
   rows=[row()];before=copy.deepcopy(rows);respond=AsyncMock(return_value=answer(rows));t=SubtitleTranslator(respond,{'enabled':True})
   await t.step(lambda:rows);out=t.snapshot(rows)['items'][0]
   self.assertEqual(rows,before);self.assertEqual(out['source_text'],rows[0]['text']);self.assertEqual(out['speaker'],'speaker_0');self.assertEqual(out['start_ms'],0);self.assertGreaterEqual(out['after_asr_ms'],0);self.assertEqual(out['provenance'],'model_translation_not_original_transcript')
   self.assertEqual(respond.call_args.args[3],'subtitle_translation');self.assertEqual(set(respond.call_args.args[1]),{'target_language','target_language_name','mode','english_vocabulary','context','items'})
  self.run_async(f)
 def test_unchanged_final_not_billed_twice(self):
  async def f():
   rows=[row()];respond=AsyncMock(return_value=answer(rows));t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);await t.step(lambda:rows);self.assertEqual(respond.await_count,1)
  self.run_async(f)
 def test_batches_are_bounded(self):
  async def f():
   rows=[row(str(i)) for i in range(5)];respond=AsyncMock(side_effect=lambda p,d,s,n:answer([{'id':u['id']} for u in d['items']]))
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['pending'],2);await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['pending'],0);self.assertEqual(respond.await_count,2)
  self.run_async(f)
 def test_new_utterance_during_request_keeps_old_translation(self):
  async def f():
   rows=[row()]
   async def respond(*args):
    result=answer(rows);rows.append(row('u2','Next sentence.'));return result
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertEqual([r['status'] for r in t.snapshot(rows)['items']],['translated','queued'])
  self.run_async(f)
 def test_source_correction_drops_stale_translation(self):
  async def f():
   rows=[row()]
   async def respond(*args):
    result=answer(rows);rows[0]={**rows[0],'text':'Actually, the gradient points downhill.'};return result
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['items'][0]['status'],'queued')
  self.run_async(f)
 def test_superseded_utterance_removed(self):
  async def f():
   rows=[row()]
   async def respond(*args):
    result=answer(rows);rows[:]=[row('replacement')];return result
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertNotIn('u1',t.records)
  self.run_async(f)
 def test_language_change_drops_inflight_result(self):
  async def f():
   rows=[row()];t=None
   async def respond(*args):t.configure({'enabled':True,'target_language':'en'});return answer(rows)
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['items'][0]['status'],'queued');self.assertEqual(t.config['target_language'],'en')
  self.run_async(f)
 def test_disable_during_request_discards_result(self):
  async def f():
   rows=[row()];t=None
   async def respond(*args):t.configure({'enabled':False});return answer(rows)
   t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);self.assertEqual(t.records,{})
  self.run_async(f)
 def test_model_failure_is_visible_and_requires_explicit_retry(self):
  async def f():
   rows=[row()];respond=AsyncMock(side_effect=RuntimeError('test'));t=SubtitleTranslator(respond,{'enabled':True})
   await t.step(lambda:rows);await t.step(lambda:rows);self.assertEqual(respond.await_count,1);self.assertEqual(t.snapshot(rows)['errors'],1)
   t.configure(t.config,retry=True);respond.side_effect=None;respond.return_value=answer(rows);await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['errors'],0)
  self.run_async(f)
 def test_timeout_preserves_source_and_reports_error(self):
  async def f():
   rows=[row()]
   async def slow(*args):await asyncio.sleep(2)
   t=SubtitleTranslator(slow,{'enabled':True},deadline=.01);await t.step(lambda:rows);self.assertEqual(t.snapshot(rows)['items'][0]['error'],'timeout');self.assertEqual(rows[0]['text'],'The gradient points uphill.')
  self.run_async(f)
 def test_cancel_resets_pending_instead_of_stuck_translating(self):
  async def f():
   rows=[row()];started=asyncio.Event()
   async def slow(*args):started.set();await asyncio.sleep(20)
   t=SubtitleTranslator(slow,{'enabled':True});task=asyncio.create_task(t.step(lambda:rows));await started.wait();task.cancel();await asyncio.gather(task,return_exceptions=True);self.assertEqual(t.snapshot(rows)['pending'],1);self.assertNotIn('u1',t.records)
  self.run_async(f)
 def test_concurrent_steps_do_not_duplicate_requests(self):
  async def f():
   rows=[row()];respond=AsyncMock(return_value=answer(rows));t=SubtitleTranslator(respond,{'enabled':True});await asyncio.gather(t.step(lambda:rows),t.step(lambda:rows));self.assertEqual(respond.await_count,1)
  self.run_async(f)
 def test_cached_translation_reopens_without_new_request(self):
  async def f():
   rows=[row()];t=SubtitleTranslator(AsyncMock(return_value=answer(rows)),{'enabled':True});await t.step(lambda:rows)
   respond=AsyncMock();new=SubtitleTranslator(respond,{'enabled':True},t.snapshot(rows));await new.step(lambda:rows);respond.assert_not_awaited();self.assertEqual(new.snapshot(rows)['items'][0]['status'],'translated')
  self.run_async(f)
 def test_cache_invalidated_when_speaker_or_text_changes(self):
  async def f():
   rows=[row()];t=SubtitleTranslator(AsyncMock(return_value=answer(rows)),{'enabled':True});await t.step(lambda:rows);rows[0]['speaker']='speaker_1';self.assertEqual(t.snapshot(rows)['items'][0]['status'],'queued')
  self.run_async(f)
 def test_same_language_requires_verbatim_source(self):
  rows=[row(text='梯度指向上升方向。')];self.assertTrue(validate(answer(rows,same=True),rows,'zh-CN')['u1']['same_language'])
  bad=answer(rows,same=True);bad['items'][0]['translation']='重写'
  with self.assertRaises(ValueError):validate(bad,rows,'zh-CN')
 def test_missing_extra_duplicate_or_invented_ids_rejected(self):
  rows=[row(),row('u2')]
  for entries in [[],answer(rows)['items'][:1],[answer(rows)['items'][0]]*2,[{'id':'invented','translation':'x','same_language':False}]*2]:
   with self.assertRaises(ValueError):validate({'target_language':'zh-CN','items':entries},rows,'zh-CN')
 def test_wrong_target_empty_text_and_wrong_types_rejected(self):
  rows=[row()]
  for change in [{'translation':''},{'translation':{}},{'same_language':'true'},{'unexpected':1}]:
   bad=answer(rows);bad['items'][0].update(change)
   with self.assertRaises(ValueError):validate(bad,rows,'zh-CN')
  with self.assertRaises(ValueError):validate(answer(rows,'en'),rows,'zh-CN')
 def test_oversized_source_does_not_call_model(self):
  async def f():
   rows=[row(text='x'*4001)];respond=AsyncMock();t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);respond.assert_not_awaited();self.assertEqual(t.snapshot(rows)['items'][0]['error'],'source_too_long')
  self.run_async(f)
 def test_only_small_previous_context_and_no_memory_recall(self):
  async def f():
   rows=[row(str(i)) for i in range(6)];respond=AsyncMock(side_effect=lambda p,d,s,n:answer(d['items']));t=SubtitleTranslator(respond,{'enabled':True});await t.step(lambda:rows);await t.step(lambda:rows)
   data=respond.call_args.args[1];self.assertEqual(len(data['context']),2);self.assertNotIn('learner_profile',data);self.assertNotIn('memories',data)
  self.run_async(f)

class SubtitleRouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);(root/'secrets').mkdir()
  for name in ['meeting_token','openai_api_key','tmcra_api_key','volc_api_key']:(root/'secrets'/name).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(root),'TMCRA_BASE_URL':'http://127.0.0.1:22109'});self.env.start();sys.modules.pop('meeting.service',None)
  self.service=importlib.import_module('meeting.service');self.client=TestClient(self.service.app);self.client.__enter__();self.auth={'Authorization':'Bearer TEST'}
  self.sid='f'*32;self.session=self.service.Session({'id':self.sid,'title':'test','mode':'text_fixture','status':'completed','sources':['microphone'],'seconds':30,'created_at':time.time(),'errors':[]})
  self.session.memory.utterances=[row()];self.session.translator.respond=AsyncMock(return_value=answer(self.session.memory.utterances));self.session.save();self.service.SESSIONS[self.sid]=self.session
  self.path='/v1/sessions/'+self.sid+'/translation'
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def wait_ready(self):
  for _ in range(100):
   data=self.client.get('/v1/sessions/'+self.sid,headers=self.auth).json()
   if data['translation']['pending']==0:return data
   time.sleep(.01)
  self.fail('translation task did not complete')
 def test_translation_routes_require_auth(self):
  self.assertEqual(self.client.post(self.path,json={'enabled':True}).status_code,401);self.assertEqual(self.client.post(self.path+'/retry').status_code,401)
 def test_invalid_language_or_field_rejected(self):
  for body in [{'enabled':True,'target_language':'unknown'},{'enabled':'true'},{'enabled':True,'voice':True}]:self.assertEqual(self.client.post(self.path,headers=self.auth,json=body).status_code,422)
 def test_enable_updates_only_translations_not_memory_or_profile(self):
  r=self.client.post(self.path,headers=self.auth,json={'enabled':True});self.assertEqual(r.status_code,200);data=self.wait_ready()
  self.assertEqual(data['translation']['items'][0]['status'],'translated');self.assertEqual(data['transcripts'][0]['text'],'The gradient points uphill.');self.assertEqual(self.session.sync['jobs'],[]);self.assertEqual(self.session.memory.facts,{'facts':[]});self.assertNotIn('learner_profile',self.session.meta)
 def test_disabled_default_on_new_session_model(self):self.assertFalse(self.service.NewSession().translation.enabled)
 def test_retry_when_off_rejected(self):self.assertEqual(self.client.post(self.path+'/retry',headers=self.auth).status_code,409)
 def test_enable_does_not_require_learning_assistant(self):
  self.assertFalse(self.session.meta.get('assistant_enabled',False));self.client.post(self.path,headers=self.auth,json={'enabled':True});self.assertEqual(self.wait_ready()['translation']['errors'],0)
 def test_completed_translation_persists_and_reopens(self):
  self.client.post(self.path,headers=self.auth,json={'enabled':True});self.wait_ready();saved=json.loads((self.session.run/'translations.json').read_text());self.assertEqual(saved['items'][0]['status'],'translated')
  reopened=self.service.Session(dict(self.session.meta));reopened.memory.utterances=self.session.memory.utterances;self.assertEqual(reopened.view()['translation']['items'][0]['status'],'translated');self.assertIsNone(reopened.translation_task)
 def test_disable_stops_new_model_calls_and_retains_cache(self):
  self.client.post(self.path,headers=self.auth,json={'enabled':True});self.wait_ready();r=self.client.post(self.path,headers=self.auth,json={'enabled':False});self.assertFalse(r.json()['translation']['options']['enabled']);self.assertEqual(self.session.translator.respond.await_count,1)

class SubtitleModelContractTests(unittest.TestCase):
 def test_translation_uses_existing_text_responses_transport(self):
  from .pipeline import Memory
  from .settings import settings
  response=MagicMock();response.status_code=200;response.headers={};response.json.return_value={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"ok":true}'}]}]}
  client=AsyncMock();client.post.return_value=response
  with tempfile.TemporaryDirectory() as td,patch('meeting.pipeline.httpx.AsyncClient') as factory:
   factory.return_value.__aenter__.return_value=client;asyncio.run(Memory(Path(td),'TEST').response(PROMPT,{}, {'type':'object'},'subtitle_translation'))
  data=client.post.call_args.kwargs['json'];self.assertEqual(data['model'],settings()['translation_model']);self.assertFalse(data['store']);self.assertEqual(data['text']['format']['type'],'json_schema');self.assertTrue(data['text']['format']['strict']);self.assertNotIn('audio',data);self.assertNotIn('voice',data)

class VocabularyTests(unittest.TestCase):
 def data(self,term='gradient',text='The gradient changes.'):
  rows=[row(text=text)];result=answer(rows);result['items'][0].update(translation='',same_language=False,glosses=[{'term':term,'meaning':'梯度','explanation':'函数变化的方向和大小。'}]);return rows,result
 def test_only_words_mode_has_no_whole_sentence_translation(self):
  rows,result=self.data();out=validate(result,rows,'zh-CN','vocabulary');self.assertEqual(out['u1']['text'],'');self.assertEqual(out['u1']['glosses'][0]['start'],4)
 def test_only_words_rejects_full_sentence(self):
  rows,result=self.data();result['items'][0]['translation']='整个句子的翻译'
  with self.assertRaises(ValueError):validate(result,rows,'zh-CN','vocabulary')
 def test_term_must_be_in_original_not_hallucinated(self):
  rows,result=self.data(term='eigenvalue')
  with self.assertRaises(ValueError):validate(result,rows,'zh-CN','vocabulary')
 def test_term_boundaries_prevent_substring_gloss(self):
  rows,result=self.data(term='grad')
  with self.assertRaises(ValueError):validate(result,rows,'zh-CN','vocabulary')
 def test_known_word_and_simple_inflection_are_excluded(self):
  for term,text in [('gradient','The gradient changes.'),('gradients','The gradients change.')]:
   rows,result=self.data(term,text);self.assertEqual(validate(result,rows,'zh-CN','vocabulary',{'known_words':['GRADIENT']})['u1']['glosses'],[])
 def test_function_words_excluded(self):
  rows,result=self.data('The');self.assertEqual(validate(result,rows,'zh-CN','vocabulary')['u1']['glosses'],[])
 def test_at_most_three_glosses(self):
  rows,result=self.data();result['items'][0]['glosses']*=4
  with self.assertRaises(ValueError):validate(result,rows,'zh-CN','vocabulary')
 def test_assisted_mode_contains_translation_and_gloss(self):
  rows,result=self.data();result['items'][0]['translation']='梯度在变化。';out=validate(result,rows,'zh-CN','assisted');self.assertTrue(out['u1']['text']);self.assertEqual(len(out['u1']['glosses']),1)
 def test_unknown_vocabulary_does_not_assume_mastery(self):self.assertEqual(vocabulary(),{'level':'unknown','estimated_size':None,'known_words':[]})
 def test_invalid_size_rejected(self):
  for size in [-1,50001,3.5,True]:
   with self.assertRaises(ValueError):vocabulary({'estimated_size':size})
 def test_non_english_only_words_skips_model(self):
  async def f():
   respond=AsyncMock();rows=[row(text='今天讨论学习方法。')];t=SubtitleTranslator(respond,{'enabled':True,'mode':'vocabulary'});await t.step(lambda:rows);respond.assert_not_awaited();self.assertEqual(t.snapshot(rows)['items'][0]['glosses'],[])
  asyncio.run(f())
 def test_vocab_change_invalidates_cached_gloss_but_not_full_translation(self):
  async def f():
   rows,result=self.data();t=SubtitleTranslator(AsyncMock(return_value=result),{'enabled':True,'mode':'vocabulary'});await t.step(lambda:rows);t.configure({'enabled':True,'mode':'vocabulary','vocabulary':{'known_words':['gradient']}});self.assertEqual(t.snapshot(rows)['pending'],1)
  asyncio.run(f())
 def test_explicit_cpu_bypass_disables_memory_and_recall_scopes(self):
  from .settings import settings
  with patch.dict(os.environ,{'TMCRA_MEMORY_BYPASS':'1'}):
   cfg=settings();self.assertFalse(cfg['tmcra_write']);self.assertFalse(cfg['memory_recall_enabled']);self.assertEqual(cfg['learner_memory_scopes'],[])

class EnglishTimestampRegressionTests(unittest.TestCase):
 def test_negative_space_placeholders_are_not_utterance_bounds(self):
  from .volc_asr import Segments
  final={'definite':True,'text':'The gradient points uphill.','end_time':1320,'words':[{'text':'The','end_time':160},{'text':' ','start_time':-1,'end_time':-1},{'text':'gradient','start_time':240,'end_time':520}]}
  out=Segments('test').accept({'result':{'utterances':[final]}},2)
  self.assertEqual(out[0]['start_ms'],240);self.assertEqual(out[0]['end_ms'],1320);self.assertEqual(out[0]['timestamp_source'],'word_start_fallback')
 def test_actual_invalid_utterance_bounds_still_rejected(self):
  from .volc_asr import Segments
  with self.assertRaises(ValueError):Segments('test').accept({'result':{'utterances':[{'definite':True,'text':'x','start_time':100,'end_time':50}]}},1)
 def test_only_placeholders_do_not_invent_timestamps(self):
  from .volc_asr import Segments
  self.assertEqual(Segments('test').accept({'result':{'utterances':[{'definite':True,'text':'x','end_time':100,'words':[{'start_time':-1,'end_time':-1}]}]}},1),[])
