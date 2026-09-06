import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from core.full_pipeline import FullPipeline

class SceneProgressTests(unittest.TestCase):
    def test_progress_tracks_frames_and_rejects_early_decode_end(self):
        with tempfile.TemporaryDirectory() as folder:
            for count in (10,4):
                pipeline = FullPipeline('',folder)
                messages = []
                pipeline._log = messages.append
                capture = Mock()
                capture.get.side_effect = lambda prop: 10
                capture.read.side_effect = [(True,np.zeros((8,8,3),dtype=np.uint8))]*count+[(False,None)]
                with patch('cv2.VideoCapture',return_value=capture),patch('core.full_pipeline.time.monotonic',side_effect=range(100)):
                    if count==10:
                        pipeline._detect_scenes_cv2()
                        self.assertTrue(any('[SCENE_PROGRESS] 90|' in m for m in messages))
                        self.assertIn('[SCENE_PROGRESS] 100|',messages[-1])
                    else:
                        with self.assertRaisesRegex(RuntimeError,'dừng sớm'):
                            pipeline._detect_scenes_cv2()
                        self.assertFalse(any('[SCENE_PROGRESS] 100|' in m for m in messages))
                capture.release.assert_called_once()

if __name__=='__main__': unittest.main()
