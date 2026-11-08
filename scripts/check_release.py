#!/usr/bin/env python3
"""Fail publication checks without echoing matching credentials or personal data."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT, stderr=subprocess.DEVNULL).decode().split('\0')
    paths = [p for p in paths if p]
except subprocess.CalledProcessError:
    paths = [str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file()
             and not any(x in p.relative_to(ROOT).parts for x in ['.git', '.venv', '__pycache__', 'dist', 'build'])]
blocked_dirs = {'secrets', 'runtime', 'state', 'evidence', 'BASELINE_FILE'}
blocked_suffixes = {'.pcm', '.jsonl', '.key', '.pem', '.p12', '.log'}
patterns = [
    re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{28,}'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{25,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{25,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(r'(?:/Users/|/root/)[A-Za-z0-9_.-]+/'),
    re.compile(r'\b[A-Za-z0-9-]+\.gpuhome\.cc\b'),
]
errors = []
for name in paths:
    path = ROOT / name
    relative = Path(name)
    if path.is_symlink() or blocked_dirs.intersection(relative.parts) or relative.suffix in blocked_suffixes:
        errors.append(name + ': private/runtime artifact')
        continue
    if relative.name.startswith('.env') and relative.name != '.env.example':
        errors.append(name + ': environment file')
        continue
    data = path.read_bytes()
    if relative.suffix in {'.wav', '.png'}:
        allowed = {'workbench/meeting/static/lecture-sample.wav', 'workbench/meeting/static/tmcra-mark.png'}
        if name not in allowed or len(data) > 1_000_000:
            errors.append(name + ': unreviewed binary asset')
        continue
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        errors.append(name + ': unreviewed binary')
        continue
    if any(pattern.search(text) for pattern in patterns):
        errors.append(name + ': sensitive content pattern')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    print('RELEASE_SCAN=FAIL')
    raise SystemExit(1)
print('RELEASE_SCAN=PASS; files=%d; credential_patterns=0; runtime_artifacts=0' % len(paths))
