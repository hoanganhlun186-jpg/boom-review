import os
import sys
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.parallel_jobs import ordered_parallel
from core.clip_assembler import build_clip_concat_list
from core.full_pipeline import FullPipeline
from utils.helpers import FFmpegUtils


class ParallelTests(unittest.TestCase):
    def test_concurrent_work_preserves_order(self):
        barrier = threading.Barrier(3)
        progress = []
        def work(number):
            barrier.wait(timeout=3)
            return number*2
        self.assertEqual(ordered_parallel(work,[3,1,2],3,lambda done,total:progress.append((done,total))),[6,2,4])
        self.assertEqual(progress,[(1,3),(2,3),(3,3)])

    def test_worker_failure_is_not_silently_omitted(self):
        def work(number):
            if number==2: raise RuntimeError('failed clip')
            return number
        with self.assertRaisesRegex(RuntimeError,'failed clip'):
            ordered_parallel(work,[1,2,3],2)

    def test_real_serial_and_parallel_media_match(self):
        ffmpeg = FFmpegUtils.ffmpeg_executable()
        def run(args):
            return subprocess.run([ffmpeg,'-y','-v','error',*args],
                check=True,**FFmpegUtils.subprocess_kwargs(capture_output=True)).stdout
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source.mp4'
            run(['-f','lavfi','-i','testsrc2=s=160x120:r=25:d=4','-c:v','libx264',str(source)])
            frames = []
            images = []
            for workers in (1,3):
                output = root/str(workers)
                output.mkdir()
                with patch.dict(os.environ,{'AUTORECAP_CLIP_WORKERS':str(workers),
                                             'AUTORECAP_KEYFRAME_WORKERS':str(workers)}):
                    path = build_clip_concat_list([(0,1,0),(1,2,0),(2,3,0)],3,str(source),str(output),1,ffmpeg,clips_are_voice_aligned=True)
                    self.assertTrue(path)
                    frames.append(run(['-i',path,'-map','0:v','-f','rawvideo','-pix_fmt','rgb24','-']))
                    pipeline = FullPipeline(str(source),str(output))
                    pipeline.scenes = [dict(scene_id=i,start_s=i,end_s=i+1) for i in range(3)]
                    pipeline.metadata = {'duration_s':4}
                    self.assertTrue(pipeline.step_keyframes())
                    self.assertEqual([Path(p).name for p in pipeline.keyframes],['kf_0000.jpg','kf_0001.jpg','kf_0002.jpg'])
                    images.append([Path(p).read_bytes() for p in pipeline.keyframes])
            self.assertEqual(frames[0],frames[1],'Decoded frames must match, including order and duration')
            self.assertGreater(len(frames[0]),0)
            self.assertEqual(images[0],images[1])
            failed = root/'failure'; failed.mkdir()
            with patch('core.clip_assembler.subprocess.run',return_value=subprocess.CompletedProcess([],1,stderr='fixture failure')):
                with self.assertRaisesRegex(RuntimeError,'cắt clip thất bại'):
                    build_clip_concat_list([(0,1,0),(1,2,0)],2,str(source),str(failed),2,ffmpeg,clips_are_voice_aligned=True)
            self.assertEqual(list(failed.iterdir()),[],'All workers finish before temporary files are removed')

if __name__=='__main__': unittest.main()
