#!/usr/bin/env python3
"""Portable local workspace launcher. Never starts recording or logs provider keys."""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
KEY_FILES = {
    'VOLC_API_KEY': 'volc_api_key',
    'DEEPSEEK_API_KEY': 'deepseek_api_key',
    'OPENAI_API_KEY': 'openai_api_key',
    'TMCRA_API_KEY': 'tmcra_api_key',
}


def read_env(path):
    """Read KEY=value data, without evaluating shell code or substitutions."""
    values = {}
    if path is None:
        return values
    for number, raw in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or key not in KEY_FILES:
            raise ValueError('Invalid or unsupported environment entry on line %d' % number)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def secure_write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Secret files must not be symlinks')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(value)


def initialize(root, env_file=None, provider='deepseek', environ=None):
    root = Path(root).expanduser().resolve()
    env = dict(os.environ if environ is None else environ)
    values = {**read_env(env_file), **{k: env[k] for k in KEY_FILES if k in env}}
    config_path = root / 'config.json'
    if config_path.exists():
        config = json.loads(config_path.read_text())
        provider = config.get('llm_provider', 'openai')
    else:
        source = PROJECT / 'workbench' / ('ops/deepseek-config.json' if provider == 'deepseek' else 'config.json')
        config = json.loads(source.read_text())
        config.update(llm_provider=provider, memory_recall_enabled=False, tmcra_write=False,
                      learner_memory_scopes=[], capture_enabled=True)
    if provider not in ('openai', 'deepseek'):
        raise ValueError('Unsupported provider')
    required = ['VOLC_API_KEY', provider.upper() + '_API_KEY']
    if config.get('memory_recall_enabled') or config.get('tmcra_write'):
        required.append('TMCRA_API_KEY')
    missing = []
    for name in required:
        file = root / 'secrets' / KEY_FILES[name]
        existing = file.read_text().strip() if file.is_file() else ''
        if not values.get(name, '').strip() and not existing:
            missing.append(name)
    if missing:
        raise ValueError('Missing configuration: ' + ', '.join(missing))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_dir = root / 'secrets'
    if secret_dir.is_symlink():
        raise ValueError('Secrets directory must not be a symlink')
    secret_dir.mkdir(exist_ok=True, mode=0o700)
    secret_dir.chmod(0o700)
    token_path = secret_dir / 'meeting_token'
    if not token_path.exists():
        secure_write(token_path, secrets.token_urlsafe(48))
    for name, filename in KEY_FILES.items():
        file = secret_dir / filename
        value = values.get(name, '').strip()
        if value or not file.exists():
            secure_write(file, value)
        elif file.is_symlink():
            raise ValueError('Secret files must not be symlinks')
        else:
            file.chmod(0o600)
    if not config_path.exists():
        secure_write(config_path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    return root


def runtime_environment(root):
    root = Path(root).expanduser().resolve()
    config_path = root / 'config.json'
    if not config_path.is_file():
        raise ValueError('Run init before serve or login')
    config = json.loads(config_path.read_text())
    required = ['meeting_token', 'volc_api_key', config.get('llm_provider', 'openai') + '_api_key']
    if config.get('memory_recall_enabled') or config.get('tmcra_write'):
        required.append('tmcra_api_key')
    for name in required:
        file = root / 'secrets' / name
        if not file.is_file() or not file.read_text().strip():
            raise ValueError('Missing workspace secret file: ' + name)
    return {**os.environ, 'MEETING_ROOT': str(root), 'MEETING_CONFIG': str(config_path),
            'PYTHONPATH': str(PROJECT / 'workbench'), 'PYTHONDONTWRITEBYTECODE': '1'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'serve', 'login'])
    parser.add_argument('--root', type=Path, default=PROJECT / 'runtime')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--provider', choices=['deepseek', 'openai'], default='deepseek')
    parser.add_argument('--port', type=int, default=18782)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        if sys.version_info >= (3, 13):
            raise ValueError('Use Python 3.12: the PCM adapter currently needs audioop')
        if args.command == 'init':
            initialize(args.root, args.env_file, args.provider)
            print('WORKSPACE_READY; recording=manual; provider keys are not printed')
            return 0
        env = runtime_environment(args.root)
        if args.command == 'serve':
            os.execvpe(sys.executable, [sys.executable, '-m', 'uvicorn', 'meeting.service:app',
                       '--host', '127.0.0.1', '--port', str(args.port), '--no-access-log',
                       '--ws-max-size', '50000'], env)
        import httpx
        base = 'http://127.0.0.1:%d' % args.port
        token = (args.root.expanduser().resolve() / 'secrets/meeting_token').read_text().strip()
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post(base + '/v1/browser-login', headers={'Authorization': 'Bearer ' + token})
            response.raise_for_status()
            code = response.json()['code']
        print(base + '/#login=' + code)
        print('One-time link, valid for 90 seconds. Recording remains manual.')
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # Provider/server response bodies can contain personal data; do not dump them.
        print('Workspace command failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
