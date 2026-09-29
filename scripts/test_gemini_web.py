import sys
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()
