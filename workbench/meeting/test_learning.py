import asyncio,copy,importlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from .learning import parse_file,LearningLibrary,SCOPE,stamp
from .coach import Coach

QUESTION='为什么 Transformer 的注意力要除以根号 d_k？我还不理解方差和缩放的关系。'
ANSWER='点积各项的方差相加，维度增加时点积尺度变大；除以根号 d_k 可以稳定尺度，避免 softmax 过度饱和。'
def fixture():return json.dumps({'title':'Transformer 学习','messages':[{'role':'user','content':QUESTION},{'role':'assistant','content':ANSWER}]},ensure_ascii=False)

class ParserTests(unittest.TestCase):
 def test_generic_preserves_questions_and_answer(self):
  groups,_=parse_file('lesson.json',fixture());self.assertEqual(groups[0]['messages'][0]['content'],QUESTION);self.assertEqual(groups[0]['messages'][1]['content'],ANSWER)
 def test_chatgpt_active_branch_only(self):
  def node(parent,text,children=[]):return {'parent':parent,'children':children,'message':{'author':{'role':'user'},'content':{'parts':[text]}}}
  data={'title':'study','current_node':'b','mapping':{'a':node(None,'question',['b','c']),'b':node('a','selected'),'c':node('a','alternate')}}
  g,_=parse_file('export.json',json.dumps(data));self.assertEqual([m['content'] for m in g[0]['messages']],['question','selected'])
 def test_ambiguous_chatgpt_branch_rejected(self):
  with self.assertRaises(ValueError):parse_file('a.json',json.dumps({'mapping':{'a':{'children':[]},'b':{'children':[]}}}))
 def test_codex_ignores_tool_system_and_internal_analysis(self):
  lines=[{'type':'response_item','payload':{'type':'message','role':'system','content':'internal'}},{'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':'hidden'}},{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':QUESTION}]}},{'type':'event_msg','payload':{'type':'user_message','message':QUESTION}},{'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final','content':[{'type':'output_text','text':ANSWER}]}}]
  g,w=parse_file('codex.jsonl','\n'.join(json.dumps(x) for x in lines));self.assertEqual(len(g[0]['messages']),2);self.assertTrue(w)
 def test_bad_jsonl_is_not_partially_imported(self):
  with self.assertRaisesRegex(ValueError,'第 2 行'):parse_file('a.jsonl','{}\nINVALID')
 def test_claude_export(self):
  g,_=parse_file('a.json',json.dumps({'name':'Claude lesson','chat_messages':[{'sender':'human','text':QUESTION,'content':[]},{'sender':'assistant','content':[{'type':'text','text':ANSWER},{'type':'thinking','text':'internal'}]}]}));self.assertEqual(len(g[0]['messages']),2);self.assertEqual(g[0]['messages'][1]['content'],ANSWER)
 def test_claude_code(self):
  g,_=parse_file('a.jsonl',json.dumps({'type':'user','uuid':'a','message':{'role':'user','content':QUESTION}}));self.assertEqual(g[0]['provider'],'claude_code')
 def test_unknown_dates_are_not_import_dates(self):
  g,_=parse_file('a.md',QUESTION);self.assertIsNone(g[0]['learning_date']);self.assertIsNone(g[0]['messages'][0]['timestamp'])
 def test_timezone_preserved(self):self.assertEqual(stamp('2020-01-01T10:00:00-05:00'),'2020-01-01T10:00:00-05:00')
 def test_subtitle_preserved_without_speaker_claim(self):
  g,_=parse_file('a.srt','1\n00:00:01,000 --> 00:00:02,000\n'+QUESTION);self.assertEqual(g[0]['provider'],'transcript');self.assertIn('-->',g[0]['messages'][0]['content'])
 def test_credentials_rejected_not_written(self):
  with self.assertRaisesRegex(ValueError,'凭证'):parse_file('a.txt','sk-proj-'+'a'*40)
 def test_file_bound(self):
  with self.assertRaises(ValueError):parse_file('a.txt','a'*(8*1024*1024+1))
 def test_unknown_format_rejected(self):
  with self.assertRaises(ValueError):parse_file('a.wav','not text audio')
 def test_repeated_questions_preserved(self):
  g,_=parse_file('a.json',json.dumps({'messages':[{'role':'user','content':QUESTION},{'role':'user','content':QUESTION}]}));self.assertEqual(len(g[0]['messages']),2)

class LibraryTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.calls=[]
  async def tc(method,path,**kwargs):
   self.calls.append((method,path,kwargs));return {'job_id':'job-1'} if method=='POST' else {'status':'succeeded'}
  self.lib=LearningLibrary(self.tmp.name,tc)
 async def asyncTearDown(self):await self.lib.close();self.tmp.cleanup()
 async def import_fixture(self,state='studied'):
  d=self.lib.preview('transformer.json',fixture());key=d['conversations'][0]['id'];r=await self.lib.commit(d['id'],d['digest'],[key],state,['Transformer'],True);await self.lib.tasks[key];return key,r
 async def test_preview_is_zero_native_calls(self):
  d=self.lib.preview('a.json',fixture());self.assertEqual(self.calls,[]);self.assertFalse(self.lib.available());self.assertEqual(len(d['conversations']),1)
 async def test_confirmation_required(self):
  d=self.lib.preview('a.json',fixture())
  with self.assertRaises(ValueError):await self.lib.commit(d['id'],d['digest'],[d['conversations'][0]['id']],'studied',[],False)
  self.assertEqual(self.calls,[])
 async def test_preview_digest_bound(self):
  d=self.lib.preview('a.json',fixture())
  with self.assertRaises(ValueError):await self.lib.commit(d['id'],'0'*64,[d['conversations'][0]['id']],'studied',[],True)
 async def test_success_after_job_not_receipt(self):
  key,_=await self.import_fixture();self.assertEqual(self.lib.get(key)['status'],'ready');self.assertTrue(self.lib.available());self.assertEqual([c[0] for c in self.calls],['POST','GET'])
 async def test_pending_receipt_not_advertised_ready(self):
  gate=asyncio.Event()
  async def tc(method,path,**kw):
   if method=='POST':return {'job_id':'j'}
   await gate.wait();return {'status':'succeeded'}
  self.lib.transport=tc;d=self.lib.preview('a.json',fixture());key=d['conversations'][0]['id']
  await self.lib.commit(d['id'],d['digest'],[key],'discussed',[],True);await asyncio.sleep(0)
  self.assertEqual(self.lib.get(key)['status'],'indexing');self.assertFalse(self.lib.available());gate.set();await self.lib.tasks[key];self.assertTrue(self.lib.available())
 async def test_resume_checks_saved_job_without_duplicate_upload(self):
  key,_=await self.import_fixture();row=self.lib.get(key);row['status']='indexing'
  from .pipeline import atomic
  atomic(self.lib.path('import',key),row)
  replacement=LearningLibrary(self.tmp.name,AsyncMock(return_value={'status':'succeeded'}));replacement.resume();await replacement.tasks[key]
  self.assertEqual(replacement.transport.call_args.args[0],'GET');self.assertTrue(replacement.available());await replacement.close()
 async def test_scope_fixed_and_roles_preserved(self):
  await self.import_fixture();post=self.calls[0];self.assertEqual(post[1],'/v1/scopes/'+SCOPE+'/ingest');self.assertEqual([m['role'] for m in post[2]['json']['messages']],['user','assistant']);self.assertFalse(post[2]['json']['messages'][0]['metadata']['timestamp_known'])
 async def test_duplicate_import_no_second_ingest(self):
  await self.import_fixture();await self.import_fixture();self.assertEqual(len([c for c in self.calls if c[0]=='POST']),1)
 async def test_job_failure_not_ready(self):
  self.lib.transport=AsyncMock(side_effect=[{'job_id':'j'},{'status':'failed'}]);key,_=await self.import_fixture();self.assertEqual(self.lib.get(key)['status'],'error');self.assertFalse(self.lib.available())
 async def test_transport_error_not_ready(self):
  self.lib.transport=AsyncMock(side_effect=ConnectionError('synthetic'));key,_=await self.import_fixture();self.assertFalse(self.lib.available());self.assertEqual(self.lib.get(key)['status'],'error')
 async def test_cannot_claim_mastered(self):
  d=self.lib.preview('a.json',fixture())
  with self.assertRaises(ValueError):await self.lib.commit(d['id'],d['digest'],[d['conversations'][0]['id']],'mastered',[],True)
 async def test_retrieval_lineage_requires_matching_native_text(self):
  key,_=await self.import_fixture();native={'evidence':{'evidence_windows':[{'text':ANSWER,'session_id':'learning-'+key}]}}
  rows=self.lib.enrich(native);self.assertEqual(rows[0]['learning_state'],'studied');self.assertEqual(rows[0]['discussion_context'][0]['content'],QUESTION);self.assertEqual(rows[0]['import_id'],key)
 async def test_unrelated_memory_is_not_learning_evidence(self):
  await self.import_fixture();self.assertEqual(self.lib.enrich({'evidence':{'evidence_windows':[{'text':'完全无关的另一段录音，不是学习资料'}]}}),[])
 async def test_wrong_scope_session_not_promoted(self):
  await self.import_fixture();self.assertEqual(self.lib.enrich({'evidence':{'evidence_windows':[{'text':ANSWER,'session_id':'different-session'}]}}),[])
 async def test_simulated_import_to_transformer_bridge(self):
  key,_=await self.import_fixture();rows=self.lib.enrich({'evidence':{'evidence_windows':[{'text':ANSWER,'session_id':'learning-'+key}]}})
  plan={'action':'retrieve','topic_key':'attention','queries':['注意力缩放与方差的关系']}
  card={'action':'show','cards':[{'kind':'knowledge_bridge','title':'联系之前的 Transformer 学习','body':'你之前问过为什么除以根号 d_k；可以把这里的缩放联系到点积方差随维度增加。','follow_up':'如果不缩放，softmax 会发生什么？','utterance_ids':['u1'],'citations':[{'source_id':rows[0]['source_id'],'quote':'除以根号 d_k 可以稳定尺度'}]}]}
  coach=Coach(AsyncMock(side_effect=[plan,card]),AsyncMock(return_value=rows));result=await coach.run([{'id':'u1','text':'今天讨论注意力 logits 的尺度控制'}]);self.assertEqual(result['action'],'show');self.assertEqual(result['cards'][0]['citations'][0]['import_id'],key)
 async def test_invalid_path_rejected(self):
  with self.assertRaises(ValueError):self.lib.get('../anything')

class LearningAPITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);(root/'secrets').mkdir()
  for name in ['meeting_token','openai_api_key','volc_api_key','tmcra_api_key']:(root/'secrets'/name).write_text('TEST')
  self.env=patch.dict(os.environ,{'MEETING_ROOT':str(root),'TMCRA_BASE_URL':'http://127.0.0.1:22109'});self.env.start();sys.modules.pop('meeting.service',None);self.s=importlib.import_module('meeting.service');self.s.LIBRARY.transport=AsyncMock(side_effect=[{'job_id':'j'},{'status':'succeeded'}]);self.client=TestClient(self.s.app);self.client.__enter__();self.headers={'Authorization':'Bearer TEST'}
 def tearDown(self):self.client.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
 def test_unauthenticated_preview_rejected(self):self.assertEqual(self.client.post('/v1/learning/preview',json={'filename':'x.md','content':QUESTION}).status_code,401)
 def test_preview_returns_content_without_writing(self):
  r=self.client.post('/v1/learning/preview',headers=self.headers,json={'filename':'x.json','content':fixture()});self.assertEqual(r.status_code,200);self.assertFalse(self.s.LIBRARY.transport.called)
 def test_ui_has_explicit_consent_entry(self):
  html=self.client.get('/').text;self.assertIn('learningConsent',html);self.assertIn('确认写入个人学习记忆',html);self.assertIn('learningNav',html)
 def test_missing_confirm_is_validation_error(self):
  r=self.client.post('/v1/learning/preview',headers=self.headers,json={'filename':'x.json','content':fixture()}).json()
  response=self.client.post('/v1/learning/previews/'+r['id']+'/commit',headers=self.headers,json={'digest':r['digest'],'selected_ids':[r['conversations'][0]['id']]});self.assertEqual(response.status_code,422)
 def test_no_import_keeps_no_recall_branch(self):
  h=self.client.get('/healthz').json();self.assertFalse(h['memory_recall_available']);self.assertFalse(h['tmcra_write']);self.assertTrue(h['explicit_learning_import'])

if __name__=='__main__':unittest.main()
