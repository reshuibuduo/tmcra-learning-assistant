import asyncio,importlib,json,unittest,tempfile
from pathlib import Path
from unittest.mock import patch,MagicMock,AsyncMock
from .pipeline import validate_facts
class ContractTests(unittest.TestCase):
 def test_evidence_and_replacement(self):
  u=[{'id':'a','text':'预算三百。'},{'id':'b','text':'预算改为五百。'}]
  base={'kind':'decision','summary':'预算','owner':None,'due':None,'amount':None}
  facts=[{**base,'id':'f1','status':'confirmed','replaces':[],'evidence':[{'utterance_id':'a','quote':'三百'}]},{**base,'id':'f2','status':'confirmed','replaces':['f1'],'evidence':[{'utterance_id':'b','quote':'五百'}]},{**base,'id':'bad','status':'confirmed','replaces':[],'evidence':[{'utterance_id':'b','quote':'一千'}]}]
  r=validate_facts({'facts':facts},u)
  self.assertEqual([f['status'] for f in r['facts']],['superseded','confirmed']);self.assertEqual(r['rejected_unverifiable_fact_ids'],['bad'])
 def test_openai_payload_no_deepseek_fields(self):
  # Native Writer now comes unchanged from reshuibuduo/tmcra and uses local Qwen.
  # Test the meeting service's actual OpenAI caller, not the obsolete native patch.
  from .pipeline import Memory
  response=MagicMock();response.status_code=200;response.headers={};response.json.return_value={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':{'input_tokens':10,'output_tokens':2},'id':'test'}
  client=AsyncMock();client.post.return_value=response
  with tempfile.TemporaryDirectory() as td,patch('meeting.pipeline.httpx.AsyncClient') as factory:
   factory.return_value.__aenter__.return_value=client
   out=asyncio.run(Memory(Path(td),'TEST').response('JSON',{}, {'type':'object'},'contract'))
   usage=json.loads((Path(td)/'api-usage.jsonl').read_text())
  body=client.post.call_args.kwargs['json'];self.assertNotIn('thinking',body);self.assertNotIn('enable_thinking',body);self.assertFalse(body['store']);self.assertEqual(out,{'ok':True});self.assertEqual(usage['usage']['input_tokens'],10)
 def test_incomplete_openai_response_is_rejected(self):
  from .pipeline import Memory
  response=MagicMock();response.status_code=200;response.headers={};response.json.return_value={'status':'incomplete','output':[]}
  client=AsyncMock();client.post.return_value=response
  with tempfile.TemporaryDirectory() as td,patch('meeting.pipeline.httpx.AsyncClient') as factory:
   factory.return_value.__aenter__.return_value=client
   with self.assertRaisesRegex(RuntimeError,'Incomplete Responses output'):
    asyncio.run(Memory(Path(td),'TEST').response('JSON',{}, {'type':'object'},'contract'))
class GroundingTests(unittest.TestCase):
 def test_unknown_answer_discards_unverified_freeform_claim(self):
  from .grounding import validate
  a=validate([{'question':'谁批准今天上线？','known':False,'answer':'实际上今天已经上线。','source_ids':['S1']}],[{'question':'谁批准今天上线？','query_id':'id','source_windows':[{'source_id':'S1','text':'Windows版暂缓。'}]}])[0]
  self.assertNotIn('实际上今天已经上线',a['answer']);self.assertIn('证据不足',a['answer']);self.assertIn('「Windows版暂缓。」',a['answer'])
 def test_empty_retrieval_cannot_claim_known(self):
  from .grounding import validate
  with self.assertRaises(ValueError):validate([{'question':'q','known':True,'source_ids':[]}],[{'question':'q','query_id':'id','source_windows':[]}])
 def test_invented_source_id_rejected(self):
  from .grounding import validate
  with self.assertRaises(ValueError):validate([{'question':'q','known':True,'source_ids':['S99']}],[{'question':'q','query_id':'id','source_windows':[{'source_id':'S1','text':'今天开会'}]}])
 def test_retrieval_payload_ignores_derived_summary(self):
  from .grounding import payload
  r=payload([{'question':'q','result':{'query_id':'id','evidence':{'evidence_windows':[{'text':'原文'}]},'prompt_evidence':{'content':'WRONG_DERIVED_SUMMARY'}}}])[0]
  self.assertEqual(r['evidence'],'原文');self.assertNotIn('WRONG_DERIVED_SUMMARY',str(r))
 def test_verbatim_quotes_are_server_generated(self):
  from .grounding import validate
  a=validate([{'question':'q','known':True,'source_ids':['S1']}],[{'question':'q','query_id':'id','source_windows':[{'source_id':'S1','text':'三百改五百。'}]}])[0]
  self.assertEqual(a['evidence_quotes'],['三百改五百。']);self.assertEqual(a['fact_ids'],['id'])
if __name__=='__main__':unittest.main()
