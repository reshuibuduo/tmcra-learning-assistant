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
from html.parser import HTMLParser
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]

# Loaded only by isolated test subprocesses, never by the user's running app.
# Preserve the guard across workspace.py's exec into uvicorn.
NETWORK_GUARD = '''\
import ipaddress, os, socket, sys

def loopback(host):
    if isinstance(host, bytes):
        host = host.decode('ascii')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def audit(event, args):
    host = None
    forbidden_port = False
    if event == 'socket.connect' and args[0].family in (socket.AF_INET, socket.AF_INET6):
        host = args[1][0]
        forbidden_port = str(args[1][1]) != os.environ.get('LOCAL_SMOKE_PORT')
    elif event in ('socket.getaddrinfo', 'socket.gethostbyname'):
        host = args[0]
    if host is not None and (not loopback(host) or forbidden_port):
        with open(os.environ['LOCAL_SMOKE_BLOCKED'], 'a') as output:
            output.write(event + '\\n')
        raise PermissionError('Local smoke test permits only its own server address')

sys.addaudithook(audit)
original_exec = os.execvpe
def guarded_exec(file, args, env):
    env = dict(env)
    env['PYTHONPATH'] = os.environ['LOCAL_SMOKE_GUARD'] + os.pathsep + env.get('PYTHONPATH', '')
    return original_exec(file, args, env)
os.execvpe = guarded_exec
'''


class PageResources(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ('script', 'img') and values.get('src'):
            self.urls.add(values['src'])
        if tag == 'link' and values.get('href'):
            self.urls.add(values['href'])


with tempfile.TemporaryDirectory(prefix='tmcra-smoke-') as tmp:
    runtime = Path(tmp) / 'runtime'
    guard = Path(tmp) / 'guard'
    guard.mkdir()
    (guard / 'sitecustomize.py').write_text(NETWORK_GUARD)
    blocked = Path(tmp) / 'blocked.txt'
    environment = {**os.environ, 'VOLC_API_KEY': 'TEST_VOICE_NOT_REAL',
                   'DEEPSEEK_API_KEY': 'TEST_LLM_NOT_REAL', 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTHONPATH': str(guard), 'LOCAL_SMOKE_GUARD': str(guard),
                   'LOCAL_SMOKE_BLOCKED': str(blocked)}
    for key in ['MEETING_ROOT', 'MEETING_CONFIG', 'TMCRA_BASE_URL', 'TMCRA_CA_FILE',
                'TMCRA_MEMORY_BYPASS', 'OPENAI_API_KEY', 'TMCRA_API_KEY']:
        environment.pop(key, None)
    # Prove that a public address is blocked before any network request is sent.
    probe = subprocess.run([sys.executable, '-c',
                            "import socket; socket.create_connection(('203.0.113.1', 443), 1)"],
                           env=environment, capture_output=True, text=True)
    assert probe.returncode != 0 and blocked.is_file()
    blocked.unlink()
    cli = [sys.executable, str(ROOT / 'scripts/workspace.py')]
    subprocess.run(cli + ['init', '--root', str(runtime)], env=environment, check=True,
                   capture_output=True, text=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = 'http://127.0.0.1:%d' % port
    environment['LOCAL_SMOKE_PORT'] = str(port)
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
                assert health['llm_provider'] == 'deepseek' and health['llm_direct']
                assert not health['memory_recall_available'] and not health['explicit_learning_import']
                page = client.get(base + '/')
                page.raise_for_status()
                resources = PageResources(page.text).urls
                assert resources and all(url.startswith('/assets/') for url in resources)
                for url in sorted(resources):
                    asset = client.get(base + url)
                    asset.raise_for_status()
                    assert asset.content
                assert (runtime / 'secrets/tmcra_api_key').read_text() == ''
                assert client.get(base + '/v1/sessions').status_code == 401
                link = subprocess.check_output(cli + ['login', '--root', str(runtime), '--port', str(port)],
                                               env=environment, text=True).splitlines()[0]
                code = link.split('#login=', 1)[1]
                response = client.post(base + '/v1/browser-login/exchange', json={'code': code})
                response.raise_for_status()
                auth = {'Authorization': 'Bearer ' + response.json()['access_token']}
                assert client.post(base + '/v1/browser-login/exchange', json={'code': code}).status_code == 401
                assert client.get(base + '/v1/sessions', headers=auth).json()['sessions'] == []
                assert not list((runtime / 'state/meetings').iterdir())
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
        assert not blocked.exists(), 'Isolated lifecycle attempted external networking'
print('SMOKE=PASS; fresh_workspace=1; local_assets=1; external_network_attempts=0; '
      'memory_server=0; auth=1; manual_finish=1; preserved_transcript=1; microphone=0; paid_api_calls=0')
