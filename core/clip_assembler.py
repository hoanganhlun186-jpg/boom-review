"""
AutoRecapPro V2 - Clip Assembler (RECAP2.0 style)
===================================================
Thay vì ép/cắt từng TTS để fit block duration,
module này làm ngược lại như RECAP2.0:

  TTS tự nhiên ở rate=0% → đo actual_tts_duration
  → tìm/cắt clips từ video gốc vừa khít tts_duration
  → ghép video clips + voice = segment hoàn chỉnh

Flow:
  script_blocks[{text, scene_ids, srt_anchor}]
  + source_video
  + render_blocks[{scene_ids, original_start, original_end}]
  ↓
  per-block:
    1. TTS text → raw_audio ở tốc độ đã duyệt
    2. actual_dur = probe(raw_audio)
    3. collect candidate scenes từ scene_ids + lookahead
    4. trim/concat clips = actual_dur
    5. mux clip + voice → block_N.mp4
  ↓
  concat tất cả block_N.mp4 → final video

Key differences từ AutoRecapPro cũ:
- Không dùng cut_video cố định — mỗi block có clip riêng
- Không cắt mất câu, không tăng tốc toàn bài để ép ngân sách review
- Không silent gap — video liên tục bằng cách đủ clip
- Dialogue center: ưu tiên trim clip vào đúng đoạn có thoại
"""

import os
import json
import subprocess
import tempfile
import re
import asyncio
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable

from utils.helpers import FFmpegUtils

logger = logging.getLogger(__name__)

# Số từ/giây đọc tự nhiên tiếng Việt ở rate=0%
WORDS_PER_SEC_NATURAL = 2.5
# Tối thiểu duration mỗi clip block (giây)
MIN_BLOCK_DURATION = 1.5
# Tối đa lookahead scenes sau scene chính
MAX_LOOKAHEAD_SCENES = 12
# Tolerance khi trim clip (giây)
TRIM_TOLERANCE = 0.1

_SEMANTIC_STOPWORDS = {
    "các", "của", "cho", "được", "đang", "đến", "đây", "điều", "giữa",
    "không", "khi", "lại", "một", "này", "những", "người", "nhưng",
    "phải", "sau", "thành", "theo", "thì", "trong", "trước", "từng",
    "vẫn", "vào", "việc", "với", "xuất", "hiện", "cùng", "ngay",
}


def _narration_semantic_keywords(text: str) -> set:
    words = set(
        re.findall(r"[\w\u00C0-\u024F\u1E00-\u1EFF]{3,}", (text or "").lower())
    )
    return {word for word in words if word not in _SEMANTIC_STOPWORDS}


def _looks_like_branding(block: Dict) -> bool:
    """Reject logos, release cards and credits as narration footage."""
    text = " ".join(
        str(block.get(key) or "")
        for key in (
            "visual_anchor", "visual_hint", "summary", "srt_anchor",
            "dialogue_text", "cut_visible_srt",
        )
    ).lower()
    markers = (
        "iqiyi", "logo", "studio logo", "giấy phép phát hành",
        "thông tin cấp phép", "credits", "end credits", "credit roll",
        "quảng cáo", "advertisement", "1xbet", "789bet",
    )
    return any(marker in text for marker in markers)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _probe_duration(path: str, ffprobe_bin: str = "ffprobe") -> float:
    """Đo duration file media bằng ffprobe."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            **FFmpegUtils.subprocess_kwargs(
                capture_output=True, text=True, timeout=10
            ),
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _estimate_tts_duration(text: str) -> float:
    """Ước tính TTS duration từ số từ (như RECAP2.0 estimate_tts_duration)."""
    words = len((text or "").split())
    return words / WORDS_PER_SEC_NATURAL


def _find_forward_unused_range(
    start: float,
    duration: float,
    content_end: float,
    used_ranges: List[Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    """Find moving footage after start without reusing an earlier clip."""
    cursor = max(0.0, float(start or 0.0))
    needed = max(0.0, float(duration or 0.0))
    occupied = sorted(
        (max(0.0, float(s)), min(content_end, float(e)))
        for s, e in (used_ranges or [])
        if float(e) > float(s)
    )
    for used_start, used_end in occupied:
        if used_end <= cursor:
            continue
        if used_start - cursor >= needed:
            return cursor, cursor + needed
        cursor = max(cursor, used_end)
    if content_end - cursor >= needed:
        return cursor, cursor + needed
    return None


def _calculate_dialogue_center(
    scene_start: float,
    scene_end: float,
    srt_segments: List[Dict],
    narration_keywords: set,
) -> float:
    """Tính vị trí trung tâm ngữ nghĩa của clip (như RECAP2.0).
    
    Ưu tiên đoạn có thoại overlap với narration keywords.
    Fallback: arithmetic mean của tất cả subtitles.
    """
    if not srt_segments:
        return (scene_start + scene_end) / 2.0

    # Lọc subtitles trong cảnh
    in_scene = [
        s for s in srt_segments
        if float(s.get("start_seconds") or s.get("start") or 0) >= scene_start - 0.5
        and float(s.get("start_seconds") or s.get("start") or 0) <= scene_end + 0.5
    ]
    if not in_scene:
        return (scene_start + scene_end) / 2.0

    # Tìm subtitles overlap với narration keywords
    semantic_matches = []
    for s in in_scene:
        text = str(s.get("text", "")).lower()
        words = set(re.findall(r"[\w\u00C0-\u024F\u1E00-\u1EFF]{3,}", text))
        if words & narration_keywords:
            t = float(s.get("start_seconds") or s.get("start") or 0)
            semantic_matches.append(t)

    if semantic_matches:
        return sum(semantic_matches) / len(semantic_matches)

    # Fallback: center của tất cả subtitles trong cảnh
    times = [float(s.get("start_seconds") or s.get("start") or 0) for s in in_scene]
    return sum(times) / len(times)


def _calculate_dialogue_anchor(
    scene_start: float,
    scene_end: float,
    srt_segments: List[Dict],
    narration_keywords: set,
) -> Tuple[float, bool]:
    """Return the earliest semantic SRT anchor and whether it is reliable.

    Voice starts at the beginning of each assembled block, so placing a
    matching subtitle at the middle of a long clip makes narration run ahead
    of the picture. The earliest matching cue is the correct visual anchor.
    """
    if not srt_segments or not narration_keywords:
        return scene_start, False

    matches: List[float] = []
    for segment in srt_segments:
        try:
            start = float(segment.get("start_seconds") or segment.get("start") or 0)
        except Exception:
            continue
        if start < scene_start - 0.5 or start > scene_end + 0.5:
            continue
        text = str(segment.get("text", "")).lower()
        words = set(re.findall(r"[\w\u00C0-\u024F\u1E00-\u1EFF]{3,}", text))
        if words & narration_keywords:
            matches.append(start)
    if matches:
        return min(matches), True
    return scene_start, False


def _align_candidates_to_narration(
    clips: List[Tuple[float, float, float]],
    srt_segments: List[Dict],
    narration_text: str,
) -> List[Tuple[float, float, float]]:
    """Drop unrelated leading clips when a later clip has real SRT evidence."""
    if len(clips or []) < 2:
        return clips
    keywords = _narration_semantic_keywords(narration_text)
    if not keywords:
        return clips
    for index, (start, end, _score) in enumerate(clips):
        _anchor, reliable = _calculate_dialogue_anchor(
            float(start), float(end), srt_segments or [], keywords
        )
        if reliable:
            if index <= 0:
                return clips
            # Keep at most one second of the previous shot as a natural cut,
            # then show the evidence scene as narration begins.
            prev_start, prev_end, prev_score = clips[index - 1]
            lead = (max(float(prev_start), float(prev_end) - 1.0), float(prev_end), prev_score)
            return [lead] + clips[index:]
    return clips


def _split_narration_units(text: str) -> List[str]:
    units = [
        part.strip()
        for part in re.split(r"(?<=[.!?…])\s+", (text or "").strip())
        if part.strip()
    ]
    if len(units) == 1 and len(units[0].split()) > 28:
        clauses = [part.strip() for part in re.split(r"(?<=[,;:])\s+", units[0]) if part.strip()]
        if len(clauses) > 1:
            units = clauses
    return units or ([text.strip()] if (text or "").strip() else [])


def _load_word_timings(audio_path: str) -> List[Dict]:
    sidecar = str(audio_path or "") + ".timing.json"
    try:
        with open(sidecar, "r", encoding="utf-8") as timing_file:
            data = json.load(timing_file)
    except Exception:
        return []
    timings = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            timings.append({
                "offset": max(0.0, float(entry.get("offset") or 0.0)),
                "duration": max(0.0, float(entry.get("duration") or 0.0)),
                "text": str(entry.get("text") or ""),
            })
        except Exception:
            pass
    return sorted(timings, key=lambda item: item["offset"])


def _unit_durations_from_word_timings(
    units: List[str],
    tts_duration: float,
    word_timings: List[Dict],
) -> List[float]:
    if not units or not word_timings:
        return []
    starts = [0.0]
    word_cursor = 0
    for unit in units[:-1]:
        word_cursor += max(1, len(re.findall(r"[\w\u00C0-\u024F\u1E00-\u1EFF]+", unit)))
        if word_cursor >= len(word_timings):
            return []
        starts.append(max(starts[-1], float(word_timings[word_cursor].get("offset") or 0.0)))
    durations = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else float(tts_duration)
        durations.append(max(TRIM_TOLERANCE, end - start))
    total = sum(durations)
    if total <= 0:
        return []
    # Bù sai số encoder/probe rất nhỏ để tổng clip luôn bằng audio thật.
    durations[-1] += float(tts_duration) - total
    return durations


def _semantic_anchor_for_range(
    start: float,
    end: float,
    srt_segments: List[Dict],
    keywords: set,
) -> Tuple[float, int]:
    best_time = float(start)
    best_overlap = 0
    for segment in srt_segments or []:
        try:
            cue_start = float(segment.get("start_seconds") or segment.get("start") or 0)
        except Exception:
            continue
        if cue_start < start - 0.5 or cue_start > end + 0.5:
            continue
        cue_words = _narration_semantic_keywords(str(segment.get("text", "")))
        overlap = len(keywords & cue_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_time = cue_start
    return best_time, best_overlap


def _merge_adjacent_ranges(
    ranges: List[Tuple[float, float, float]],
) -> List[Tuple[float, float, float]]:
    merged: List[Tuple[float, float, float]] = []
    for start, end, score in ranges:
        if end <= start + 0.05:
            continue
        if merged and abs(merged[-1][1] - start) <= 0.08:
            prev_start, _prev_end, prev_score = merged[-1]
            merged[-1] = (prev_start, end, max(prev_score, score))
        else:
            merged.append((start, end, score))
    return merged


def _available_duration_from(
    clips: List[Tuple[float, float, float]],
    index: int,
    cursor: float,
) -> float:
    total = 0.0
    for candidate_index in range(index, len(clips)):
        start, end, _score = clips[candidate_index]
        take_start = max(float(start), float(cursor)) if candidate_index == index else float(start)
        total += max(0.0, float(end) - take_start)
    return total


def _tail_start_for_duration(
    clips: List[Tuple[float, float, float]],
    start_index: int,
    start_cursor: float,
    needed_duration: float,
) -> Tuple[int, float]:
    """Find the latest forward-only start that still has enough footage."""
    ranges: List[Tuple[int, float, float]] = []
    for candidate_index in range(start_index, len(clips)):
        start, end, _score = clips[candidate_index]
        usable_start = max(float(start), float(start_cursor)) if candidate_index == start_index else float(start)
        if end > usable_start + TRIM_TOLERANCE:
            ranges.append((candidate_index, usable_start, float(end)))
    remaining = max(0.0, float(needed_duration))
    for candidate_index, start, end in reversed(ranges):
        span = end - start
        if remaining <= span + TRIM_TOLERANCE:
            return candidate_index, max(start, end - remaining)
        remaining -= span
    return start_index, start_cursor


def _plan_clips_on_tts_timeline(
    clips: List[Tuple[float, float, float]],
    tts_duration: float,
    srt_segments: List[Dict],
    narration_text: str,
    word_timings: List[Dict] = None,
) -> Tuple[List[Tuple[float, float, float]], int]:
    """Map narration sentences onto source clips using actual TTS duration.

    Khi Edge TTS có WordBoundary, thời lượng từng câu lấy từ timeline phát âm
    thật. Các voice engine không có boundary mới fallback theo số từ. Mỗi câu
    tìm evidence SRT mạnh nhất theo chiều tiến của timeline nguồn.
    """
    clips = sorted((clips or []), key=lambda item: item[0])
    units = _split_narration_units(narration_text)
    if not clips or not units or tts_duration <= 0:
        return clips, 0

    durations = _unit_durations_from_word_timings(
        units, tts_duration, word_timings or []
    )
    if not durations:
        weights = [max(1, len(unit.split())) for unit in units]
        total_weight = float(sum(weights) or 1)
        durations = [tts_duration * weight / total_weight for weight in weights]

    planned: List[Tuple[float, float, float]] = []
    clip_index = 0
    source_cursor = float(clips[0][0])
    anchored_units = 0

    for unit_index, (unit, unit_duration) in enumerate(zip(units, durations)):
        keywords = _narration_semantic_keywords(unit)
        best_index = clip_index
        best_anchor = source_cursor
        best_overlap = 0
        # Look forward only. This prevents later narration from returning to
        # footage already used by an earlier sentence.
        for candidate_index in range(clip_index, min(len(clips), clip_index + 8)):
            start, end, _score = clips[candidate_index]
            anchor, overlap = _semantic_anchor_for_range(
                float(start), float(end), srt_segments or [], keywords
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = candidate_index
                best_anchor = anchor
        if best_overlap > 0:
            previous_index = clip_index
            previous_cursor = source_cursor
            start, end, _score = clips[best_index]
            anchored_cursor = max(float(start), min(float(end), best_anchor - 0.55))
            remaining_voice = sum(durations[unit_index:])
            available_after_anchor = _available_duration_from(
                clips, best_index, anchored_cursor
            )
            if available_after_anchor + TRIM_TOLERANCE < remaining_voice:
                # Evidence can sit near the end of the block. Start from the
                # latest earlier shot that still keeps this whole block's TTS
                # inside its own candidate footage.
                clip_index, source_cursor = _tail_start_for_duration(
                    clips,
                    previous_index,
                    previous_cursor,
                    remaining_voice,
                )
            else:
                clip_index = best_index
                if clip_index == previous_index:
                    source_cursor = max(float(previous_cursor), anchored_cursor)
                else:
                    source_cursor = anchored_cursor
            anchored_units += 1

        remaining = max(0.0, float(unit_duration))
        while remaining > TRIM_TOLERANCE and clip_index < len(clips):
            start, end, score = clips[clip_index]
            take_start = max(float(start), source_cursor)
            available = max(0.0, float(end) - take_start)
            if available <= TRIM_TOLERANCE:
                clip_index += 1
                if clip_index < len(clips):
                    source_cursor = float(clips[clip_index][0])
                continue
            take = min(available, remaining)
            planned.append((take_start, take_start + take, score))
            source_cursor = take_start + take
            remaining -= take
            if source_cursor >= float(end) - TRIM_TOLERANCE:
                clip_index += 1
                if clip_index < len(clips):
                    source_cursor = float(clips[clip_index][0])

    return _merge_adjacent_ranges(planned), anchored_units


def _trim_clip_around_center(
    center: float,
    duration: float,
    scene_start: float,
    scene_end: float,
) -> Tuple[float, float]:
    """Tính [start, end] của clip duration giây, căn giữa tại center,
    clamp trong [scene_start, scene_end]."""
    half = duration / 2.0
    clip_start = max(scene_start, center - half)
    clip_end = clip_start + duration
    if clip_end > scene_end:
        clip_end = scene_end
        clip_start = max(scene_start, clip_end - duration)
    return clip_start, clip_end


# ─────────────────────────────────────────────────────────────────
# Core: build clips for one block
# ─────────────────────────────────────────────────────────────────

def collect_candidate_clips(
    scene_ids: List[int],
    render_blocks: List[Dict],
    scenes: List[Dict],
    tts_duration: float,
    min_start: float = 0.0,
    used_ranges: List[Tuple[float, float]] = None,
    source_block_ids: List[int] = None,
) -> List[Tuple[float, float, float]]:
    """Thu thập danh sách (original_start, original_end, score) để fill tts_duration.
    
    Trả về list (orig_start, orig_end, score) đủ tổng = tts_duration.
    """
    # Build lookup: scene_id → (orig_start, orig_end, score)
    scene_map: Dict[int, Tuple[float, float, float]] = {}
    block_map: Dict[int, Tuple[float, float, float]] = {}
    source_blocks: Dict[int, Dict] = {}

    # Timestamp của scene detector là dữ liệu chi tiết nhất. Nạp trước để
    # render block không thể ghi đè mỗi scene_id bằng cùng một range rộng.
    for s in (scenes or []):
        if not isinstance(s, dict) or _looks_like_branding(s):
            continue
        try:
            sid = int(s.get("scene_id") or 0)
            scene_map[sid] = (
                float(s.get("start_s") or s.get("start") or 0),
                float(s.get("end_s") or s.get("end") or 0),
                float(s.get("cut_score") or s.get("smart_score") or 0),
            )
        except Exception:
            pass

    # Từ render_blocks — ưu tiên original_start/end (timestamp video GỐC)
    for rb in (render_blocks or []):
        if not isinstance(rb, dict):
            continue
        if _looks_like_branding(rb):
            continue
        sids = rb.get("scene_ids") or []
        if isinstance(sids, list):
            for sid in sids:
                try:
                    # original_start/end = timestamp trong video GỐC (chưa băm)
                    # ClipAssembler dùng video gốc nên cần dùng timestamp này
                    s_start = float(
                        rb.get("original_start")
                        or rb.get("src_start")
                        or rb.get("start_s")        # từ scenes list
                        or rb.get("start_in_final_video")  # fallback cuối
                        or 0
                    )
                    s_end = float(
                        rb.get("original_end")
                        or rb.get("src_end")
                        or (s_start + float(rb.get("duration") or 4))
                    )
                    score = float(rb.get("smart_score") or rb.get("cut_score") or 0)
                    scene_map.setdefault(int(sid), (s_start, s_end, score))
                except Exception:
                    pass
        try:
            s_start = float(
                rb.get("original_start")
                or rb.get("src_start")
                or rb.get("start_s")
                or rb.get("start_in_final_video")
                or 0
            )
            s_end = float(
                rb.get("original_end")
                or rb.get("src_end")
                or (s_start + float(rb.get("duration") or 4))
            )
            score = float(rb.get("smart_score") or rb.get("cut_score") or 0)
            for alt_key in (rb.get("block_id"), rb.get("book_id")):
                try:
                    key = int(alt_key)
                    block_map[key] = (s_start, s_end, score)
                    source_blocks[key] = rb
                except Exception:
                    pass
        except Exception:
            pass

    # source_block_ids point to the fine-grained chronological cuts created
    # before AI_FULL. Prefer them over the broad review_clip range.
    clips: List[Tuple[float, float, float]] = []
    source_ids: List[int] = []
    for value in (source_block_ids or []):
        try:
            source_ids.append(int(value))
        except Exception:
            pass
    scene_keys: List[int] = []
    for value in (scene_ids or []):
        try:
            scene_keys.append(int(value))
        except Exception:
            pass

    selected_ids = source_ids or scene_keys
    if source_ids:
        # source_block_ids tham chiếu các render block thô. Phân rã từng block
        # về scene detector thật để câu thoại có thể neo đúng shot bên trong.
        for source_id in source_ids:
            source_block = source_blocks.get(source_id) or {}
            nested_ids = source_block.get("scene_ids") or []
            if not isinstance(nested_ids, list):
                nested_ids = re.findall(r"\d+", str(nested_ids))
            appended = False
            for nested_id in nested_ids:
                try:
                    scene_clip = scene_map.get(int(nested_id))
                except Exception:
                    scene_clip = None
                if scene_clip:
                    clips.append(scene_clip)
                    appended = True
            if not appended and source_id in block_map:
                clips.append(block_map[source_id])
    else:
        for scene_id in scene_keys:
            if scene_id in scene_map:
                clips.append(scene_map[scene_id])

    # Một scene có thể xuất hiện trong nhiều render block; chỉ giữ range đầu.
    clips = list(dict.fromkeys(clips))

    # Lookahead: nếu tổng chưa đủ → thêm cảnh tiếp theo
    if clips:
        last_sid = max(selected_ids) if selected_ids else 0
        for lookahead in range(1, MAX_LOOKAHEAD_SCENES + 1):
            total = sum(e - s for s, e, _ in clips)
            if total >= tts_duration - TRIM_TOLERANCE:
                break
            next_sid = last_sid + lookahead
            lookup = block_map if source_ids else scene_map
            if next_sid in lookup and next_sid not in selected_ids:
                clips.append(lookup[next_sid])

    # Sort theo original_start để giữ thứ tự thời gian
    clips.sort(key=lambda x: x[0])

    # RECAP2 flow: mỗi block tiến về phía trước theo timeline.
    # Nếu từng block tự lookahead độc lập, block sau có thể lấy lại đoạn video
    # mà block trước đã dùng, làm hình bị lặp dù voice đúng.
    if min_start > 0 or used_ranges:
        filtered: List[Tuple[float, float, float]] = []
        used_ranges = used_ranges or []
        for s, e, score in clips:
            s = max(float(s), float(min_start or 0.0))
            e = float(e)
            if e - s < 0.25:
                continue
            overlaps_used = False
            for us, ue in used_ranges:
                overlap = max(0.0, min(e, ue) - max(s, us))
                # Cho phép trùng tối đa 5% thời lượng đoạn đã dùng.
                # Vượt ngưỡng này thì bỏ candidate để block kế tiếp lấy hình mới.
                if overlap > min(e - s, ue - us) * 0.05:
                    overlaps_used = True
                    break
            if not overlaps_used:
                filtered.append((s, e, score))
        clips = filtered
    return clips


def build_clip_concat_list(
    clips: List[Tuple[float, float, float]],
    target_dur: float,
    source_video: str,
    output_dir: str,
    block_id: int,
    ffmpeg_bin: str,
    srt_segments: List[Dict] = None,
    narration_text: str = "",
    clips_are_voice_aligned: bool = False,
    progress_callback=None,
) -> Optional[str]:
    """Cắt clips từ source_video để tổng duration = target_dur.
    
    Trả về đường dẫn file video đã ghép, hoặc None nếu thất bại.
    """
    if not clips or not source_video or not os.path.exists(source_video):
        return None

    # Tính narration keywords để tìm dialogue center
    narration_kw = _narration_semantic_keywords(narration_text)

    tmp_clips = []
    remaining = target_dur
    jobs = []
    try:
        for (orig_start, orig_end, score) in clips:
            if remaining <= TRIM_TOLERANCE:
                break
            scene_dur = orig_end - orig_start
            if scene_dur <= 0:
                continue

            clip_need = min(remaining, scene_dur)
            if clips_are_voice_aligned:
                clip_start = orig_start
                clip_end = min(orig_end, clip_start + clip_need)
            else:
                # Compatibility path for packages without a TTS scene plan.
                anchor, has_semantic_anchor = _calculate_dialogue_anchor(
                    orig_start, orig_end, srt_segments or [], narration_kw
                )
                if has_semantic_anchor:
                    lead_in = min(0.75, max(0.25, clip_need * 0.05))
                    clip_start = max(orig_start, anchor - lead_in)
                    clip_end = min(orig_end, clip_start + clip_need)
                else:
                    clip_start = orig_start
                    clip_end = min(orig_end, clip_start + clip_need)
            actual_clip_dur = clip_end - clip_start

            if actual_clip_dur < 0.1:
                continue

            # Cắt clip từ source video
            tmp_handle = tempfile.NamedTemporaryFile(
                suffix=f"_clip_{block_id:04d}_{len(tmp_clips):02d}.mp4",
                dir=output_dir, delete=False
            )
            tmp_clip = tmp_handle.name
            tmp_handle.close()
            tmp_clips.append(tmp_clip)
            jobs.append((tmp_clip,clip_start,actual_clip_dur))
            remaining -= actual_clip_dur

        if remaining > TRIM_TOLERANCE:
            return None
        from core.parallel_jobs import ordered_parallel, worker_count
        workers = worker_count('AUTORECAP_CLIP_WORKERS',2)
        def cut(job):
            path,start,duration = job
            result = subprocess.run([
                ffmpeg_bin,'-y','-hide_banner','-loglevel','error',
                '-ss',f'{start:.4f}','-threads','1','-i',source_video,
                '-t',f'{duration:.4f}','-map','0:v:0','-an',
                '-vf','setpts=PTS-STARTPTS','-c:v','libx264',
                '-threads','2','-preset','veryfast','-crf','18',
                '-pix_fmt','yuv420p','-avoid_negative_ts','make_zero',path],
                **FFmpegUtils.subprocess_kwargs(capture_output=True,text=True,timeout=60))
            if result.returncode or not os.path.exists(path) or not os.path.getsize(path):
                raise RuntimeError(f'Block {block_id}: cắt clip thất bại: {result.stderr[-500:]}')
            return path
        def report(done,total):
            if progress_callback:
                progress_callback(f'[JOB_PROGRESS] {done*100//max(1,total)}|Block {block_id}: cắt xong {done}/{total} clip ({workers} luồng)')
        ordered_parallel(cut,jobs,workers,report)

        if not tmp_clips:
            return None

        # Ghép tất cả clips thành 1 file
        out_video = os.path.join(output_dir, f"block_video_{block_id:04d}.mp4")
        if len(tmp_clips) == 1:
            os.replace(tmp_clips[0], out_video)
            return out_video

        # Concat list
        lst_path = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", dir=output_dir, delete=False, encoding="utf-8"
        )
        for cp in tmp_clips:
            lst_path.write(f"file '{cp.replace(chr(92), '/')}'\n")
        lst_path.close()

        r = subprocess.run([
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", lst_path.name,
            "-c", "copy", out_video
        ], **FFmpegUtils.subprocess_kwargs(
            capture_output=True, text=True, timeout=120
        ))

        try: os.unlink(lst_path.name)
        except: pass

        if r.returncode == 0 and os.path.exists(out_video):
            return out_video
        return None

    finally:
        for cp in tmp_clips:
            try:
                if os.path.exists(cp): os.unlink(cp)
            except: pass


# ─────────────────────────────────────────────────────────────────
# Main: assemble_clip_based_video
# ─────────────────────────────────────────────────────────────────

def assemble_clip_based_video(
    source_video: str,
    script_blocks: List[Dict],
    render_blocks: List[Dict],
    voice_segments: List[Dict],
    scenes: List[Dict],
    srt_segments: List[Dict],
    output_path: str,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    bg_music_path: str = "",
    header_vf: str = "",
    target_duration_seconds: float = 0.0,
    log: Optional[Callable] = None,
) -> bool:
    """Ghép video theo style RECAP2.0: clip fit TTS, không atempo.
    
    Mỗi block:
      1. Lấy voice audio từ voice_segments (đã TTS tự nhiên)
      2. Đo actual_tts_duration
      3. Tìm/cắt clips từ source_video = actual_tts_duration
      4. Mux clip + voice → block_N.mp4
    Cuối: concat tất cả block_N.mp4 → output_path
    
    Returns True nếu thành công.
    """
    if log is None:
        log = lambda msg: logger.info(msg.strip())

    try:
        target_duration_seconds = max(0.0, float(target_duration_seconds or 0.0))
    except Exception:
        target_duration_seconds = 0.0

    if not source_video or not os.path.exists(source_video):
        log(f"❌ ClipAssembler: source_video không tồn tại: {source_video}")
        return False

    output_dir = os.path.dirname(output_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    blocks_dir = os.path.join(output_dir, "block_clips")
    Path(blocks_dir).mkdir(exist_ok=True)

    # Build voice_segment lookup: block_id → audio_path
    voice_map: Dict[int, Dict] = {}
    for vs in (voice_segments or []):
        if isinstance(vs, dict):
            try: voice_map[int(vs.get("block_id") or 0)] = vs
            except: pass

    # Build render_block lookup
    rb_map: Dict[int, Dict] = {}
    for idx, rb in enumerate(render_blocks or [], 1):
        if isinstance(rb, dict):
            for k in (rb.get("block_id"), rb.get("book_id"), idx):
                try: rb_map[int(k)] = rb
                except: pass

    # Voice đã được duyệt/TTS ở các bước trước là timeline có thẩm quyền.
    # Ngân sách review chỉ dùng để báo chênh lệch; bước render tuyệt đối không
    # fail, pad silence hay atempo toàn bộ voice để ép về con số ngân sách.
    voice_duration_map: Dict[int, float] = {}
    for i, block in enumerate(script_blocks or []):
        if not isinstance(block, dict):
            continue
        try:
            bid = int(block.get("block_id") or i + 1)
        except Exception:
            bid = i + 1
        vs = voice_map.get(bid) or {}
        audio_path = vs.get("audio_path", "")
        if not audio_path or not os.path.exists(audio_path):
            continue
        dur = _probe_duration(audio_path, ffprobe_bin)
        if dur <= 0:
            dur = _estimate_tts_duration(block.get("text", ""))
        voice_duration_map[bid] = max(MIN_BLOCK_DURATION, dur)

    total_voice_duration = sum(voice_duration_map.values())
    if target_duration_seconds > 0 and total_voice_duration > 0:
        delta = total_voice_duration - target_duration_seconds
        if abs(delta) > max(3.0, target_duration_seconds * 0.02):
            log(
                f"   ℹ️ Voice thật {total_voice_duration:.1f}s / ngân sách "
                f"{target_duration_seconds:.1f}s (chênh {delta:+.1f}s). "
                "Render theo voice thật, không ép tốc độ và không chèn im lặng."
            )
        else:
            log(
                f"   ✅ Voice thật {total_voice_duration:.1f}s gần ngân sách "
                f"{target_duration_seconds:.1f}s; render nguyên bản."
            )

    block_files: List[str] = []
    total = len(script_blocks)
    used_ranges: List[Tuple[float, float]] = []
    # timeline_cursor track vi tri CUOI trong source video da dung
    # (khong phai final video duration) de tranh bi filter out clips
    timeline_cursor = 0.0
    source_timeline_cursor = 0.0  # cursor tren source video (goc)
    assembled_duration = 0.0

    for i, block in enumerate(script_blocks or []):
        if not isinstance(block, dict):
            continue
        try: bid = int(block.get("block_id") or i + 1)
        except: bid = i + 1

        vs = voice_map.get(bid) or {}
        audio_path = vs.get("audio_path", "")
        if not audio_path or not os.path.exists(audio_path):
            log(f"   ⚠️ Block {bid}: không có voice audio → skip")
            continue

        # Đo actual TTS duration
        tts_dur = voice_duration_map.get(bid) or _probe_duration(audio_path, ffprobe_bin)
        if tts_dur <= 0:
            tts_dur = _estimate_tts_duration(block.get("text", ""))
        tts_dur = max(MIN_BLOCK_DURATION, tts_dur)

        # Lấy scene_ids
        rb = dict(rb_map.get(bid) or {})
        # In RECAP2/chapter-budget mode, script_blocks are the authoritative
        # story beats. render_blocks may still be raw scene cuts (e.g. 168
        # scenes), so rb_map[block_id] can point to the wrong original timestamp.
        # Always let the script block override source/timing fields.
        for _field in (
            "scene_ids", "source_block_ids",
            "original_start", "original_end",
            "source_start", "source_end",
            "start_s", "end_s",
            "start_in_final_video", "end_in_final_video",
            "duration", "duration_hint_seconds",
            "smart_score", "cut_score",
        ):
            if block.get(_field) is not None and block.get(_field) != "":
                rb[_field] = block.get(_field)
        source_block_ids = block.get("source_block_ids") or rb.get("source_block_ids") or []
        scene_ids = block.get("scene_ids") or rb.get("scene_ids") or []
        if isinstance(source_block_ids, str):
            source_block_ids = [int(x) for x in re.findall(r"\d+", source_block_ids)]
        if isinstance(scene_ids, str):
            scene_ids = [int(x) for x in re.findall(r"\d+", scene_ids)]
        if not scene_ids and rb.get("scene_id") is not None:
            try: scene_ids = [int(rb.get("scene_id"))]
            except: scene_ids = []

        # Ưu tiên các source block nhỏ theo timeline. review_clip thường là một
        # khoảng rất rộng chứa nhiều cảnh; dùng nó trước sẽ khiến semantic
        # anchor nằm giữa clip và voice đọc trước hình nhiều giây.
        candidates = collect_candidate_clips(
            scene_ids,
            render_blocks,
            scenes,
            tts_dur,
            min_start=source_timeline_cursor,  # dùng source cursor thay vì final cursor
            used_ranges=used_ranges,
            source_block_ids=source_block_ids,
        )

        # review_clip là fallback khi package cũ chưa có source_block_ids.
        review_clip = block.get("review_clip") if isinstance(block.get("review_clip"), dict) else {}
        try:
            rc_start = float(review_clip.get("start") if review_clip.get("start") is not None else -1)
            rc_end = float(review_clip.get("end") if review_clip.get("end") is not None else -1)
        except Exception:
            rc_start, rc_end = -1.0, -1.0
        if not candidates and rc_end > rc_start + 0.25 and not _looks_like_branding(block):
            candidates = [(max(float(source_timeline_cursor or 0.0), rc_start), rc_end, float(rb.get("smart_score") or rb.get("cut_score") or 0.0))]

        # A review_clip can be shorter than its natural narration. Fill the
        # remainder with following source scenes instead of asking FFmpeg to
        # hold the final frame until the audio finishes.
        candidate_seconds = sum(max(0.0, end - start) for start, end, _ in candidates)
        if candidates and candidate_seconds < tts_dur - TRIM_TOLERANCE:
            extra = collect_candidate_clips(
                scene_ids,
                render_blocks,
                scenes,
                tts_dur - candidate_seconds,
                min_start=max(float(source_timeline_cursor or 0.0), max(end for _start, end, _score in candidates)),
                used_ranges=used_ranges + [(start, end) for start, end, _score in candidates],
                source_block_ids=source_block_ids,
            )
            candidates.extend(extra)

        # Package rất cũ có thể chưa có cả source_block_ids lẫn review_clip.
        if not candidates:
            candidates = collect_candidate_clips(
                scene_ids, render_blocks, scenes, tts_dur,
                min_start=source_timeline_cursor,
                used_ranges=used_ranges,
                source_block_ids=source_block_ids,
            )
        if not candidates:
            # RECAP2 flow: AI_FULL chạy trước CLIP_FIND, nên một số chapter beat
            # có thể không còn scene_ids rõ ràng. Khi đó dùng source range của
            # chính render_block để cắt đúng cảnh thay vì fallback mù theo final time.
            try:
                source_start = float(
                    rb.get("original_start")
                    or rb.get("src_start")
                    or rb.get("start_s")
                    or rb.get("start_in_final_video")
                    or source_timeline_cursor
                )
                source_end = float(
                    rb.get("original_end")
                    or rb.get("src_end")
                    or (source_start + float(rb.get("duration") or tts_dur))
                )
            except Exception:
                source_start, source_end = source_timeline_cursor, source_timeline_cursor + tts_dur
            if _looks_like_branding(block) or _looks_like_branding(rb):
                source_start = float(source_timeline_cursor or 0.0)
                source_end = source_start + tts_dur
            source_start = max(float(source_timeline_cursor or 0.0), source_start)
            if source_end > source_start + 0.25:
                candidates = [(source_start, source_end, float(rb.get("smart_score") or rb.get("cut_score") or 0.0))]
                log(f"   ℹ️ Block {bid}: dùng source range của beat ({source_start:.1f}-{source_end:.1f}s)")
            else:
                log(f"   ⚠️ Block {bid}: không có candidate clips")
                s = max(source_timeline_cursor, float(rb.get("original_start") or rb.get("start_in_final_video") or 0))
                e = s + tts_dur
                candidates = [(s, e, 0.0)]

        voice_timeline_candidates, anchored_units = _plan_clips_on_tts_timeline(
            candidates,
            tts_dur,
            srt_segments or [],
            block.get("text", ""),
            _load_word_timings(audio_path),
        )
        voice_timeline_planned = bool(voice_timeline_candidates)
        if voice_timeline_planned:
            candidates = voice_timeline_candidates
        if anchored_units:
            log(
                f"   🎯 Block {bid}: neo {anchored_units} câu theo timeline TTS thật; "
                f"cảnh bắt đầu tại {candidates[0][0]:.1f}s"
            )

        # Last-resort moving footage. Never leave a block with fewer video
        # frames than voice audio, because players then display one frozen
        # frame for the remainder of the narration.
        candidate_seconds = sum(max(0.0, end - start) for start, end, _ in candidates)
        if candidate_seconds < tts_dur - TRIM_TOLERANCE:
            source_duration = _probe_duration(source_video, ffprobe_bin)
            content_end = max(0.0, source_duration - 12.0)
            missing = tts_dur - candidate_seconds
            fallback_start = max(
                float(source_timeline_cursor or 0.0),
                max((end for _start, end, _score in candidates), default=0.0),
            )
            occupied = used_ranges + [(start, end) for start, end, _score in candidates]
            unused = _find_forward_unused_range(fallback_start, missing, content_end, occupied)
            if unused:
                fallback_start, fallback_end = unused
                candidates.append((fallback_start, fallback_end, 0.0))
                log(f"   ℹ️ Block {bid}: bù {fallback_end - fallback_start:.1f}s cảnh chuyển động")
            else:
                log(
                    f"   ❌ Block {bid}: thiếu {missing:.1f}s cảnh mới sau "
                    f"{fallback_start:.1f}s; không tái dùng cảnh cũ"
                )
                return False

        # Build video clip
        clip_video = build_clip_concat_list(
            clips=candidates,
            target_dur=tts_dur,
            source_video=source_video,
            output_dir=blocks_dir,
            block_id=bid,
            ffmpeg_bin=ffmpeg_bin,
            srt_segments=srt_segments,
            narration_text=block.get("text", ""),
            clips_are_voice_aligned=voice_timeline_planned,
            progress_callback=log,
        )

        if not clip_video:
            log(f"   ⚠️ Block {bid}: không cắt được clip → skip")
            continue

        # Mux: clip_video + voice_audio → block_N.mp4
        block_out = os.path.join(blocks_dir, f"block_{bid:04d}.mp4")
        mux_cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", clip_video,
            "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
        ]
        mux_cmd += [
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{tts_dur:.4f}", block_out,
        ]
        r = subprocess.run(mux_cmd, **FFmpegUtils.subprocess_kwargs(
            capture_output=True, text=True, timeout=120
        ))

        if os.path.exists(clip_video):
            try: os.unlink(clip_video)
            except: pass

        if r.returncode == 0 and os.path.exists(block_out):
            block_files.append(block_out)
            assembled_duration += tts_dur
            consumed = 0.0
            for cs, ce, _score in candidates:
                if consumed >= tts_dur - TRIM_TOLERANCE:
                    break
                take = min(max(0.0, ce - cs), max(0.0, tts_dur - consumed))
                if take <= 0:
                    continue
                used_ranges.append((cs, cs + take))
                # timeline_cursor = final video position (assembled)
                timeline_cursor = assembled_duration
                # source_timeline_cursor = source video position (để block sau tìm tiếp)
                source_timeline_cursor = max(source_timeline_cursor, cs + take)
                consumed += take
            if target_duration_seconds > 0:
                log(
                    f"   ✅ Block {bid}/{total}: {tts_dur:.1f}s | "
                    f"{len(candidates)} clips | tổng voice thật {assembled_duration:.1f}s "
                    f"| ngân sách {target_duration_seconds:.1f}s"
                )
            else:
                log(f"   ✅ Block {bid}/{total}: {tts_dur:.1f}s | {len(candidates)} clips")
        else:
            log(f"   ⚠️ Block {bid}: mux lỗi: {r.stderr[:100]}")

    if not block_files:
        log("❌ ClipAssembler: không có block nào thành công")
        return False

    log(f"   🔗 Ghép {len(block_files)} blocks → {os.path.basename(output_path)}")

    # Concat tất cả blocks
    lst = tempfile.NamedTemporaryFile(
        mode="w", suffix="_final.txt", dir=output_dir, delete=False, encoding="utf-8"
    )
    for bf in block_files:
        lst.write(f"file '{bf.replace(chr(92), '/')}'\n")
    lst.close()

    # Build ffmpeg filter với optional title overlay và BGM
    inputs = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
              "-f", "concat", "-safe", "0", "-i", lst.name]

    if bg_music_path and os.path.exists(bg_music_path):
        inputs += ["-stream_loop", "-1", "-i", bg_music_path]
        # Keep narration in front. The music is attenuated first, then ducked
        # again whenever the voice side-chain is active.
        try:
            bgm_vol = float(os.environ.get("AUTORECAP_BGM_VOLUME", "0.06") or "0.06")
        except (TypeError, ValueError):
            bgm_vol = 0.06
        bgm_vol = max(0.01, min(0.20, bgm_vol))
        audio_mix = (
            f"[0:a]asetpts=PTS-STARTPTS,volume=1.25[voice];"
            f"[1:a]volume={bgm_vol:.3f}[music];"
            "[music][voice]sidechaincompress="
            "threshold=0.025:ratio=10:attack=20:release=500[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
        if header_vf:
            fc = (f"[0:v]setpts=PTS-STARTPTS,{header_vf}[vout];"
                  f"{audio_mix}")
            cmd = inputs + ["-filter_complex", fc,
                            "-map", "[vout]", "-map", "[aout]",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k", output_path]
        else:
            fc = audio_mix
            cmd = inputs + ["-filter_complex", fc,
                            "-map", "0:v:0", "-map", "[aout]",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k", output_path]
    elif header_vf:
        cmd = inputs + ["-vf", f"setpts=PTS-STARTPTS,{header_vf}",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-af", "asetpts=PTS-STARTPTS",
                        "-c:a", "aac", "-b:a", "192k", output_path]
    else:
        # Các block đã được chuẩn hóa H.264/AAC ở bước mux phía trên. Khi không
        # có BGM/overlay, chỉ cần remux thay vì encode lại toàn bộ phim.
        cmd = inputs + [
            "-map", "0:v:0", "-map", "0:a:0",
            "-c", "copy", "-movflags", "+faststart", output_path,
        ]

    needs_encode = bool(bg_music_path and os.path.exists(bg_music_path)) or bool(header_vf)
    concat_timeout = max(
        900,
        min(7200, int(assembled_duration * (3.0 if needs_encode else 0.35) + 300)),
    )

    try:
        r = subprocess.run(
            cmd,
            **FFmpegUtils.subprocess_kwargs(
                capture_output=True, text=True, timeout=concat_timeout
            ),
        )

        # Hiếm khi block có thông số codec khác nhau khiến stream-copy thất
        # bại. Khi đó mới encode lại với timeout theo thời lượng thật.
        if r.returncode != 0 and not needs_encode:
            log("   ⚠️ Ghép nhanh không tương thích → encode lại một lần")
            retry_cmd = inputs + [
                "-vf", "setpts=PTS-STARTPTS",
                "-af", "asetpts=PTS-STARTPTS",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", output_path,
            ]
            retry_timeout = max(1200, min(7200, int(assembled_duration * 3.0 + 300)))
            r = subprocess.run(
                retry_cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, timeout=retry_timeout
                ),
            )
    except subprocess.TimeoutExpired as timeout_error:
        log(
            "❌ ClipAssembler ghép cuối quá thời gian "
            f"({int(timeout_error.timeout)}s). Video tạm vẫn được giữ để kiểm tra."
        )
        return False
    finally:
        try:
            os.unlink(lst.name)
        except OSError:
            pass

    if r.returncode != 0:
        log(f"❌ ClipAssembler concat lỗi: {r.stderr[:2000]}")
        return False

    final_duration = _probe_duration(output_path, ffprobe_bin)
    # MP4/AAC concat adds encoder and container rounding at block boundaries.
    # With many blocks, valid mux drift can exceed 0.5s without losing voice.
    qa_tolerance = max(
        2.5,
        min(8.0, assembled_duration * 0.005),
        min(5.0, len(block_files) * 0.025),
    )
    duration_delta = final_duration - assembled_duration
    if final_duration <= 0 or abs(duration_delta) > qa_tolerance:
        log(
            f"❌ ClipAssembler QA: video {final_duration:.2f}s / "
            f"voice block {assembled_duration:.2f}s | "
            f"lech {duration_delta:+.2f}s (cho phep {qa_tolerance:.2f}s)"
        )
        return False

    log(
        f"   ✅ ClipAssembler QA: video {final_duration:.2f}s / "
        f"voice block {assembled_duration:.2f}s | lech {duration_delta:+.2f}s "
        "do mux, chap nhan"
    )

    size_mb = round(os.path.getsize(output_path) / 1e6, 1)
    log(f"   ✅ ClipAssembler hoàn tất: {os.path.basename(output_path)} ({size_mb} MB)")
    return True
