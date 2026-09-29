from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.voice_fit import fit_voices, _max_playback_speed


class FitTests(unittest.TestCase):
    def test_block_speed_is_hard_capped_at_1_4(self):
        with patch.dict('os.environ', {'AUTORECAP_MAX_BLOCK_VOICE_SPEED': '1.6'}):
            self.assertEqual(_max_playback_speed(), 1.4)

    def test_stubborn_duplicate_fallback_preserves_anchor_text(self):
        from core.full_pipeline import FullPipeline
        sentence = 'Lâm Động phát hiện bí mật trong căn phòng rồi lập tức quay lại báo tin.'
        blocks = [
            dict(block_id=1, text=sentence),
            dict(block_id=2, text=sentence),
            dict(block_id=3, text=sentence),
        ]
        self.assertEqual(
            FullPipeline._count_duplicate_sentences_for_tts(blocks), 2)
        repaired, changed = FullPipeline._force_unique_duplicate_sentences(blocks)
        self.assertEqual(changed, 2)
        self.assertEqual(
            FullPipeline._count_duplicate_sentences_for_tts(repaired), 0)
        self.assertTrue(all('lâm động phát hiện bí mật' in b['text'].lower()
                            for b in repaired))

    def test_automatic_review_detects_script_editor_near_duplicates(self):
        from core.full_pipeline import FullPipeline
        first = (
            'Không yên ổn chút nào, Tô Nhã và Lưu Phi Phi bị cướp biển '
            'bắt cóc trong lúc con thuyền trôi giữa biển.'
        )
        near = (
            'Không yên ổn chút nào, Tô Nhã và Lưu Phi Phi bị cướp biển '
            'bắt cóc khi con thuyền trôi giữa biển.'
        )
        blocks = [dict(block_id=5, text=first), dict(block_id=6, text=near)]
        matches = FullPipeline._duplicate_narration_matches(blocks)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['block_id'], 6)
        self.assertEqual(matches[0]['duplicate_of_block'], 5)
        self.assertEqual(matches[0]['reason'], 'near_duplicate')
        self.assertEqual(FullPipeline._count_duplicate_sentences_for_tts(blocks), 1)
        repaired, changed = FullPipeline._force_unique_duplicate_sentences(blocks)
        self.assertEqual(changed, 1)
        self.assertEqual(FullPipeline._count_duplicate_sentences_for_tts(repaired), 0)

    def test_chapter_budget_package_is_not_rejected_by_legacy_block_cap(self):
        from core.full_pipeline import FullPipeline
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda m: None)
            pipeline.cut_duration = 4154
            pipeline.ai_package = {
                'script_block_strategy': 'chapter_budget_story_segment',
                'script_blocks': [
                    dict(block_id=index, text='Lời kể hợp lệ.')
                    for index in range(1, 265)
                ],
            }
            self.assertTrue(pipeline._uses_modern_segmented_script())
            self.assertGreater(
                len(pipeline.ai_package['script_blocks']),
                pipeline._max_ai_blocks(pipeline.cut_duration) * 2,
            )

    def test_automatic_review_repairs_duplicates_and_scene_errors(self):
        from core.full_pipeline import FullPipeline
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda m: None)
            blocks = [dict(block_id=1, text='Câu kể.', script_editor_locked=True)]
            repaired_blocks = [
                dict(block_id=1, text='Câu kể đã được viết lại rõ ràng và không còn trùng lặp.')
            ]
            pipeline.ai_package = dict(script_blocks=blocks, script_editor_synced=True)
            with patch('engine.ai_engine.AIEngine'), \
                 patch.object(pipeline, '_count_duplicate_sentences_for_tts', side_effect=[1, 0, 0]) as duplicate_count, \
                 patch.object(pipeline, '_rewrite_duplicate_sentences_for_tts', return_value=(repaired_blocks, 1, 0)) as duplicates, \
                 patch.object(pipeline, '_annotate_srt_alignment', side_effect=[
                     (pipeline.ai_package, {'error_count': 1}),
                     (pipeline.ai_package, {'error_count': 0}),
                     (pipeline.ai_package, {'error_count': 0}),
                     (pipeline.ai_package, {'error_count': 0}),
                     (pipeline.ai_package, {'error_count': 0}),
                 ]), \
                 patch.object(pipeline, '_repair_srt_alignment_final_pass') as repair:
                self.assertTrue(pipeline.prepare_automatic_review())
                self.assertEqual(duplicates.call_count, 1)
                self.assertEqual(duplicate_count.call_count, 3)
                repair.assert_called_once()
                self.assertTrue(pipeline.ai_package['script_editor_synced'])
                self.assertTrue(pipeline.ai_package['script_blocks'][0]['script_editor_locked'])
                self.assertTrue(pipeline.prepare_automatic_review())
                self.assertEqual(duplicates.call_count, 1)

    def test_automatic_review_continues_when_only_alignment_warnings_remain(self):
        from core.full_pipeline import FullPipeline
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda m: None)
            blocks = [dict(block_id=1, text='Nhân vật trở về nhà sau cuộc hành trình.')]
            pipeline.ai_package = dict(script_blocks=blocks)
            with patch('engine.ai_engine.AIEngine'), \
                 patch.object(
                     pipeline,
                     '_rewrite_duplicate_sentences_for_tts',
                     return_value=(blocks, 0, 2),
                 ) as duplicates, \
                 patch.object(
                     pipeline,
                     '_annotate_srt_alignment',
                     side_effect=lambda package, *_: (package, {'error_count': 1, 'block_count': 40}),
                 ), \
                 patch.object(pipeline, '_repair_srt_alignment_final_pass'), \
                 patch.object(pipeline, '_count_under_target_story_blocks', return_value=0):
                self.assertTrue(pipeline.prepare_automatic_review())
            self.assertEqual(duplicates.call_count, 0)
            self.assertTrue(pipeline.ai_package['script_editor_synced'])
            self.assertEqual(
                pipeline.ai_package['automatic_review_warnings']['srt_alignment_error_count'], 1)
            self.assertEqual(pipeline.ai_package['voice_source'], 'automatic_review_with_warnings')

    def test_automatic_review_does_not_bypass_broad_alignment_failure(self):
        from core.full_pipeline import FullPipeline
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('', folder, progress_callback=lambda m: None)
            pipeline.ai_package = {
                'script_blocks': [
                    dict(block_id=index, text=f'Nhân vật tiếp tục sự kiện riêng thứ {index}.')
                    for index in range(1, 41)
                ]
            }
            with patch('engine.ai_engine.AIEngine'), \
                 patch.object(
                     pipeline, '_annotate_srt_alignment',
                     side_effect=lambda package, *_: (
                         package, {'error_count': 8, 'block_count': 40}),
                 ), \
                 patch.object(pipeline, '_repair_srt_alignment_final_pass'):
                self.assertFalse(pipeline.prepare_automatic_review())
            self.assertIn('vượt ngưỡng cảnh báo nhỏ', pipeline.script_review_error)
    def test_only_long_block_rewritten_and_resume_does_not_charge(self):
        with tempfile.TemporaryDirectory() as folder:
            blocks = [dict(block_id=i, text='Một hai ba bốn năm sáu.', original_start=(i-1)*10, original_end=i*10) for i in (1, 2)]
            segments = [dict(block_id=i, text=blocks[i-1]['text'], audio_path=str(Path(folder)/f'{i}.wav')) for i in (1, 2)]
            for seg in segments:
                Path(seg['audio_path']).write_bytes(b'audio')
            def speed(seg, *args):
                seg.setdefault('original_audio_path', seg['audio_path'])
            def probe(path):
                return 15 if Path(path).name == '2.wav' else 8
            synth = Mock(side_effect=lambda text, path: Path(path).write_bytes(b'new'))
            rewrite = Mock(return_value='Một hai ba.')
            with patch('core.voice_fit.speed_voice', side_effect=speed):
                for _ in range(2):
                    fit_voices(blocks, segments, [], [], 'ffmpeg', probe, rewrite, synth, lambda: None, lambda m: None)
            self.assertEqual(synth.call_count, 1)
            self.assertEqual(rewrite.call_count, 1)
            self.assertEqual(blocks[0]['text'], 'Một hai ba bốn năm sáu.')
            self.assertEqual(blocks[1]['text'], 'Một hai ba.')

    def test_long_rewrite_is_shortened_again_before_speeding(self):
        original_text = ' '.join(f'word{i}' for i in range(49))
        first_text = ' '.join(f'first{i}' for i in range(39))
        final_text = ' '.join(f'final{i}' for i in range(24))
        seg = dict(block_id=10, text=original_text, audio_path='original.wav')
        block = dict(block_id=10, text=original_text, original_start=0, original_end=6.5)
        natural_durations = {'original.wav': 14.232}
        rewrites = Mock(side_effect=[first_text, final_text])

        def speed(item, unused_ffmpeg, value):
            source = item.get('original_audio_path') or item['audio_path']
            item['original_audio_path'] = source
            item['audio_path'] = f'{source}|{value:.4f}'
            item['playback_speed'] = value

        def probe(path):
            source, value = path.rsplit('|', 1) if '|' in path else (path, '1')
            return natural_durations.get(source, 0) / float(value)

        def synth(text, path):
            Path(path).write_bytes(b'new')
            natural_durations[path] = 8.4 if text == first_text else 5.4

        with patch('core.voice_fit.speed_voice', side_effect=speed):
            fit_voices([block], [seg], [], [], 'ffmpeg', probe, rewrites,
                       synth, lambda: None, lambda m: None)

        self.assertEqual(rewrites.call_count, 2)
        self.assertEqual(seg['text'], final_text)
        self.assertEqual(seg['playback_speed'], 1.2)
        self.assertLessEqual(seg['audio_duration'], 6.54)

    def test_block_162_uses_local_shortening_after_invalid_ai_rewrites(self):
        original_text = ' '.join(f'word{i}' for i in range(106))
        seg = dict(block_id=162, text=original_text, audio_path='original.wav')
        block = dict(block_id=162, text=original_text, original_start=0, original_end=17.5)
        natural_durations = {'original.wav': 32.436}
        logs = []
        rewrites = Mock(return_value=original_text)

        def speed(item, unused_ffmpeg, value):
            source = item.get('original_audio_path') or item['audio_path']
            item['original_audio_path'] = source
            item['audio_path'] = f'{source}|{value:.4f}'
            item['playback_speed'] = value

        def probe(path):
            source, value = path.rsplit('|', 1) if '|' in path else (path, '1')
            return natural_durations.get(source, 0) / float(value)

        def synth(text, path):
            Path(path).write_bytes(b'new')
            natural_durations[path] = len(text.split()) * .25

        with patch('core.voice_fit.speed_voice', side_effect=speed):
            fit_voices([block], [seg], [], [], 'ffmpeg', probe, rewrites,
                       synth, lambda: None, logs.append)

        self.assertEqual(rewrites.call_count, 5)
        self.assertLess(len(seg['text'].split()), len(original_text.split()))
        self.assertEqual(seg['playback_speed'], 1.2)
        self.assertLessEqual(seg['audio_duration'], 17.54)
        self.assertTrue(any('tự rút còn' in message for message in logs))


if __name__ == '__main__':
    unittest.main()
