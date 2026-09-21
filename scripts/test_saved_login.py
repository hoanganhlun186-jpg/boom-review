import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ConfigManager
import boom_auth

class SavedLoginTests(unittest.TestCase):
    def test_shared_api_store_survives_multiple_instances(self):
        from ui.config_manager import ConfigManager as UIConfig
        self.assertIs(UIConfig, ConfigManager)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'config.json')
            first, second = ConfigManager(path), UIConfig(path)
            second.load()
            self.assertTrue(first.save({'gemini_api_key':'test-gemini','pekka_api_key':'test-pekka'}))
            self.assertTrue(second.save({'tts_voice':'pekka:test'}))
            self.assertEqual(first.get('pekka_api_key'), 'test-pekka')
            self.assertEqual(first.get('gemini_api_key'), 'test-gemini')
            self.assertNotIn('test-pekka', Path(path).read_text())
            self.assertEqual(ConfigManager(path).get('tts_voice'), 'pekka:test')

    def test_failed_write_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(str(Path(folder)/'config.json'))
            manager.save({'pekka_api_key':'old'})
            with patch('config.os.replace', side_effect=PermissionError('test')):
                self.assertFalse(manager.save({'pekka_api_key':'new'}))
            self.assertEqual(manager.get('pekka_api_key'), 'old')

    def test_saved_session_and_expiry_formats(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(boom_auth,'TOKEN_FILE',str(Path(folder)/'auth.json')):
            expiry = (datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
            self.assertTrue(boom_auth.save_token('tester','test-token',expiry))
            stored = boom_auth.load_token()
            self.assertEqual(stored['token'],'test-token')
            self.assertTrue(boom_auth.is_token_valid(stored))
            self.assertNotIn('password',stored)
            self.assertNotIn('test-token', Path(boom_auth.TOKEN_FILE).read_text())
            with patch.object(boom_auth,'verify_token_with_server',return_value=True), patch.object(boom_auth,'LoginDialog') as dialog:
                self.assertEqual(boom_auth.check_auth(), (True,'tester','test-token'))
                dialog.assert_not_called()
            for value in ('2099-01-01 00:00:00','2099-01-01T00:00:00Z'):
                self.assertTrue(boom_auth.is_token_valid({'token':'test','expiry':value}))

    def test_network_failure_does_not_erase_session_or_bypass_auth(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(boom_auth,'TOKEN_FILE',str(Path(folder)/'auth.json')):
            boom_auth.save_token('tester','test','2099-01-01 00:00:00')
            with patch.object(boom_auth,'verify_token_with_server',return_value=None), patch.object(boom_auth,'LoginDialog'):
                self.assertFalse(boom_auth.check_auth()[0])
                self.assertEqual(boom_auth.load_token()['token'],'test')
            with patch.object(boom_auth,'verify_token_with_server',return_value=False), patch.object(boom_auth,'LoginDialog'):
                self.assertFalse(boom_auth.check_auth()[0])
                self.assertEqual(boom_auth.load_token(),{})

if __name__ == '__main__': unittest.main()
