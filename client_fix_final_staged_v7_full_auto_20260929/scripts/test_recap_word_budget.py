import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.recap_engine import write_chapter_segments


def _response(word_count):
    return json.dumps({
        'segments': [{
            'block_id': 1,
            'source_block_ids': [1],
            'scene_role': 'setup_context',
            'text': ' '.join(f'từ{i}' for i in range(word_count)),
            'duration_hint_seconds': 30,
        }]
    }, ensure_ascii=False)


class RecapWordBudgetTests(unittest.TestCase):
    def _run(self, responses):
        calls = []

        def ai_call(prompt):
            calls.append(prompt)
            return responses[min(len(calls) - 1, len(responses) - 1)]

        result = write_chapter_segments(
            chapter_idx=1,
            chapter_scenes=[{
                'block_id': 1,
                'target_words': 100,
                'duration': 30,
                'original_start': 0,
                'original_end': 30,
                'srt_anchor': 'Nhân vật trở về nhà.',
            }],
            char_budget=400,
            logline='Một câu chuyện.',
            chapter_synopsis='Nhân vật trở về nhà.',
            prev_tail='',
            ai_call=ai_call,
            log=lambda message: None,
        )
        return result, calls

    def test_seventy_five_percent_is_accepted_without_expand_call(self):
        result, calls = self._run([_response(75)])
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertIn('70-115', calls[0])
        self.assertIn('KHONG CAN DUNG CHINH XAC', calls[0])

    def test_clearly_short_chapter_gets_only_one_expand_call(self):
        result, calls = self._run([_response(60), _response(80)])
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertIn('Không cần đạt đúng một con số cụ thể', calls[1])


if __name__ == '__main__':
    unittest.main()
