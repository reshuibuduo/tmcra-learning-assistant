"""Provisional text streams immediately; only a verified final answer is saved."""
import asyncio
import fcntl
import hashlib
import json
import time
import httpx
from fastapi.responses import StreamingResponse
from .pipeline import atomic
from .concepts import profile
from .stream_json import partial_fields
from .selection_qa import (QuestionAnswerer, PROMPT, SCHEMA, history, question_context,
    validate_answer, StaleSelection, RequestConflict, QuestionBusy, InvalidAnswer, ProviderUnavailable)

class StreamingQuestionAnswerer(QuestionAnswerer):
    def __init__(self, run, respond, stream_respond, deadline=30):
        super().__init__(run,respond,deadline); self.stream_respond = stream_respond

    async def stream(self, request, rows, translations, learner):
        rid = request['request_id']
        fingerprint = hashlib.sha256(json.dumps({k:v for k,v in request.items() if k!='request_id'},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.run.mkdir(parents=True,exist_ok=True)
        with (self.run/'questions.lock').open('a') as lock:
            until = time.monotonic()+2
            while True:
                try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() >= until: raise QuestionBusy('这一课堂还有提问正在处理，请稍后重试。')
                    await asyncio.sleep(.1)
            try:
                saved = history(self.run)
                prior = next((x for x in saved if x['request_id']==rid),None)
                if prior:
                    if prior['_fingerprint'] != fingerprint: raise RequestConflict('请求编号已用于另一个问题。')
                    answer = {k:v for k,v in prior.items() if not k.startswith('_')}
                    yield {'type':'start','request_id':rid,'cached':True}
                    yield {'type':'done','request_id':rid,'cached':True,'answer':answer}
                    return
                payload = question_context(rows,translations,request)
                payload.update(question=request['question'],learner_profile=profile(learner))
                yield {'type':'start','request_id':rid,'cached':False}
                started = time.monotonic(); until = started+self.deadline
                buffer = ''; sent = {'answer':'','example':''}; result = None; first_text = None; updates = 0
                stream = self.stream_respond(PROMPT,payload,SCHEMA,'subtitle_question')
                try:
                    while True:
                        remaining = until-time.monotonic()
                        if remaining <= 0: raise asyncio.TimeoutError()
                        try: event = await asyncio.wait_for(stream.__anext__(),remaining)
                        except StopAsyncIteration: break
                        if event['type'] == 'delta':
                            buffer += event['text']
                            for field,value in partial_fields(buffer).items():
                                if not value.startswith(sent[field]): raise InvalidAnswer('non-monotonic stream')
                                delta = value[len(sent[field]):]
                                if delta:
                                    sent[field] = value; updates += 1
                                    if field=='answer' and value.strip() and first_text is None:
                                        first_text = round(time.monotonic()-started,3)
                                    yield {'type':field+'_delta','request_id':rid,'text':delta}
                        elif event['type'] == 'result': result = event['answer']
                        else: raise InvalidAnswer('unknown provider event')
                except httpx.HTTPStatusError as error: raise ProviderUnavailable(error) from None
                finally: await stream.aclose()
                if result is None: raise InvalidAnswer('missing final answer')
                validate_answer(result,payload['context'])
                if any(result[field] != sent[field] for field in sent): raise InvalidAnswer('final answer differs from stream')
                answer = {**result,'request_id':rid,'question':request['question'],'selection':payload['selected'],
                    'profile_level':payload['learner_profile']['level'],'created_at':time.time(),
                    'elapsed_seconds':round(time.monotonic()-started,3),'first_text_seconds':first_text,
                    'stream_updates':updates,'delivery':'stream','provenance':'classroom_context_and_general_knowledge',
                    'memory_queries':0,'memory_writes':0}
                atomic(self.run/'questions.json',saved+[{**answer,'_fingerprint':fingerprint}])
                yield {'type':'done','request_id':rid,'cached':False,'answer':answer}
            finally: fcntl.flock(lock,fcntl.LOCK_UN)

def event_response(engine, request, rows, translations, learner):
    def encode(event): return ('data: '+json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n\n').encode()
    async def events():
        stream = engine.stream(request,rows,translations,learner)
        error = None
        try:
            async for event in stream: yield encode(event)
        except (StaleSelection,RequestConflict) as e: error = (409,str(e))
        except QuestionBusy as e: error = (429,str(e))
        except ProviderUnavailable as e: error = (e.status_code,str(e))
        except asyncio.TimeoutError: error = (504,'这次回答超时，已显示内容尚未完成，请重试。')
        except (InvalidAnswer,ValueError): error = (502,'回答未通过完整性或原文引用核验，已显示内容尚未确认，请重试。')
        except Exception: error = (502,'回答流已中断，已显示内容尚未完成，请重试。')
        finally: await stream.aclose()
        if error:
            yield encode({'type':'error','request_id':request['request_id'],'code':error[0],'message':error[1]})
    return StreamingResponse(events(),media_type='text/event-stream',headers={
        'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})
