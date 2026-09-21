"""Apply the requested playback speed without changing original TTS audio."""
import json
import os
from pathlib import Path
import subprocess


def speed_voice(segment, ffmpeg, speed=1.2):
    from utils.helpers import FFmpegUtils
    original = Path(segment.get('original_audio_path') or segment['audio_path'])
    target = original.with_name(original.stem + '.speed120.wav')
    marker = Path(str(target) + '.source.json')
    stat = original.stat()
    signature = [str(original.resolve()), stat.st_size, stat.st_mtime_ns, speed]
    try:
        cached = target.exists() and json.loads(marker.read_text()) == signature
    except (OSError, ValueError):
        cached = False
    if not cached:
        staged = target.with_name(target.stem + '.tmp.wav')
        result = subprocess.run([ffmpeg, '-y', '-v', 'error', '-i', str(original),
            '-af', f'atempo={speed}', '-ar', '44100', '-ac', '1', str(staged)],
            **FFmpegUtils.subprocess_kwargs(capture_output=True, timeout=180))
        if result.returncode or not staged.exists() or staged.stat().st_size < 100:
            raise RuntimeError('Không tăng tốc được audio; giữ nguyên voice gốc.')
        os.replace(staged, target)
        timing = Path(str(original) + '.timing.json')
        output_timing = Path(str(target) + '.timing.json')
        if timing.exists():
            entries = json.loads(timing.read_text(encoding='utf-8'))
            for entry in entries:
                for key in ('start', 'end', 'duration', 'offset'):
                    if isinstance(entry.get(key), (int, float)):
                        entry[key] /= speed
            output_timing.write_text(json.dumps(entries, ensure_ascii=False), encoding='utf-8')
        else:
            output_timing.unlink(missing_ok=True)
        marker.write_text(json.dumps(signature), encoding='utf-8')
    segment['original_audio_path'] = str(original)
    segment['audio_path'] = str(target)
    segment['playback_speed'] = speed

