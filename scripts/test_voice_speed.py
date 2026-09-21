from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.voice_speed import speed_voice
from scripts.test_actual_voice_timeline import generate
from core.full_pipeline import FullPipeline
from utils.helpers import FFmpegUtils

class SpeedTests(unittest.TestCase):
    def test_speed_and_resume_preserve_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'voice.wav'
            generate(source,3)
            original=source.read_bytes()
            segment={'audio_path':str(source)}
            speed_voice(segment,FFmpegUtils.ffmpeg_executable())
            output=Path(segment['audio_path'])
            stamp=output.stat().st_mtime_ns
            self.assertAlmostEqual(FullPipeline('',folder)._media_duration(str(output)),2.5,delta=.06)
            speed_voice(segment,FFmpegUtils.ffmpeg_executable())
            self.assertEqual(stamp,output.stat().st_mtime_ns)
            self.assertEqual(original,source.read_bytes())

if __name__=='__main__': unittest.main()
