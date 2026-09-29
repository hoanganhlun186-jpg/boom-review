import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.full_pipeline import FullPipeline
from engine.market_readiness import MarketReadinessValidator


class FakeAI:
    @staticmethod
    def _clean_review_script_text(value):
        return str(value or '').strip()

    @staticmethod
    def _extract_json_payload(value):
        return json.loads(value)

    def _try_generate(self, prompt):
        data = json.loads(prompt.split('INPUT:\n', 1)[1])
        return json.dumps({
            'script_blocks': [
                {
                    'block_id': item['block_id'],
                    'text': ' '.join(f'từ{index}' for index in range(item['minimum_words'])),
                }
                for item in data
            ]
        }, ensure_ascii=False)


class MarketAutoRepairTests(unittest.TestCase):
    def test_story_segments_are_validated_by_source_coverage(self):
        renders = [dict(block_id=index) for index in range(1, 5)]
        scripts = [
            dict(block_id=1, source_block_ids=[1, 2], script_block_strategy='chapter_budget_story_segment'),
            dict(block_id=2, source_block_ids=[3, 4], script_block_strategy='chapter_budget_story_segment'),
        ]
        issues, metrics = MarketReadinessValidator._block_count_report(
            scripts,
            renders,
            {'script_block_strategy': 'chapter_budget_story_segment'},
        )
        self.assertFalse(issues)
        self.assertEqual(metrics['mapped_render_block_count'], 4)
        self.assertEqual(metrics['missing_blocks'], 0)

    def test_under_target_story_block_is_expanded_before_market(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda message: None)
            scripts = [{
                'block_id': 1,
                'text': 'Đoạn quá ngắn.',
                'target_words': 20,
                'source_block_ids': [1],
            }]
            renders = [{
                'block_id': 1,
                'srt_anchor': 'Nhân vật trở về nhà và phát hiện biến cố.',
                'visual_anchor': 'Nhân vật đứng trước căn nhà bị phá hủy.',
            }]
            repaired, changed, remaining = pipeline._repair_under_target_story_blocks(
                FakeAI(), scripts, renders
            )
            self.assertEqual(changed, 1)
            self.assertEqual(remaining, 0)
            self.assertGreaterEqual(len(repaired[0]['text'].split()), 11)


if __name__ == '__main__':
    unittest.main()
