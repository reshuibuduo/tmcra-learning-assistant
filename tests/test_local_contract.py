"""Local edition contracts; no model calls, recording or service replacement."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts.workspace import initialize, main, runtime_environment

ROOT = Path(__file__).resolve().parents[1]
KEYS = {'VOLC_API_KEY': 'TEST_VOICE', 'DEEPSEEK_API_KEY': 'TEST_LLM'}


class LocalEditionTests(unittest.TestCase):
    def test_fresh_workspace_has_no_memory_service_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = initialize(Path(tmp) / 'runtime', environ=KEYS)
            config = json.loads((root / 'config.json').read_text())
            self.assertFalse(config['memory_recall_enabled'])
            self.assertFalse(config['tmcra_write'])
            self.assertEqual(config['learner_memory_scopes'], [])
            self.assertEqual((root / 'secrets/tmcra_api_key').read_text(), '')
            self.assertEqual(runtime_environment(root)['MEETING_ROOT'], str(root))
            self.assertFalse((root / 'state').exists())

    def test_serve_is_loopback_local_python_not_remote_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = initialize(Path(tmp) / 'runtime', environ=KEYS)
            with patch('scripts.workspace.os.execvpe', side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):
                    main(['serve', '--root', str(root), '--port', '18790'])
            binary, args, env = execute.call_args.args
            self.assertEqual(args[0], binary)
            self.assertEqual(args[1:3], ['-m', 'uvicorn'])
            self.assertEqual(args[args.index('--host') + 1], '127.0.0.1')
            self.assertEqual(args[args.index('--port') + 1], '18790')
            self.assertEqual(env['MEETING_ROOT'], str(root))
            self.assertFalse(any('ssh' == value for value in args))

    def test_default_provider_destinations_are_direct(self):
        from meeting.deepseek import ENDPOINT
        config = json.loads((ROOT / 'workbench/ops/deepseek-config.json').read_text())
        self.assertEqual(urlsplit(config['asr_endpoint']).hostname, 'openspeech.bytedance.com')
        self.assertEqual(urlsplit(ENDPOINT).hostname, 'api.deepseek.com')
        pipeline = (ROOT / 'workbench/meeting/pipeline.py').read_text()
        self.assertIn('https://api.openai.com/v1/responses', pipeline)

    def test_local_guide_is_linked_and_distinguishes_cloud_models(self):
        readme = (ROOT / 'README.md').read_text()
        guide = (ROOT / 'docs/local.md').read_text()
        self.assertIn('(docs/local.md)', readme)
        self.assertIn('不依赖项目方服务器', readme)
        for text in ['不代表模型完全离线', '你自己的 API Key',
                     'memory_recall_enabled=false', 'tmcra_write=false',
                     '不使用麦克风', '模型额度', '手动']:
            self.assertIn(text, guide)


if __name__ == '__main__':
    unittest.main()
