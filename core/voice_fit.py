"""Shorten only overlong narration, with durable per-block retry limits."""
import hashlib
import json
import os
from pathlib import Path
from core.block_source_bounds import owned_source_ranges
from core.voice_speed import speed_voice
from core.voice_timing import VoiceTimingController
from core.narration_guard import invalid_narration


def fit_voices(blocks, segments, render_blocks, scenes, ffmpeg, probe,
               rewrite, synthesize, save, log):
    by_id = {int(s['block_id']): s for s in segments}
    changed = False
    for block in blocks:
        bid = int(block['block_id'])
        seg = by_id[bid]
        if invalid_narration(seg.get('text') or block.get('text')):
            raise RuntimeError(f'Block {bid}: voice chứa câu từ chối của AI; cần sửa lời và tạo lại riêng block này trước khi ghép.')
        try:
            ranges = owned_source_ranges(block, render_blocks, scenes)
        except ValueError as error:
            raise ValueError(f'Block {bid}: {error}') from error
        budget = sum(b-a for a,b in ranges)
        if budget <= 0:
            raise RuntimeError(f'Block {bid}: không có thời lượng hình hợp lệ.')
        speed_voice(seg, ffmpeg, 1.2)
        duration = probe(seg['audio_path'])
        if duration <= 0:
            raise RuntimeError(f'Block {bid}: không đọc được audio.')
        while duration > budget + .04:
            current = str(seg.get('text') or block.get('text') or '').strip()
            identity = hashlib.sha256(json.dumps([current, budget, seg.get('original_audio_path')]).encode()).hexdigest()
            state = seg.setdefault('duration_repair', {})
            if state.get('identity') != identity:
                state.clear()
                state.update(identity=identity, attempts=0)
            pending = state.get('pending')
            if not pending:
                if state['attempts'] >= 3:
                    raise RuntimeError(f'Block {bid}: đã rút lời 3 lần nhưng vẫn dài; giữ kết quả để chỉnh tiếp, không gọi API thêm.')
                words = max(1, int(len(current.split()) * budget / duration * .90))
                log(f'   ✂️ Block {bid}: voice {duration:.2f}s / hình {budget:.2f}s; rút lời còn tối đa {words} từ, lần {state["attempts"]+1}/3')
                state['attempts'] += 1
                save()
                candidate = rewrite(current, words, block).strip()
                if invalid_narration(candidate) or len(candidate.split()) >= len(current.split()) or len(candidate.split()) > words:
                    save()
                    continue
                name = hashlib.sha256(candidate.encode()).hexdigest()[:16]
                path = str(Path(seg['original_audio_path']).parent / f'fit_{bid}_{name}.mp3')
                pending = dict(text=candidate, path=path)
                state['pending'] = pending
                save()
            path = pending['path']
            if not os.path.exists(path) or probe(path) <= 0:
                synthesize(pending['text'], path)
            if probe(path) <= 0:
                raise RuntimeError(f'Block {bid}: audio lời rút gọn chưa hợp lệ; giữ voice cũ.')
            seg['original_audio_path'] = path
            seg['audio_path'] = path
            seg['text'] = pending['text']
            seg['text_preview'] = pending['text'][:80]
            seg['text_hash'] = VoiceTimingController.text_hash(pending['text'])
            block.setdefault('text_before_duration_repair', block.get('text'))
            block['text'] = pending['text']
            state.pop('pending', None)
            state['identity'] = hashlib.sha256(json.dumps([seg['text'], budget, path]).encode()).hexdigest()
            speed_voice(seg, ffmpeg, 1.2)
            duration = probe(seg['audio_path'])
            seg['audio_duration'] = duration
            changed = True
            save()
    return changed
