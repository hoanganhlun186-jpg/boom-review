import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.full_pipeline import FullPipeline


class TranscriptTransitionTests(unittest.TestCase):
    def test_intro_detection_does_not_call_ai_by_default(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=False):
            os.environ.pop('AUTORECAP_INTRO_OUTRO',None)
            pipeline = FullPipeline('',folder,gemini_api_key='test-placeholder')
            pipeline.transcript_srt = str(Path(folder)/'transcript.srt')
            pipeline.metadata = {'duration_s':171.3}
            with patch('core.recap_engine.detect_intro_outro_boundaries',side_effect=AssertionError('Must not wait on AI')):
                pipeline._detect_and_store_intro_outro()
            self.assertEqual(pipeline.metadata,{'duration_s':171.3})

if __name__=='__main__': unittest.main()
