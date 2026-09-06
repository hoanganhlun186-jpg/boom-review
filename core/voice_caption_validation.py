"""Validate captions against synthesized audio, not source-movie evidence."""
import math
import re


def segment_window(segment, render_timeline=False):
    if render_timeline:
        start = segment.get('start_in_video',segment.get('actual_voice_start',0))
        duration = segment.get('target_duration') or segment.get('actual_voice_duration') or segment.get('audio_duration')
    else:
        start = segment.get('actual_voice_start',segment.get('start_in_video',0))
        duration = segment.get('actual_voice_duration') or segment.get('audio_duration') or segment.get('target_duration')
    return float(start),float(start)+float(duration or 0)


def validate_captions(cues, segments, render_timeline=False):
    errors = []
    if not segments or len(cues)!=len(segments):
        errors.append('Số câu sub không khớp số đoạn voice')
    previous_end = 0
    normalize = lambda text: ' '.join(str(text or '').split())
    for index,(cue,segment) in enumerate(zip(cues,segments),1):
        start,end,text = cue
        expected_start,expected_end = segment_window(segment,render_timeline)
        if not all(math.isfinite(v) for v in (start,end,expected_start,expected_end)) or start<0 or end<=start:
            errors.append(f'Block {index}: thời gian không hợp lệ')
        if abs(start-expected_start)>.02 or abs(end-expected_end)>.02:
            errors.append(f'Block {index}: sub lệch mốc voice')
        if start<previous_end-.02:
            errors.append(f'Block {index}: sub chồng thời gian')
        if not normalize(text) or normalize(text)!=normalize(re.sub(r'<[^>]+>','',str(segment.get('text','')))):
            errors.append(f'Block {index}: sub khác lời đã tạo voice')
        previous_end = end
    return errors
