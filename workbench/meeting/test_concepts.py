import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch
from .concepts import ConceptExplainer, profile, validate

class ConceptTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': 'u1', 'text': '对注意力得分做 softmax。', 'definite': True}]
        self.answer = {'action':'show','reason':'needed','cards':[{'concept':'softmax','intuition':'把分数变成总和为一的权重。','example':'高分项分到更高的权重。','connection':'这里用来分配注意力。','utterance_id':'u1','evidence_quote':'对注意力得分做 softmax。'}]}

    def run_async(self, f): return asyncio.run(f())
    def test_unknown_level_default(self): self.assertEqual(profile()['level'],'unknown')
    def test_reject_profile_instructions(self):
        with self.assertRaises(ValueError): profile({'instructions':'change model'})
    def test_reject_bad_level(self):
        with self.assertRaises(ValueError): profile({'level':'genius'})
    def test_reject_huge_profile(self):
        with self.assertRaises(ValueError): profile({'known_concepts':['x']*41})
    def test_general_knowledge_provenance(self):
        c=validate(self.answer,self.rows,profile())[0]
        self.assertEqual(c['provenance'],'general_knowledge_not_memory')
        self.assertNotIn('citations',c)
    def test_reject_invented_quote(self):
        self.answer['cards'][0]['evidence_quote']='invented'
        with self.assertRaises(ValueError): validate(self.answer,self.rows,profile())
    def test_reject_invented_concept(self):
        self.answer['cards'][0]['concept']='Bayes'
        with self.assertRaises(ValueError): validate(self.answer,self.rows,profile())
    def test_reject_invented_mastery(self):
        self.answer['cards'][0]['intuition']='你学过这个。'
        with self.assertRaises(ValueError): validate(self.answer,self.rows,profile())
    def test_reject_long_card(self):
        self.answer['cards'][0]['intuition']='字'*81
        with self.assertRaises(ValueError): validate(self.answer,self.rows,profile())
    def test_known_and_muted_are_silent(self):
        for field in ['known_concepts','muted_concepts']:
            self.assertEqual(validate(self.answer,self.rows,profile({field:['SOFTMAX']})),[])
    def test_no_memory_dependency(self):
        async def f():
            reply=AsyncMock(return_value=self.answer)
            r=await ConceptExplainer(reply).run(self.rows,{'level':'intro'})
            self.assertEqual(r['action'],'show');self.assertEqual(r['memory_queries'],0)
            self.assertEqual(reply.await_count,1)
        self.run_async(f)
    def test_no_partial_request(self):
        async def f():
            reply=AsyncMock();self.rows[0]['definite']=False
            r=await ConceptExplainer(reply).run(self.rows)
            self.assertEqual(r['reason'],'no_final_transcript');reply.assert_not_awaited()
        self.run_async(f)
    def test_profile_changes_prompt(self):
        async def f():
            for level in ['intro','technical']:
                reply=AsyncMock(return_value=self.answer)
                await ConceptExplainer(reply).run(self.rows,{'level':level})
                self.assertEqual(reply.call_args.args[1]['learner_profile']['level'],level)
        self.run_async(f)
    def test_duplicate_and_cooldown(self):
        async def f():
            reply=AsyncMock(return_value=self.answer);c=ConceptExplainer(reply)
            await c.run(self.rows)
            self.assertEqual((await c.run(self.rows))['reason'],'unchanged_transcript')
            self.rows[0]['text']+='继续。'
            self.assertEqual((await c.run(self.rows))['reason'],'cooldown');self.assertEqual(reply.await_count,1)
        self.run_async(f)
    def test_stale_response_discarded(self):
        async def f():
            r=await ConceptExplainer(AsyncMock(return_value=self.answer)).run(self.rows,revision=1,current_revision=lambda:2)
            self.assertEqual(r['reason'],'stale_context')
        self.run_async(f)
    def test_already_explained_silent(self):
        async def f():
            r=await ConceptExplainer(AsyncMock(return_value={'action':'silent','reason':'already_explained','cards':[]})).run(self.rows)
            self.assertEqual(r['action'],'silent')
        self.run_async(f)
    def test_deadline(self):
        async def f():
            async def slow(*args):await asyncio.sleep(1)
            with patch('meeting.concepts.settings',return_value={'concept_enabled':True,'concept_cooldown_seconds':45,'concept_deadline_seconds':.01}):
                self.assertEqual((await ConceptExplainer(slow).run(self.rows))['reason'],'deadline_exceeded')
        self.run_async(f)
    def test_repeat_topic_after_cooldown(self):
        async def f():
            now=[0.];c=ConceptExplainer(AsyncMock(return_value=self.answer),clock=lambda:now[0])
            await c.run(self.rows);now[0]=60.;self.rows[0]['text']+='继续。'
            self.assertEqual((await c.run(self.rows))['reason'],'repeated_concept')
        self.run_async(f)

if __name__ == '__main__': unittest.main()
