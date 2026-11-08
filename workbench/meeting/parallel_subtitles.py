"""Bounded subtitle workers: one recent batch, remaining slots drain older rows."""
import asyncio,copy,time
import httpx
from .subtitles import SubtitleTranslator,LANGUAGES,PROMPT,SCHEMA,finals,fingerprint,validate

ERRORS={'invalid target','missing translation','invalid translation fields','unverified utterance',
 'invalid translation text','whole-sentence translation forbidden in vocabulary mode',
 'empty translation','same-language source changed','invalid gloss count','invalid gloss fields',
 'invalid English term','term not grounded in source','invalid gloss explanation'}

def validate_individually(answer,selected,config):
 """Keep grounding checks strict, but a bad row no longer discards its neighbors."""
 target=config['target_language'];result={}
 if not isinstance(answer,dict) or set(answer)!={'target_language','items'} or answer['target_language']!=target or not isinstance(answer['items'],list):
  return {u['id']:{'error':'invalid_envelope'} for u in selected}
 for u in selected:
  entries=[x for x in answer['items'] if isinstance(x,dict) and x.get('id')==u['id']]
  if len(entries)!=1:result[u['id']]={'error':'missing_or_duplicate_id'};continue
  try:
   result[u['id']]=validate({'target_language':target,'items':entries},[u],target,config['mode'],config['vocabulary'])[u['id']]
  except (ValueError,TypeError,KeyError) as e:
   result[u['id']]={'error':str(e).replace(' ','_') if str(e) in ERRORS else 'invalid_result'}
 return result

class ParallelSubtitleTranslator(SubtitleTranslator):
 def __init__(self,responder,config=None,cached=None,deadline=12,concurrency=4,retry_base=2,window_seconds=0,batch_size=6,context_size=12,clock=time.monotonic):
  if type(concurrency) is not int or not 1<=concurrency<=8:raise ValueError('concurrency must be 1..8')
  super().__init__(responder,config,cached,deadline)
  if not 0<=window_seconds<=5 or not 1<=batch_size<=8 or not 0<=context_size<=20:raise ValueError('invalid rolling window bounds')
  self.window_seconds=window_seconds;self.batch_size=batch_size;self.context_size=context_size;self.clock=clock
  self.concurrency=concurrency;self.retry_base=retry_base;self.jobs={};self.seen={};self.dirty=False;self.cooldown_until=0
  self.metrics={'requests_started':0,'requests_finished':0,'active_requests':0,'peak_active_requests':0,'retries':0,'stale_results_discarded':0,'validation_errors':0}

 def configure(self,config,retry=False):
  for task in self.jobs:task.cancel()
  super().configure(config,retry);self.dirty=True

 async def close(self):
  tasks=list(self.jobs)
  for task in tasks:task.cancel()
  await asyncio.gather(*tasks,return_exceptions=True);self.jobs.clear()
  self.records={k:v for k,v in self.records.items() if v.get('status')!='translating'}

 def snapshot(self,rows,running=False):
  out=super().snapshot(rows,running)
  out.update(window_seconds=self.window_seconds,batch_size=self.batch_size if self.window_seconds else 3,parallelism=self.concurrency,inflight_batches=len(self.jobs),active_requests=self.metrics['active_requests'],
    retry_after_seconds=round(max(0,self.cooldown_until-time.monotonic()),2),metrics=dict(self.metrics))
  return out

 def _reap(self):
  for task in list(self.jobs):
   if task.done():
    self.jobs.pop(task)
    if not task.cancelled():task.result()

 async def step(self,get_rows,flush=False):
  async with self.lock:
   self._reap();dirty=self.dirty;self.dirty=False
   if not self.config['enabled']:return dirty
   rows=finals(get_rows());current={u['id']:u for u in rows}
   self.records={k:v for k,v in self.records.items() if k in current and self.matching(current[k])}
   live_keys={(u['id'],fingerprint(u)) for u in rows}
   self.seen={k:v for k,v in self.seen.items() if k in live_keys}
   for key in live_keys:self.seen.setdefault(key,self.clock())
   claims={key for job in self.jobs.values() if job['generation']==self.generation for key in job['claims']}
   pending=[u for u in rows if (u['id'],fingerprint(u)) not in claims and (not self.matching(u) or self.matching(u)['status']=='queued')]
   import re
   usable=[]
   for u in pending:
    if len(u['text'])>4000:self.records[u['id']]=self.record(u,self.config['target_language'],'error',error='source_too_long');dirty=True
    elif self.config['mode']=='vocabulary' and not re.search('[A-Za-z]',u['text']):
     self.records[u['id']]=self.record(u,self.config['target_language'],'translated',text='',same_language=False,glosses=[],reason='no_english_terms');dirty=True
    else:usable.append(u)
   while usable and len(self.jobs)<self.concurrency and time.monotonic()>=self.cooldown_until:
    if self.window_seconds and not flush and self.clock()-min(self.seen[(u['id'],fingerprint(u))] for u in usable)<self.window_seconds:break
    priority='latest' if not any(j['priority']=='latest' for j in self.jobs.values()) else 'backlog'
    if self.window_seconds:
     priority='window';selected=[];chars=0
     while usable and len(selected)<self.batch_size and (not selected or chars+len(usable[0]['text'])<=3000):
      item=usable.pop(0);selected.append(item);chars+=len(item['text'])
    elif priority=='latest':selected=[usable.pop()]
    else:
     selected=[];chars=0
     while usable and len(selected)<3 and (not selected or chars+len(usable[0]['text'])<=3000):
      item=usable.pop(0);selected.append(item);chars+=len(item['text'])
    selected=copy.deepcopy(selected);config=copy.deepcopy(self.config);generation=self.generation
    first=next(i for i,u in enumerate(rows) if u['id']==selected[0]['id'])
    context=[{'text':u['text'],'speaker':u.get('speaker','unknown')} for u in rows[max(0,first-(self.context_size if self.window_seconds else 2)):first]]
    while len(context)>1 and sum(len(u['text']) for u in context)>4000:context.pop(0)
    queued={u['id']:max(0,self.clock()-self.seen[(u['id'],fingerprint(u))]) for u in selected}
    for u in selected:self.records[u['id']]=self.record(u,config['target_language'],'translating',schedule_priority=priority)
    task=asyncio.create_task(self._request(selected,context,config,generation,priority,queued,get_rows))
    self.jobs[task]={'priority':priority,'generation':generation,'claims':{(u['id'],fingerprint(u)) for u in selected}}
    dirty=True
   if self.jobs:
    # Never wait for the slowest batch before making room for the next sentence.
    await asyncio.wait(list(self.jobs),timeout=.1,return_when=asyncio.FIRST_COMPLETED)
    self._reap()
   dirty=dirty or self.dirty;self.dirty=False
   return dirty

 async def _request(self,selected,context,config,generation,priority,queued,get_rows):
  payload={'target_language':config['target_language'],'target_language_name':LANGUAGES[config['target_language']],
   'mode':config['mode'],'english_vocabulary':config['vocabulary'],'context':context,
   'items':[{'id':u['id'],'text':u['text'],'speaker':u.get('speaker','unknown')} for u in selected]}
  started=time.monotonic();results={};attempt=0;request_error=None
  try:
   for attempt in (1,2):
    delay=max(0,self.cooldown_until-time.monotonic())
    if delay:await asyncio.sleep(delay)
    self.metrics['requests_started']+=1;self.metrics['active_requests']+=1
    self.metrics['peak_active_requests']=max(self.metrics['peak_active_requests'],self.metrics['active_requests'])
    try:
     answer=await asyncio.wait_for(self.respond(PROMPT,payload,SCHEMA,'subtitle_translation'),self.deadline)
     results=validate_individually(answer,selected,config);request_error=None;break
    except asyncio.CancelledError:raise
    except Exception as e:
     code=e.response.status_code if isinstance(e,httpx.HTTPStatusError) else None
     retryable=code==429 or code is not None and code>=500 or isinstance(e,(asyncio.TimeoutError,httpx.TransportError))
     request_error='timeout' if isinstance(e,(asyncio.TimeoutError,httpx.TimeoutException)) else 'rate_limited' if code==429 else 'upstream_error' if code else 'translation_failed'
     if not retryable or attempt==2:break
     delay=self.retry_base
     if code==429:
      try:delay=max(delay,min(30,float(e.response.headers.get('retry-after',delay))))
      except (ValueError,TypeError):pass
     self.cooldown_until=max(self.cooldown_until,time.monotonic()+delay);self.metrics['retries']+=1
    finally:
     self.metrics['active_requests']-=1;self.metrics['requests_finished']+=1
   if generation!=self.generation or not self.config['enabled']:return
   latest={u['id']:u for u in finals(get_rows())};finished=time.time()
   for u in selected:
    if u['id'] not in latest or fingerprint(latest[u['id']])!=fingerprint(u):
     self.metrics['stale_results_discarded']+=1;continue
    item=results.get(u['id'],{});detail=item.get('error');error=request_error or ('validation_failed' if detail else None)
    record=self.record(u,config['target_language'],'error' if error else 'translated',
      schedule_priority=priority,attempts=attempt,batch_size=len(selected),queue_seconds=round(queued[u['id']],3))
    if error:
     record.update(error=error,error_detail=detail or request_error)
     if detail:self.metrics['validation_errors']+=1
    else:
     record.update(item,completed_at=finished,request_seconds=round(time.monotonic()-started,3),
       after_asr_ms=round(max(0,finished-u['completed_wall'])*1000) if isinstance(u.get('completed_wall'),(int,float)) else None)
    self.records[u['id']]=record;self.dirty=True
  except asyncio.CancelledError:
   # A mode change, pause or shutdown must not publish an older-language result.
   if generation==self.generation:
    for u in selected:
     r=self.records.get(u['id'])
     if r and r.get('status')=='translating' and r.get('source_fingerprint')==fingerprint(u):self.records.pop(u['id'],None)
   self.dirty=True;raise
