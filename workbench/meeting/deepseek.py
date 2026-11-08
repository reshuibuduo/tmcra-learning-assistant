"""Direct DeepSeek JSON adapter; no proxy, fallback, or transcript logging."""
import json
import time
from pathlib import Path
import httpx
from jsonschema import Draft202012Validator, ValidationError

ENDPOINT = 'https://api.deepseek.com/chat/completions'

def provider_key(root, config):
    provider = config.get('llm_provider', 'openai')
    if provider not in {'openai', 'deepseek'}:
        raise ValueError('unsupported reasoning provider')
    return (Path(root) / 'secrets' / (provider + '_api_key')).read_text().strip()

async def respond(memory, prompt, data, schema, name, config, append):
    model = config['planner_model'] if name in {'recall_plan', 'learning_route'} else config['reasoning_model']
    if name == 'subtitle_translation':
        model = config.get('translation_model', config['planner_model'])
    if memory.deepseek_client is None or memory.deepseek_client.is_closed:
        memory.deepseek_client = httpx.AsyncClient(
            trust_env=False, timeout=config['reasoning_timeout_seconds'],
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=30))
    # JSON mode guarantees syntax, not this application's schema or citations.
    payload = {'model': model, 'thinking': {'type': 'disabled'},
               'max_tokens': config['max_output_tokens'], 'stream': False,
               'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': prompt +
                   '\n只输出一个符合以下 JSON Schema 的 JSON 对象，不加 Markdown。'
                   '\nJSON 示例形式：{"字段名":"字段值"}；实际字段必须严格服从 Schema：\n' +
                   json.dumps(schema, ensure_ascii=False, separators=(',', ':'))},
                  {'role': 'user', 'content': json.dumps(data, ensure_ascii=False, separators=(',', ':'))}]}
    start = time.monotonic()
    meta = {'time': time.time(), 'provider': 'deepseek', 'direct': True,
            'operation': name, 'model': model, 'thinking': 'disabled'}
    try:
        response = await memory.deepseek_client.post(
            ENDPOINT, headers={'Authorization': 'Bearer ' + memory.key}, json=payload)
        meta.update(status=response.status_code, request_id=response.headers.get('x-request-id'))
        response.raise_for_status()
        body = response.json()
        choice = body['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('incomplete DeepSeek output')
        content = choice['message'].get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('blank DeepSeek JSON output')
        result = json.loads(content)
        try:
            Draft202012Validator(schema).validate(result)
        except ValidationError:
            raise ValueError('DeepSeek output schema mismatch') from None
        append(memory.run / 'api-usage.jsonl', {'operation': name, 'provider': 'deepseek',
               'usage': body.get('usage'), 'response_id': body.get('id')})
        meta['validated'] = True
        return result
    except Exception as exc:
        meta['error'] = type(exc).__name__
        raise
    finally:
        meta['elapsed_seconds'] = round(time.monotonic() - start, 3)
        append(memory.run / 'api-requests.jsonl', meta)
