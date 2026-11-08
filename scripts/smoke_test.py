#!/usr/bin/env python3
"""Run a fresh isolated server, auth and manual text-fixture lifecycle. No paid APIs."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='tmcra-smoke-') as tmp:
    runtime = Path(tmp) / 'runtime'
    environment = {**os.environ, 'VOLC_API_KEY': 'TEST_VOICE_NOT_REAL',
                   'DEEPSEEK_API_KEY': 'TEST_LLM_NOT_REAL', 'PYTHONDONTWRITEBYTECODE': '1'}
    for key in ['MEETING_ROOT', 'MEETING_CONFIG', 'TMCRA_BASE_URL', 'TMCRA_CA_FILE']:
        environment.pop(key, None)
    cli = [sys.executable, str(ROOT / 'scripts/workspace.py')]
    subprocess.run(cli + ['init', '--root', str(runtime)], env=environment, check=True,
                   capture_output=True, text=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = 'http://127.0.0.1:%d' % port
    with (Path(tmp) / 'server.log').open('w') as log:
        server = subprocess.Popen(cli + ['serve', '--root', str(runtime), '--port', str(port)],
                                  env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Isolated server exited during startup')
                    try:
                        health = client.get(base + '/healthz').json()
                        break
                    except (httpx.HTTPError, ValueError):
                        time.sleep(.1)
                else:
                    raise TimeoutError('Isolated server startup')
                assert health['memory_bypass'] and not health['tmcra_write'] and health['resumable_audio']
                assert client.get(base + '/v1/sessions').status_code == 401
                link = subprocess.check_output(cli + ['login', '--root', str(runtime), '--port', str(port)],
                                               env=environment, text=True).splitlines()[0]
                code = link.split('#login=', 1)[1]
                response = client.post(base + '/v1/browser-login/exchange', json={'code': code})
                response.raise_for_status()
                auth = {'Authorization': 'Bearer ' + response.json()['access_token']}
                assert client.post(base + '/v1/browser-login/exchange', json={'code': code}).status_code == 401
                assert client.get(base + '/v1/sessions', headers=auth).json()['sessions'] == []
                body = {'title': 'Synthetic smoke fixture', 'mode': 'text_fixture', 'assistant_enabled': False}
                response = client.post(base + '/v1/sessions', headers=auth, json=body)
                response.raise_for_status()
                sid = response.json()['session']['id']
                client.post(base + '/v1/sessions/' + sid + '/text-fixture', headers=auth,
                            json={'texts': ['这是公开项目的合成测试，不是真实课堂记录。']}).raise_for_status()
                client.post(base + '/v1/sessions/' + sid + '/finish', headers=auth).raise_for_status()
                for _ in range(50):
                    view = client.get(base + '/v1/sessions/' + sid, headers=auth).json()
                    if view['session']['status'] == 'completed':
                        break
                    time.sleep(.05)
                assert view['session']['status'] == 'completed' and len(view['transcripts']) == 1
                transcript = runtime / 'state/meetings' / sid / 'transcripts.json'
                digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
                assert not list(runtime.rglob('*.pcm'))
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        assert hashlib.sha256(transcript.read_bytes()).hexdigest() == digest
print('SMOKE=PASS; fresh_workspace=1; auth=1; manual_finish=1; preserved_transcript=1; microphone=0; paid_api_calls=0')
