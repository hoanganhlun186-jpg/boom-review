import sys
from pathlib import Path
import unittest
import os
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import gemini_web


class FakeEditor:
    def __init__(self, text='', visible=True, disabled=False):
        self.text = text
        self.visible = visible
        self.disabled = disabled
        self.evaluate_calls = 0

    def is_visible(self):
        return self.visible

    def get_attribute(self, name):
        if name == 'aria-disabled':
            return 'true' if self.disabled else None
        return None

    def inner_text(self):
        return self.text

    def text_content(self):
        return self.text

    def click(self, **kwargs):
        return None

    def focus(self):
        return None

    def evaluate(self, script, value):
        self.evaluate_calls += 1
        self.text = value


class FakePage:
    def __init__(self, editors):
        self.editors = editors

    def query_selector_all(self, selector):
        return self.editors if selector == gemini_web._INPUT_SELS[0] else []


class GeminiWebInputTests(unittest.TestCase):
    def test_find_input_prefers_last_visible_editable_composer(self):
        stale = FakeEditor('old')
        hidden = FakeEditor('hidden', visible=False)
        composer = FakeEditor('')
        self.assertIs(
            gemini_web._find_input(FakePage([stale, hidden, composer])),
            composer,
        )

    def test_large_prompt_is_injected_without_playwright_fill(self):
        composer = FakeEditor('')
        prompt = ('Dòng prompt rất dài\n' * 5000).strip()
        gemini_web._inject_text(FakePage([composer]), prompt)
        self.assertEqual(composer.text, prompt)
        self.assertEqual(composer.evaluate_calls, 1)

    def test_existing_prompt_is_not_injected_twice_after_timeout(self):
        composer = FakeEditor('Dòng một\nDòng hai')
        gemini_web._inject_text(FakePage([composer]), 'Dòng một\nDòng hai')
        self.assertEqual(composer.evaluate_calls, 0)


class GeminiWebProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_profile_dir = os.environ.get('AUTORECAP_CHROME_PROFILE_DIR')
        os.environ['AUTORECAP_CHROME_PROFILE_DIR'] = os.path.join(self.temp_dir.name, 'gemini_profile')
        gemini_web.set_active_profile(1)

    def tearDown(self):
        if self.old_profile_dir is None:
            os.environ.pop('AUTORECAP_CHROME_PROFILE_DIR', None)
        else:
            os.environ['AUTORECAP_CHROME_PROFILE_DIR'] = self.old_profile_dir
        gemini_web.set_active_profile(1)
        self.temp_dir.cleanup()

    def _mark_ready(self, index):
        profile_dir = gemini_web._default_profile_dir(index)
        os.makedirs(profile_dir, exist_ok=True)
        with open(os.path.join(profile_dir, gemini_web._LOGIN_MARKER), 'w', encoding='ascii') as f:
            f.write('1')

    def test_profile_one_keeps_legacy_path(self):
        root = os.environ['AUTORECAP_CHROME_PROFILE_DIR']
        self.assertEqual(gemini_web._default_profile_dir(1), root)
        self.assertEqual(gemini_web._default_profile_dir(2), root + '_2')

    def test_next_ready_profile_wraps_and_skips_missing(self):
        self._mark_ready(1)
        self._mark_ready(3)
        self.assertEqual(gemini_web.ready_profile_indices(), [1, 3])
        self.assertEqual(gemini_web.next_ready_profile(1), 3)
        self.assertEqual(gemini_web.next_ready_profile(3), 1)

    def test_logout_removes_only_selected_profile(self):
        self._mark_ready(1)
        self._mark_ready(2)
        self.assertTrue(gemini_web.logout_profile(2))
        self.assertTrue(gemini_web.is_profile_ready(1))
        self.assertFalse(gemini_web.is_profile_ready(2))


if __name__ == '__main__':
    unittest.main()
