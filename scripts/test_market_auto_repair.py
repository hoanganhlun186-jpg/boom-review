import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.full_pipeline import FullPipeline
from core.narration_guard import invalid_narration
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


class RefusingAlignmentAI:
    @staticmethod
    def _clean_review_script_text(value):
        return str(value or '').strip()

    @staticmethod
    def _extract_json_payload(value):
        return json.loads(value)

    def _try_generate(self, prompt):
        return json.dumps({
            'script_blocks': [{
                'block_id': 1,
                'text': 'Xin lỗi, tôi không thể hỗ trợ yêu cầu này.',
            }]
        }, ensure_ascii=False)


class RetryAlignmentAI(RefusingAlignmentAI):
    def __init__(self):
        self.calls = 0

    def _try_generate(self, prompt):
        self.calls += 1
        if self.calls == 1:
            return super()._try_generate(prompt)
        return json.dumps({
            'script_blocks': [{
                'block_id': 1,
                'text': 'Nhân vật chính bước vào thành phố và lập tức điều tra biến cố bí ẩn.',
            }]
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

    def test_alignment_repair_never_saves_ai_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda message: None)
            original = 'Nhân vật chính bước vào thành phố và bắt đầu điều tra biến cố.'
            pipeline.ai_package = {'script_blocks': [{
                'block_id': 1,
                'text': original,
            }]}
            pipeline.render_blocks = [{
                'block_id': 1,
                'srt_anchor': 'Nhân vật chính bước vào thành phố.',
            }]
            report = {'blocks': [{
                'block_id': 1,
                'errors': ['missing_anchor'],
                'required_anchor': 'Nhân vật chính bước vào thành phố.',
            }]}

            changed, count = pipeline._repair_srt_alignment_final_pass(
                RefusingAlignmentAI(), report, max_blocks=1
            )

            self.assertTrue(changed)
            self.assertEqual(count, 1)
            repaired = pipeline.ai_package['script_blocks'][0]['text']
            self.assertFalse(invalid_narration(repaired))
            self.assertNotIn('không thể hỗ trợ', repaired.lower())

    def test_alignment_repair_retries_refusal_then_accepts_narration(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda message: None)
            pipeline.ai_package = {'script_blocks': [{
                'block_id': 1,
                'text': 'Lời kể cũ vẫn hợp lệ.',
            }]}
            pipeline.render_blocks = [{
                'block_id': 1,
                'srt_anchor': 'Nhân vật chính bước vào thành phố.',
            }]
            report = {'blocks': [{
                'block_id': 1,
                'errors': ['missing_anchor'],
                'required_anchor': 'Nhân vật chính bước vào thành phố.',
            }]}
            ai = RetryAlignmentAI()

            changed, count = pipeline._repair_srt_alignment_final_pass(
                ai, report, max_blocks=1
            )

            self.assertTrue(changed)
            self.assertEqual(count, 1)
            self.assertEqual(ai.calls, 2)
            self.assertIn(
                'lập tức điều tra',
                pipeline.ai_package['script_blocks'][0]['text'],
            )


if __name__ == '__main__':
    unittest.main()
