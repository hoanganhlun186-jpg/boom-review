"""Fit overlong narration to its visual budget without redoing unaffected blocks."""
import hashlib
import json
import math
import os
from pathlib import Path

from core.block_source_bounds import owned_source_ranges
from core.narration_guard import invalid_narration
from core.voice_speed import speed_voice
from core.voice_timing import VoiceTimingController


BASE_PLAYBACK_SPEED = 1.2
DEFAULT_MAX_PLAYBACK_SPEED = 1.4
MAX_REWRITE_ATTEMPTS = 5
MAX_LOCAL_SHORTEN_ATTEMPTS = 3
MAX_WORD_OVERRUN = 5


def _max_playback_speed():
    try:
        value = float(os.environ.get("AUTORECAP_MAX_BLOCK_VOICE_SPEED", DEFAULT_MAX_PLAYBACK_SPEED))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_PLAYBACK_SPEED
    if not math.isfinite(value):
        value = DEFAULT_MAX_PLAYBACK_SPEED
    return max(BASE_PLAYBACK_SPEED, min(value, DEFAULT_MAX_PLAYBACK_SPEED))


def _hard_cap_text(text, max_words):
    """Last-resort shortening that always makes progress without inventing facts."""
    words = str(text or '').strip().split()
    limit = max(1, min(int(max_words or 1), len(words)))
    if limit >= len(words):
        return ' '.join(words)
    shortened = ' '.join(words[:limit]).rstrip(' ,;:-.!?')
    return shortened + '.' if shortened else ''


def _fit_with_speed(seg, bid, budget, duration, ffmpeg, probe, save, log):
    """Speed up only the overlong block after text shortening."""
    required = BASE_PLAYBACK_SPEED * duration / budget * 1.01
    speed = min(_max_playback_speed(), max(BASE_PLAYBACK_SPEED, required))
    log(
        f"   💨 Block {bid}: voice vẫn dài sau khi rút; "
        f"tăng tốc riêng block lên {speed:.2f}x"
    )
    speed_voice(seg, ffmpeg, speed)
    fitted_duration = probe(seg['audio_path'])
    if fitted_duration <= 0:
        raise RuntimeError(f'Block {bid}: không đọc được audio sau khi tăng tốc.')
    seg['audio_duration'] = fitted_duration
    seg.setdefault('duration_repair', {})['fallback_playback_speed'] = round(speed, 4)
    save()
    if fitted_duration > budget + .04:
        raise RuntimeError(
            f'[VOICE_FIT_LIMIT] Block {bid}: voice {fitted_duration:.2f}s vẫn dài hơn '
            f'hình {budget:.2f}s ở tốc độ tối đa {speed:.2f}x.'
        )
    return fitted_duration


def fit_voices(blocks, segments, render_blocks, scenes, ffmpeg, probe,
               rewrite, synthesize, save, log):
    by_id = {int(s['block_id']): s for s in segments}
    changed = False
    for block in blocks:
        bid = int(block['block_id'])
        seg = by_id[bid]
        if invalid_narration(seg.get('text') or block.get('text')):
            raise RuntimeError(
                f'Block {bid}: voice chứa câu từ chối của AI; '
                'cần sửa lời và tạo lại riêng block này trước khi ghép.'
            )
        try:
            ranges = owned_source_ranges(block, render_blocks, scenes)
        except ValueError as error:
            raise ValueError(f'Block {bid}: {error}') from error
        budget = sum(b - a for a, b in ranges)
        if budget <= 0:
            raise RuntimeError(f'Block {bid}: không có thời lượng hình hợp lệ.')

        speed_voice(seg, ffmpeg, BASE_PLAYBACK_SPEED)
        duration = probe(seg['audio_path'])
        if duration <= 0:
            raise RuntimeError(f'Block {bid}: không đọc được audio.')

        while duration > budget + .04:
            current = str(seg.get('text') or block.get('text') or '').strip()
            identity = hashlib.sha256(
                json.dumps([current, budget, seg.get('original_audio_path')]).encode()
            ).hexdigest()
            state = seg.setdefault('duration_repair', {})
            if state.get('identity') != identity:
                state.clear()
                state.update(identity=identity, attempts=0)

            pending = state.get('pending')
            if not pending:
                attempt = int(state.get('attempts') or 0)
                if attempt >= MAX_REWRITE_ATTEMPTS:
                    local_attempt = int(state.get('local_shorten_attempts') or 0)
                    if local_attempt >= MAX_LOCAL_SHORTEN_ATTEMPTS:
                        duration = _fit_with_speed(
                            seg, bid, budget, duration, ffmpeg, probe, save, log
                        )
                        changed = True
                        break
                    safe_words = max(
                        1, int(len(current.split()) * budget / duration * .82)
                    )
                    candidate = _hard_cap_text(current, safe_words)
                    if not candidate or len(candidate.split()) >= len(current.split()):
                        duration = _fit_with_speed(
                            seg, bid, budget, duration, ffmpeg, probe, save, log
                        )
                        changed = True
                        break
                    state['local_shorten_attempts'] = local_attempt + 1
                    log(
                        f'   Block {bid}: AI sai giới hạn '
                        f'{MAX_REWRITE_ATTEMPTS} lần; tự rút còn '
                        f'{len(candidate.split())} từ '
                        f'({local_attempt + 1}/{MAX_LOCAL_SHORTEN_ATTEMPTS}).'
                    )
                    name = hashlib.sha256(candidate.encode()).hexdigest()[:16]
                    path = str(Path(seg['original_audio_path']).parent / f'fit_{bid}_{name}.mp3')
                    pending = dict(text=candidate, path=path, local_fallback=True)
                    state['pending'] = pending
                    save()

                if not pending:
                    target_words = max(1, int(len(current.split()) * budget / duration * .85))
                    minimum_words = 1
                    maximum_words = target_words + MAX_WORD_OVERRUN
                    log(
                        f'   Block {bid}: voice {duration:.2f}s / visual {budget:.2f}s; '
                        f'target at most {maximum_words} words (prefer {target_words}), '
                        f'attempt {attempt + 1}/{MAX_REWRITE_ATTEMPTS}'
                    )
                    state['attempts'] = attempt + 1
                    save()
                    candidate = rewrite(current, target_words, block).strip()
                    candidate_words = len(candidate.split())
                    if (
                        invalid_narration(candidate)
                        or candidate_words >= len(current.split())
                        or candidate_words > maximum_words
                    ):
                        log(
                            f'   Block {bid}: rejected rewrite '
                            f'({candidate_words} words; max {maximum_words}).'
                        )
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
                raise RuntimeError(
                    f'Block {bid}: audio lời rút gọn chưa hợp lệ; giữ voice cũ.'
                )
            seg['original_audio_path'] = path
            seg['audio_path'] = path
            seg['text'] = pending['text']
            seg['text_preview'] = pending['text'][:80]
            seg['text_hash'] = VoiceTimingController.text_hash(pending['text'])
            block.setdefault('text_before_duration_repair', block.get('text'))
            block['text'] = pending['text']
            state.pop('pending', None)
            state['identity'] = hashlib.sha256(
                json.dumps([seg['text'], budget, path]).encode()
            ).hexdigest()
            speed_voice(seg, ffmpeg, BASE_PLAYBACK_SPEED)
            duration = probe(seg['audio_path'])
            seg['audio_duration'] = duration
            changed = True
            save()

            # Measure each accepted rewrite. If it remains long, shorten the new
            # text again; playback speed is only the final limited fallback.
            if duration <= budget + .04:
                break
    return changed
