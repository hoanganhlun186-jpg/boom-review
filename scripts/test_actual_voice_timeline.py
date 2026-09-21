import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.full_pipeline import FullPipeline
from utils.helpers import FFmpegUtils
from core.clip_assembler import assemble_clip_based_video

def generate(path, duration, video=False):
    source = 'color=c=blue:s=160x120:r=25' if video else 'sine=frequency=440:sample_rate=44100'
    subprocess.run([FFmpegUtils.ffmpeg_executable(),'-y','-v','error','-f','lavfi','-i',source,
                    '-t',str(duration),str(path)],check=True,**FFmpegUtils.subprocess_kwargs(capture_output=True))

class ActualVoiceTests(unittest.TestCase):
    def test_old_editor_tts_accepts_duration_difference_without_speedup(self):
        from engine.ai_engine import AIEngine
        with tempfile.TemporaryDirectory() as folder:
            p=FullPipeline('',folder,progress_callback=lambda m:None)
            p.ai_package={'script_editor_synced':True,'script_blocks':[
                dict(block_id=1,text='Anh ấy trở về nhà và gặp lại người thân.',duration_hint_seconds=1,_tts_rate='+50%'),
                dict(block_id=2,text='Cô gái đứng bên cửa, chờ đợi trong im lặng.',duration_hint_seconds=8,_tts_rate='-20%')]}
            rates=[]
            p.render_blocks=[dict(block_id=i,original_start=i,original_end=i+1) for i in range(1,4)]
            async def speak(self,text,path,voice=None,rate=None,**kwargs):
                rates.append(rate)
                generate(path,2.0)
                return voice
            p.ai_package['automatic_review']=True
            with patch.object(AIEngine,'__init__',return_value=None), patch.object(AIEngine,'text_to_speech',speak), \
                    patch.object(p,'_annotate_srt_alignment',side_effect=lambda package,*args,**kwargs:(package,{})), \
                    patch.object(p,'_annotate_market_readiness',side_effect=lambda package,*args,**kwargs:(package,{})), \
                    patch.object(p,'_normalize_script_blocks_to_render_blocks',side_effect=AssertionError('Must keep narration blocks')):
                self.assertTrue(p.step_voice_segments(),p.steps['VOICE_SEGMENTS'].error)
            self.assertEqual(rates,['+0%','+0%'])
            self.assertTrue(all(abs(s['audio_duration']-2)<.15 for s in p.voice_segments))
            self.assertTrue(all(s['timing_status']=='recap2_scene_anchored' for s in p.voice_segments))

    def test_old_editor_blocks_concat_follow_audio_not_estimate(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'AUTORECAP_RECAP2_BEAT_MODE':'0','AUTORECAP_CONTINUOUS_VOICE':'0'}):
            p=FullPipeline('',folder)
            p.cut_duration=40
            p.ai_package={'script_editor_synced':True,'script_blocks':[{'block_id':2,'text':'Câu đầu.','original_start':0,'original_end':10},{'block_id':1,'text':'Câu sau.','original_start':10,'original_end':20}]}
            p.voice_segments=[]
            for bid,duration,start in [(1,1.4,20),(2,2.3,5)]:
                path=Path(folder)/f'{bid}.wav'; generate(path,duration)
                p.voice_segments.append(dict(block_id=bid,audio_path=str(path),audio_duration=duration,start_in_video=start,target_duration=10,text='Câu đầu.' if bid==2 else 'Câu sau.'))
            self.assertTrue(p.step_voice_concat(),p.steps['VOICE_CONCAT'].error)
            self.assertEqual([s['block_id'] for s in p.voice_segments],[2,1])
            self.assertEqual(p.voice_segments[0]['actual_voice_start'],0)
            self.assertAlmostEqual(p.voice_segments[1]['actual_voice_start'],2.3/1.2,delta=.06)
            self.assertAlmostEqual(p._media_duration(p.concat_audio_path),3.7/1.2,delta=.1)
            with patch.dict(os.environ,{'AUTORECAP_VOICE_SRT_RENDER_TIMELINE':'1'}), \
                    patch.object(p,'_annotate_srt_alignment',side_effect=lambda package,*args,**kwargs:(package,{})), \
                    patch.object(p,'_annotate_market_readiness',side_effect=lambda package,*args,**kwargs:(package,{})):
                self.assertTrue(p.step_voice_srt(),p.steps['VOICE_SRT'].error)
            from core.preview_design import read_cues
            cues=read_cues(p.voice_srt_path)
            self.assertAlmostEqual(cues[0][0],0,delta=.01)
            self.assertAlmostEqual(cues[-1][1],3.7/1.2,delta=.1)

    def test_missing_block_still_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            p=FullPipeline('',folder)
            p.ai_package={'script_blocks':[{'block_id':1},{'block_id':2}]}
            path=Path(folder)/'1.wav'; generate(path,1)
            p.voice_segments=[dict(block_id=1,audio_path=str(path))]
            self.assertFalse(p.step_voice_concat())

    def test_real_final_matches_short_and_long_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); video=root/'source.mp4'; generate(video,8,True)
            blocks=[]; voices=[]
            for bid,duration in [(1,.9),(2,2.1)]:
                path=root/f'{bid}.wav';generate(path,duration)
                blocks.append(dict(block_id=bid,text='Anh ấy trở về nhà.',original_start=(bid-1)*4,original_end=bid*4,duration_hint_seconds=4))
                voices.append(dict(block_id=bid,audio_path=str(path)))
            output=root/'final.mp4'
            self.assertTrue(assemble_clip_based_video(str(video),blocks,[],voices,[],[],str(output),
                            ffmpeg_bin=FFmpegUtils.ffmpeg_executable(),ffprobe_bin=FFmpegUtils.ffprobe_executable(),log=lambda m:None))
            self.assertAlmostEqual(FullPipeline(str(video),folder)._media_duration(str(output)),3,delta=.2)

    def test_long_voice_extends_cut_inside_same_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); video=root/'source.mp4'; generate(video,8,True)
            audio=root/'voice.wav'; generate(audio,3)
            blocks=[dict(block_id=1,text='Anh ấy trở về nhà.',original_start=0,original_end=1)]
            output=root/'final.mp4'
            messages=[]
            with self.assertRaisesRegex(ValueError,'Block 1'):
                assemble_clip_based_video(str(video),blocks,[],
                    [dict(block_id=1,audio_path=str(audio))],[dict(start=0,end=4)],[],str(output),
                    ffmpeg_bin=FFmpegUtils.ffmpeg_executable(),ffprobe_bin=FFmpegUtils.ffprobe_executable(),log=messages.append)
            self.assertFalse(output.exists())

    def test_long_voice_cannot_borrow_next_block_or_freeze_last_block(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); video=root/'source.mp4'; generate(video,8,True)
            audio=root/'voice.wav';generate(audio,3)
            blocks=[dict(block_id=1,text='Anh ấy trở về nhà.',original_start=0,original_end=1)]
            with self.assertRaisesRegex(ValueError,'Block 1.*Rút gọn'):
                assemble_clip_based_video(str(video),blocks,[],[dict(block_id=1,audio_path=str(audio))],[],[],str(root/'final.mp4'),
                    ffmpeg_bin=FFmpegUtils.ffmpeg_executable(),ffprobe_bin=FFmpegUtils.ffprobe_executable(),log=lambda m:None)
            self.assertFalse((root/'final.mp4').exists())

if __name__=='__main__':unittest.main()
