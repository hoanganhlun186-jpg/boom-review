import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.resume_job import find_resume_job
from core.full_pipeline import FullPipeline

class ResumeTests(unittest.TestCase):
    def test_matching_failed_job_not_same_filename_or_newer_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            video=str(Path(folder)/'a'/'Tap_01.mp4')
            for name,source,failed,completed,time in [('right',video,'VOICE_CONCAT',[],1),('new',video,'',[],9),('done',video,'',['RENDER_FINAL'],20),('wrong',str(Path(folder)/'b'/'Tap_01.mp4'),'VOICE_CONCAT',[],30)]:
                root=Path(folder)/name;root.mkdir()
                (root/'.autorecap_job.json').write_text(json.dumps({'source_video':source}))
                (root/'pipeline_state.json').write_text(json.dumps(dict(failed_step=failed,completed_steps=completed,updated_at=time)))
            self.assertEqual(find_resume_job([folder],video),str(Path(folder)/'right'))
            self.assertEqual(find_resume_job([folder],str(Path(folder)/'missing.mp4')),'')

    def test_existing_voice_never_calls_ai_or_tts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); audio=root/'voice.wav';audio.write_bytes(b'audio')
            for name,data in [('pipeline_state.json',{'failed_step':'VOICE_CONCAT'}),('ai_package.json',{'script_blocks':[{'block_id':1,'text':'test'}]}),('voice_segments.json',[{'block_id':1,'audio_path':str(audio)}])]:
                (root/name).write_text(json.dumps(data))
            p=FullPipeline('',folder,progress_callback=lambda m:None)
            calls=[]
            with patch.object(p,'_media_duration',return_value=1),patch.object(p,'_resume_skip'), \
                 patch.object(p,'step_ai_full',side_effect=AssertionError('AI must not run')), \
                 patch.object(p,'step_voice_segments',side_effect=AssertionError('TTS must not run')), \
                 patch.object(p,'_with_retry',side_effect=lambda fn,name:calls.append(name) or True):
                self.assertTrue(p.resume_saved())
            self.assertEqual(calls,['VOICE_CONCAT','VOICE_SRT','RENDER_FINAL'])

if __name__=='__main__':unittest.main()
