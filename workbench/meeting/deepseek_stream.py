"""True provider SSE; independent of the existing batch translation adapter."""
import asyncio
import json
import time
import httpx
from jsonschema import Draft202012Validator
from .deepseek import ENDPOINT
from .stream_json import LIMIT, strict_json

async def sse_data(response):
    data = []; size = 0
    async for line in response.aiter_lines():
        if not line:
            if data: yield '\n'.join(data)
            data = []; size = 0
        elif line.startswith('data:'):
            value = line[5:]
            if value.startswith(' '): value = value[1:]
            size += len(value)
            if size > 131072: raise ValueError('SSE frame too large')
            data.append(value)
    if data: yield '\n'.join(data)

async def respond_stream(memory, prompt, data, schema, name, config, append):
    if memory.deepseek_client is None or memory.deepseek_client.is_closed:
        memory.deepseek_client = httpx.AsyncClient(
            trust_env=False, timeout=config['reasoning_timeout_seconds'],
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=30))
    payload = {'model':config['reasoning_model'], 'thinking':{'type':'disabled'},
               'max_tokens':config['max_output_tokens'], 'stream':True,
               'stream_options':{'include_usage':True}, 'response_format':{'type':'json_object'},
               'messages':[{'role':'system','content':prompt +
                    '\n为便于流式显示，JSON顶层字段依次输出 answer、example、status、citations。先写 answer 正文；不要先输出引用或推理过程。'
                    '\n只输出一个JSON对象，不加Markdown。必须符合以下JSON Schema：\n'+json.dumps(schema,ensure_ascii=False)},
                   {'role':'user','content':json.dumps(data,ensure_ascii=False,separators=(',',':'))}]}
    started = time.monotonic(); content = ''; finish = None; done = False; usage = None; response_id = None
    meta = {'time':time.time(),'provider':'deepseek','direct':True,'operation':name,
            'model':config['reasoning_model'],'thinking':'disabled','stream':True,'content_chunks':0}
    try:
        async with memory.deepseek_client.stream('POST', ENDPOINT,
                headers={'Authorization':'Bearer '+memory.key}, json=payload) as response:
            meta.update(status=response.status_code,request_id=response.headers.get('x-request-id'))
            response.raise_for_status()
            async for frame in sse_data(response):
                if frame == '[DONE]': done = True; break
                chunk = json.loads(frame); response_id = chunk.get('id',response_id)
                if chunk.get('usage') is not None: usage = chunk['usage']
                choices = chunk.get('choices') or []
                if not choices: continue
                choice = choices[0]
                if choice.get('finish_reason') is not None: finish = choice['finish_reason']
                delta = (choice.get('delta') or {}).get('content')
                if delta:
                    if not isinstance(delta,str): raise ValueError('invalid provider text delta')
                    content += delta
                    if len(content) > LIMIT: raise ValueError('stream output too long')
                    meta.setdefault('first_token_seconds',round(time.monotonic()-started,3))
                    meta['content_chunks'] += 1
                    yield {'type':'delta','text':delta}
        if not done or finish != 'stop': raise ValueError('incomplete DeepSeek stream')
        result = strict_json(content)
        Draft202012Validator(schema).validate(result)
        meta['validated'] = True
        append(memory.run/'api-usage.jsonl',{'operation':name,'provider':'deepseek','stream':True,'usage':usage,'response_id':response_id})
        yield {'type':'result','answer':result}
    except asyncio.CancelledError:
        meta['error'] = 'CancelledError'; raise
    except GeneratorExit:
        meta['error'] = 'StreamClosed'; raise
    except Exception as error:
        meta['error'] = type(error).__name__; raise
    finally:
        meta['elapsed_seconds'] = round(time.monotonic()-started,3)
        append(memory.run/'api-requests.jsonl',meta)
