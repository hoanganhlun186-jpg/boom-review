import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.voice_caption_validation import validate_captions, segment_window
from core.preview_design import read_cues
from core.full_pipeline import FullPipeline


class VoiceCaptionTests(unittest.TestCase):
    def test_repair_stops_after_two_failed_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('',folder)
            pipeline.ai_package = {'script_blocks':[{'block_id':1,'text':'Lời kể'}]}
            pipeline.voice_segments = [{'block_id':1,'text':'Lời kể'}]
            pipeline.render_blocks = [{'block_id':1}]
            report = {'blocks':[{'block_id':1,'errors':['missing_cut_evidence_anchor']}]}
            with patch('core.srt_alignment.SrtAlignmentValidator.annotate_package',return_value=({},report)), \
                 patch('core.ai_engine.AIEngine'), \
                 patch.object(pipeline,'_repair_srt_alignment_final_pass',return_value=(True,1)), \
                 patch.object(pipeline,'step_voice_segments',return_value=True) as voice, \
                 patch.object(pipeline,'step_voice_concat',return_value=True):
                self.assertFalse(pipeline._repair_recorded_voice_alignment())
                self.assertEqual(voice.call_count,2)

    def test_auto_repair_regenerates_voice_and_rechecks_scene_anchors(self):
        source = Path(__file__).resolve().parents[1]/'exports/Tap_01_20260905_193232'
        if not source.exists(): self.skipTest('Local job fixture unavailable')
        with tempfile.TemporaryDirectory() as folder:
            pipeline = FullPipeline('',folder)
            pipeline.ai_package = json.loads((source/'ai_package.json').read_text(encoding='utf-8'))
            pipeline.render_blocks = json.loads((source/'render_blocks.json').read_text(encoding='utf-8'))
            pipeline.voice_segments = json.loads((source/'voice_segments.json').read_text(encoding='utf-8'))
            before = {seg['block_id']:seg['text'] for seg in pipeline.voice_segments}
            from core.srt_alignment import SrtAlignmentValidator
            import copy
            expected_package = copy.deepcopy(pipeline.ai_package)
            for block in expected_package['script_blocks']:
                if block['block_id'] in before: block['text'] = before[block['block_id']]
            _,expected_report = SrtAlignmentValidator.annotate_package(expected_package,pipeline.render_blocks)
            expected_ids = {item['block_id'] for item in expected_report['blocks'] if item['errors']}
            repaired_ids = []
            def repair(ai,report,max_blocks):
                by_id = {block['block_id']:block for block in pipeline.ai_package['script_blocks']}
                for item in report['blocks']:
                    if item['errors']:
                        bid = item['block_id']
                        repaired_ids.append(bid)
                        by_id[bid]['text'] = item['required_anchor']+'.'
                return True,len(repaired_ids)
            def regenerate():
                for seg in pipeline.voice_segments:
                    seg['text'] = pipeline._automatic_voice_repair_texts[seg['block_id']]
                return True
            with patch('core.ai_engine.AIEngine'), \
                 patch.object(pipeline,'_repair_srt_alignment_final_pass',side_effect=repair), \
                 patch.object(pipeline,'step_voice_segments',side_effect=regenerate) as voice, \
                 patch.object(pipeline,'step_voice_concat',return_value=True) as concat:
                self.assertTrue(pipeline._repair_recorded_voice_alignment())
                self.assertEqual(set(repaired_ids),expected_ids)
                self.assertEqual(voice.call_count,1 if expected_ids else 0)
                self.assertEqual(concat.call_count,voice.call_count)
            for seg in pipeline.voice_segments:
                if seg['block_id'] not in expected_ids:
                    self.assertEqual(seg['text'],before[seg['block_id']])

    def test_reject_wrong_text_time_and_overlap(self):
        segments = [dict(text='Câu một',actual_voice_start=0,actual_voice_duration=2),
                    dict(text='Câu hai',actual_voice_start=2,actual_voice_duration=3)]
        self.assertEqual(validate_captions([(0,2,'Câu một'),(2,5,'Câu hai')],segments),[])
        for cues in [[(0,2,'Sai chữ'),(2,5,'Câu hai')],[(0,3,'Câu một'),(2,5,'Câu hai')],[]]:
            self.assertTrue(validate_captions(cues,segments))

    def test_failed_job_regenerates_from_recorded_voice(self):
        source = Path(__file__).resolve().parents[1]/'exports/Tap_01_20260905_193232'
        if not source.exists(): self.skipTest('Local job fixture unavailable')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=False):
            os.environ.pop('AUTORECAP_VOICE_SRT_RENDER_TIMELINE',None)
            pipeline = FullPipeline('',folder)
            pipeline.ai_package = json.loads((source/'ai_package.json').read_text(encoding='utf-8'))
            pipeline.render_blocks = json.loads((source/'render_blocks.json').read_text(encoding='utf-8'))
            pipeline.voice_segments = json.loads((source/'voice_segments.json').read_text(encoding='utf-8'))
            before = json.dumps(pipeline.voice_segments,ensure_ascii=False)
            self.assertTrue(pipeline.step_voice_srt())
            self.assertEqual(json.dumps(pipeline.voice_segments,ensure_ascii=False),before)
            cues = read_cues(pipeline.voice_srt_path)
            self.assertEqual(len(cues),len(pipeline.voice_segments))
            render_timeline = not pipeline.ai_package.get('voice_concat_continuous')
            self.assertEqual(validate_captions(cues,pipeline.voice_segments,render_timeline),[])
            self.assertAlmostEqual(cues[-1][1],segment_window(pipeline.voice_segments[-1],render_timeline)[1],places=2)

if __name__=='__main__': unittest.main()
