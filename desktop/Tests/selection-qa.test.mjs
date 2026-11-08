import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {makeQuestion,selectedQuote,answerHTML} from '../../workbench/meeting/static/selection-qa.mjs';
const base={sessionId:'a'.repeat(32),utteranceId:'u1',source:'original',quote:'梯度下降'};
test('question payload is bound to selected utterance',()=>assert.deepEqual(makeQuestion(base,' 为什么？ ','request-1'),{request_id:'request-1',utterance_id:'u1',source:'original',quote:'梯度下降',question:'为什么？'}));
test('selection is required',()=>assert.throws(()=>makeQuestion(null,'问题','request-1')));
test('blank question is rejected',()=>assert.throws(()=>makeQuestion(base,'  ','request-1')));
test('long question is rejected',()=>assert.throws(()=>makeQuestion(base,'a'.repeat(601),'request-1')));
test('long quote is rejected',()=>assert.throws(()=>makeQuestion({...base,quote:'a'.repeat(1201)},'问题','request-1')));
test('translated source is supported',()=>assert.equal(makeQuestion({...base,source:'translation'},'问题','request-1').source,'translation'));
test('non-subtitle source is rejected',()=>assert.throws(()=>makeQuestion({...base,source:'other'},'问题','request-1')));
function fixture(cross=false){const article={dataset:{utterance:'u1'}};const paragraph={dataset:{qaSource:'original'},closest:()=>article};const left={nodeType:3,parentElement:{closest:()=>paragraph}},right=cross?{nodeType:3,parentElement:{closest:()=>({})}}:left;return {selection:{anchorNode:left,focusNode:right,isCollapsed:false,rangeCount:1,toString:()=>base.quote},root:{contains:n=>n===paragraph}};}
test('DOM selection resolves actual paragraph and utterance',()=>{const {selection,root}=fixture();assert.deepEqual(selectedQuote(selection,root,base.sessionId),base);});
test('cross-paragraph selection is not silently re-anchored',()=>{const {selection,root}=fixture(true);assert.equal(selectedQuote(selection,root,base.sessionId),null);});
test('collapsed selection does not offer question',()=>{const {selection,root}=fixture();selection.isCollapsed=true;assert.equal(selectedQuote(selection,root,base.sessionId),null);});
test('answer text and references are escaped',()=>{const html=answerHTML({answer:'<img src=x onerror=alert(1)>',example:'<script>',status:'answered',citations:[{utterance_id:'" onclick="x',quote:'<svg>'}]});assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<script>'));assert.ok(html.includes('未查询历史记忆'));});
test('UI selection guard precedes transcript replacement',()=>{const s=fs.readFileSync(new URL('../../workbench/meeting/static/workbench.js',import.meta.url),'utf8');assert.ok(s.includes('function renderTranscript(){if(questionUI.holdingTranscript())return;'));assert.ok(s.includes("['concept','memory','question']"));assert.ok(s.includes('questionUI.sync(S.view)'));});

test('asking restores the visible panel from focus mode',()=>{const s=fs.readFileSync(new URL('../../workbench/meeting/static/workbench.js',import.meta.url),'utf8');assert.ok(s.includes("onOpen:()=>{S.focus=false;document.body.classList.remove('focus-mode')"));});

test('a new question clears the previous answer before awaiting the provider',()=>{const s=fs.readFileSync(new URL('../../workbench/meeting/static/selection-qa.mjs',import.meta.url),'utf8');assert.ok(s.includes("busy=true;$('qaError').textContent='';$('qaAnswer').innerHTML='';update();"));});
