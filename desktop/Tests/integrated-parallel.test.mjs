import test from 'node:test';
import assert from 'node:assert/strict';
import {concurrencyNote} from '../../workbench/meeting/static/subtitle-ui.mjs';
test('main workbench shows actual parallel workers',()=>assert.equal(concurrencyNote({parallelism:4,active_requests:3}),'3/4 路处理中 · '));
test('old or absent API fields do not invent four workers',()=>{assert.equal(concurrencyNote({}), '');assert.equal(concurrencyNote(null),'');});
test('idle parallel pool remains visible and counts clamped',()=>{assert.equal(concurrencyNote({parallelism:4,active_requests:0}),'0/4 路处理中 · ');assert.equal(concurrencyNote({parallelism:4,active_requests:99}),'4/4 路处理中 · ');});

test('rolling window timing comes from server',()=>assert.equal(concurrencyNote({parallelism:4,active_requests:1,window_seconds:1.2}),'1.2s 滚动窗口 · 1/4 路处理中 · '));
test('invalid window duration is not displayed',()=>assert.equal(concurrencyNote({window_seconds:-1}),''));
