"""Read-only regression against supplied job JSON; no media/API calls."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.voice_fit import fit_voices
p=Path(sys.argv[1])
package=json.loads((p/'ai_package.json').read_text(encoding='utf-8-sig'))
rows=json.loads((p/'render_blocks.json').read_text(encoding='utf-8-sig'))
segments=json.loads((p/'voice_segments.json').read_text(encoding='utf-8-sig'))
def forbidden(*args):raise AssertionError('Unexpected API request')
with patch('core.voice_fit.speed_voice'):
    fit_voices(package['script_blocks'],segments,rows,[],'unused',lambda path:1,
               forbidden,forbidden,lambda:None,print)
print('PASS: mapping path inside voice fitting; real audio duration/render not tested')
