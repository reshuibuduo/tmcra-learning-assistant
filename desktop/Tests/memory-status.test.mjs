import test from 'node:test';
import assert from 'node:assert/strict';
import {memoryBadge,memoryNote} from '../../workbench/meeting/static/memory-status.mjs';
test('memory-enabled service does not claim that automatic writes are disabled',()=>{
 const h={tmcra_write:true,memory_recall_available:true};
 assert.equal(memoryBadge(h),'学习关联已启用');assert.match(memoryNote(h),/转写会写入独立记忆库/);assert.doesNotMatch(memoryNote(h),/不自动写入/);
});
test('empty learning library still declares enabled meeting writes',()=>{
 const h={tmcra_write:true,memory_recall_available:false};
 assert.equal(memoryBadge(h),'记忆写入已启用');assert.match(memoryNote(h),/导入学习记录后/);
});
test('explicit-import-only mode retains its no-automatic-write label',()=>{
 assert.match(memoryNote({tmcra_write:false,memory_recall_available:true}),/课堂不自动写入记忆/);
});
test('bypass and missing health do not invent active memory',()=>{
 for(const h of [undefined,{tmcra_write:false,memory_recall_available:false}]){
  assert.equal(memoryBadge(h),'无召回模式');assert.match(memoryNote(h),/概念解释独立运行/);
 }
});
