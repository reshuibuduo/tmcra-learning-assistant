#!/usr/bin/env python3
"""Offline suite: explicit event loops also support the legacy synchronous tests."""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workbench'))
sys.path.insert(0, str(ROOT))


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


names = ['meeting.' + p.stem for p in sorted((ROOT / 'workbench/meeting').glob('test_*.py'))]
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py'))
result = unittest.TestResult()
for test in flatten(suite):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        test.run(result)
    finally:
        loop.close()
        asyncio.set_event_loop(None)
for test, trace in result.errors + result.failures:
    print(str(test) + '\n' + trace, file=sys.stderr)
print('BACKEND_TESTS=%d %s' % (result.testsRun, 'PASS' if result.wasSuccessful() else 'FAIL'))
raise SystemExit(0 if result.wasSuccessful() else 1)
