import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.workspace import initialize, read_env, runtime_environment, main


class WorkspaceTests(unittest.TestCase):
    def test_init_uses_private_permissions_and_memory_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            initialize(root, environ={'VOLC_API_KEY': 'TEST_VOICE', 'DEEPSEEK_API_KEY': 'TEST_LLM'})
            config = json.loads((root / 'config.json').read_text())
            self.assertFalse(config['tmcra_write'])
            self.assertFalse(config['memory_recall_enabled'])
            self.assertEqual(config['llm_provider'], 'deepseek')
            self.assertEqual((root / 'secrets/meeting_token').stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / 'secrets').stat().st_mode & 0o777, 0o700)
            self.assertGreater(len((root / 'secrets/meeting_token').read_text()), 32)
            self.assertFalse((root / 'state').exists())

    def test_reinitialization_keeps_token_config_and_recordings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialize(root, environ={'VOLC_API_KEY': 'TEST_VOICE', 'DEEPSEEK_API_KEY': 'TEST_LLM'})
            token = (root / 'secrets/meeting_token').read_bytes()
            old = (root / 'config.json').read_bytes()
            pcm = root / 'state/meetings/test/microphone.pcm'
            pcm.parent.mkdir(parents=True)
            pcm.write_bytes(b'\x01\x00' * 16)
            initialize(root, environ={})
            self.assertEqual((root / 'secrets/meeting_token').read_bytes(), token)
            self.assertEqual((root / 'config.json').read_bytes(), old)
            self.assertEqual(pcm.read_bytes(), b'\x01\x00' * 16)

    def test_missing_keys_fail_without_partial_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'new'
            with self.assertRaisesRegex(ValueError, 'Missing configuration'):
                initialize(root, environ={})
            self.assertFalse(root.exists())

    def test_environment_data_is_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / '.env'
            file.write_text('# comment\nVOLC_API_KEY="$(echo never-run)"\nDEEPSEEK_API_KEY=TEST\n')
            self.assertEqual(read_env(file)['VOLC_API_KEY'], '$(echo never-run)')
            initialize(Path(tmp) / 'workspace', file, environ={'DEEPSEEK_API_KEY': 'OVERRIDE'})
            self.assertEqual((Path(tmp) / 'workspace/secrets/deepseek_api_key').read_text(), 'OVERRIDE')

    def test_openai_optional_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialize(root, provider='openai', environ={'VOLC_API_KEY': 'TEST_VOICE', 'OPENAI_API_KEY': 'TEST_LLM'})
            self.assertEqual(json.loads((root / 'config.json').read_text())['llm_provider'], 'openai')
            self.assertEqual(runtime_environment(root)['MEETING_ROOT'], str(root.resolve()))

    def test_symlink_secret_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'runtime'
            initialize(root, environ={'VOLC_API_KEY': 'TEST_VOICE', 'DEEPSEEK_API_KEY': 'TEST_LLM'})
            key = root / 'secrets/deepseek_api_key'
            target = Path(tmp) / 'unrelated'
            target.write_text('PRESERVE')
            key.unlink()
            key.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                initialize(root, environ={'DEEPSEEK_API_KEY': 'REPLACEMENT'})
            self.assertEqual(target.read_text(), 'PRESERVE')

    def test_init_does_not_print_provider_keys(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, VOLC_API_KEY='TEST_VOICE', DEEPSEEK_API_KEY='TEST_PRIVATE'), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['init', '--root', tmp]), 0)
            self.assertNotIn('TEST_PRIVATE', output.getvalue())
            self.assertIn('recording=manual', output.getvalue())

    def test_invalid_env_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / '.env'
            file.write_text('UNRELATED=not-used\n')
            with self.assertRaisesRegex(ValueError, 'line 1'):
                read_env(file)
