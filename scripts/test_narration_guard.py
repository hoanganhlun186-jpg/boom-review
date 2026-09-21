import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.narration_guard import invalid_narration

class GuardTests(unittest.TestCase):
    def test_refusals(self):
        for text in ['Tôi không thể trợ giúp về điều đó, vì tôi chỉ là một mô hình ngôn ngữ.',
                     'Toi chi la mot mo hinh ngon ngu.', 'As an AI, I cannot assist with that.', '']:
            self.assertTrue(invalid_narration(text))
    def test_story_is_accepted(self):
        self.assertFalse(invalid_narration('Cô không thể về nhà vì đường đã bị chặn.'))

if __name__=='__main__': unittest.main()
