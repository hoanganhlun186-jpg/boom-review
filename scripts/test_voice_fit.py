from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.voice_fit import fit_voices

class FitTests(unittest.TestCase):
    def test_automatic_review_repairs_duplicates_and_scene_errors(self):
        from core.full_pipeline import FullPipeline
        with tempfile.TemporaryDirectory() as folder:
            pipeline=FullPipeline('',folder,progress_callback=lambda m:None)
            blocks=[dict(block_id=1,text='Câu kể.',script_editor_locked=True)]
            pipeline.ai_package=dict(script_blocks=blocks,script_editor_synced=True)
            with patch('engine.ai_engine.AIEngine'), \
                 patch.object(pipeline,'_rewrite_duplicate_sentences_for_tts',side_effect=[(blocks,1,1),(blocks,0,0)]) as duplicates, \
                 patch.object(pipeline,'_annotate_srt_alignment',side_effect=[(pipeline.ai_package,{'error_count':1}),(pipeline.ai_package,{'error_count':0})]), \
                 patch.object(pipeline,'_repair_srt_alignment_final_pass') as repair:
                self.assertTrue(pipeline.prepare_automatic_review())
                self.assertEqual(duplicates.call_count,2)
                repair.assert_called_once()
                self.assertTrue(pipeline.ai_package['script_editor_synced'])
                self.assertTrue(blocks[0]['script_editor_locked'])
                self.assertTrue(pipeline.prepare_automatic_review())
                self.assertEqual(duplicates.call_count,2)

    def test_only_long_block_rewritten_and_resume_does_not_charge(self):
        with tempfile.TemporaryDirectory() as folder:
            blocks=[dict(block_id=i,text='Một hai ba bốn năm sáu.',original_start=(i-1)*10,original_end=i*10) for i in (1,2)]
            segments=[dict(block_id=i,text=blocks[i-1]['text'],audio_path=str(Path(folder)/f'{i}.wav')) for i in (1,2)]
            for seg in segments: Path(seg['audio_path']).write_bytes(b'audio')
            def speed(seg,*args): seg.setdefault('original_audio_path',seg['audio_path'])
            def probe(path): return 15 if Path(path).name=='2.wav' else 8
            synth=Mock(side_effect=lambda text,path:Path(path).write_bytes(b'new'))
            rewrite=Mock(return_value='Một hai ba.')
            with patch('core.voice_fit.speed_voice',side_effect=speed):
                for _ in range(2):
                    fit_voices(blocks,segments,[],[],'ffmpeg',probe,rewrite,synth,lambda:None,lambda m:None)
            self.assertEqual(synth.call_count,1)
            self.assertEqual(rewrite.call_count,1)
            self.assertEqual(blocks[0]['text'],'Một hai ba bốn năm sáu.')
            self.assertEqual(blocks[1]['text'],'Một hai ba.')

    def test_invalid_rewrites_have_durable_limit(self):
        seg=dict(block_id=1,text='Một hai ba.',audio_path='original.wav')
        block=dict(block_id=1,text=seg['text'],original_start=0,original_end=1)
        def speed(seg,*args): seg.setdefault('original_audio_path',seg['audio_path'])
        rewrite=Mock(return_value=seg['text']); synth=Mock()
        with patch('core.voice_fit.speed_voice',side_effect=speed):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    fit_voices([block],[seg],[],[],'ffmpeg',lambda p:10,rewrite,synth,lambda:None,lambda m:None)
        self.assertEqual(rewrite.call_count,3)
        synth.assert_not_called()

if __name__=='__main__':unittest.main()
