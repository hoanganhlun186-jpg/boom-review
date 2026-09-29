"""Source ownership for narration blocks; never infer it from final timestamps."""
import math
import re


def _range(item):
    for start_key, end_key in (("original_start", "original_end"),
                               ("source_start", "source_end"), ("src_start", "src_end"),
                               ("start_s", "end_s"), ("start", "end")):
        try:
            start, end = float(item[start_key]), float(item[end_key])
            if math.isfinite(start) and math.isfinite(end) and 0 <= start < end:
                return start, end
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _ids(values):
    if isinstance(values, str):
        values = re.findall(r"\d+", values)
    return {int(v) for v in (values or [])}


def owned_source_ranges(block, render_blocks, scenes):
    """Explicit source IDs own a union of ranges, not the gaps between them."""
    if block.get('source_mapping_version') == 1:
        ranges = block.get('source_ranges')
        if not isinstance(ranges, list) or not ranges:
            raise ValueError('Bản đồ nguồn đã lưu bị trống.')
        checked = [_range({'start': pair[0], 'end': pair[1]}) for pair in ranges
                   if isinstance(pair, (list, tuple)) and len(pair) == 2]
        if len(checked) != len(ranges) or any(r is None for r in checked):
            raise ValueError('Bản đồ nguồn đã lưu không hợp lệ.')
        return checked
    source_ids = _ids(block.get("source_block_ids"))
    scene_ids = _ids(block.get("scene_ids"))
    direct = _range(block)
    clip = block.get("review_clip")
    # The editor stores its selection twice. When these agree, preserve that
    # selection rather than original_* potentially rewritten by coverage repair.
    if isinstance(clip, dict) and clip.get("mode") == "source_video_timestamp":
        selected = _range(clip)
        source = _range({"source_start": block.get("source_start"),
                         "source_end": block.get("source_end")})
        if selected and source and all(abs(a-b)<0.001 for a,b in zip(selected,source)):
            direct = selected
    if direct is None and isinstance(clip, dict):
        direct = _range(clip)
    ranges = []
    if source_ids:
        # Scripts can reference the IDs of the render rows themselves, even
        # when those rows also retain IDs from an earlier grouping level.
        # Require the explicit source envelope to corroborate this namespace.
        rows = [rb for rb in render_blocks
                if rb.get("block_id") is not None
                and int(rb["block_id"]) in source_ids and _range(rb)]
        row_ids = {int(rb["block_id"]) for rb in rows}
        envelope = _range(block)
        row_mapping = (row_ids == source_ids and envelope is not None
                       and abs(min(_range(rb)[0] for rb in rows)-envelope[0]) < .001
                       and abs(max(_range(rb)[1] for rb in rows)-envelope[1]) < .001)
        # A confirmed preview may trim the last referenced row (e.g. remove
        # the source outro). Its source IDs still refer to render row IDs.
        if (not row_mapping and row_ids == source_ids and direct
                and isinstance(clip, dict) and clip.get('mode') == 'source_video_timestamp'
                and _ids(clip.get('source_block_ids')) == source_ids
                and _range(clip) == direct):
            row_mapping = (min(_range(rb)[0] for rb in rows) <= direct[0]
                           and direct[1] <= max(_range(rb)[1] for rb in rows))
        # Editor render blocks are grouped: their IDs are narration IDs, while
        # source_block_ids still refer to the original fine-grained cuts.
        grouped = not row_mapping and any(rb.get("source_block_ids") for rb in render_blocks)
        found = set()
        for rb in render_blocks:
            keys = (_ids(rb.get("source_block_ids")) if grouped else
                    {int(rb[k]) for k in ("block_id", "book_id") if rb.get(k) is not None})
            selected = source_ids & keys
            if selected and _range(rb):
                if grouped and str(rb.get("block_id")) != str(block.get("block_id")):
                    continue
                ranges.append(_range(rb)); found.update(selected)
        if found != source_ids:
            raise ValueError("Thiếu mốc cảnh nguồn được gán cho block; cần map lại cảnh.")
    elif direct:
        ranges = [direct]
    elif scene_ids:
        found = set()
        for scene in scenes:
            sid = scene.get("scene_id")
            if sid is not None and int(sid) in scene_ids and _range(scene):
                ranges.append(_range(scene)); found.add(int(sid))
        if found != scene_ids:
            raise ValueError("Thiếu thời gian cảnh nguồn của block; cần map lại cảnh.")
    else:
        # Legacy one-to-one mappings need an explicit matching ID, never list position.
        bid = block.get("block_id")
        for rb in render_blocks:
            if bid is not None and str(rb.get("block_id")) == str(bid) and _range(rb):
                ranges.append(_range(rb))
    if direct and source_ids:
        ranges = [(max(a,direct[0]),min(b,direct[1])) for a,b in ranges]
    merged = []
    for a,b in sorted(ranges):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    if not merged:
        raise ValueError("Block chưa có phạm vi cảnh nguồn hợp lệ; cần map lại cảnh.")
    return merged


def freeze_source_mapping(blocks, render_blocks, scenes=()):
    """Resolve once, before preview, and migrate identifiable legacy row mappings."""
    rows = {str(r.get('block_id')): r for r in render_blocks}
    # Legacy one-to-one jobs retained every source ID of their matching row,
    # but preview lookahead appended IDs and overwrote timestamps. Require
    # evidence across the entire job, never infer this from list position.
    legacy = (len(blocks) == len(rows) and len({str(b.get('block_id')) for b in blocks}) == len(rows)
              and all(str(b.get('block_id')) in rows
                      and _range(rows[str(b.get('block_id'))])
                      and _ids(rows[str(b.get('block_id'))].get('source_block_ids'))
                      and _ids(rows[str(b.get('block_id'))].get('source_block_ids')) <= _ids(b.get('source_block_ids'))
                      for b in blocks))
    resolved = []
    for b in blocks:
        if b.get('source_mapping_version') == 1:
            ranges = owned_source_ranges(b, render_blocks, scenes)
        elif legacy:
            ranges = [_range(rows[str(b['block_id'])])]
        else:
            ranges = owned_source_ranges(b, render_blocks, scenes)
        resolved.append(ranges)
    previous_end = 0.0
    for b, ranges in zip(blocks, resolved):
        for start, end in ranges:
            if start < previous_end - .001:
                raise ValueError(f"Block {b.get('block_id')}: đoạn hình nguồn chồng block trước; cần lập lại bản đồ trước TTS.")
            previous_end = end
    for b, ranges in zip(blocks, resolved):
        b['source_ranges'] = [list(r) for r in ranges]
        b['source_mapping_version'] = 1
        b['source_mapping_origin'] = b.get('source_mapping_origin') or ('legacy_render_row_recovery' if legacy else 'validated_source_ids')
    return blocks


def bounded_candidates(ranges, duration, minimum_start=0, used_ranges=()):
    """Select only unused footage owned by this block, preserving source order."""
    clips = []
    for start,end in ranges:
        pieces = [(max(start,minimum_start),end)]
        for used_start,used_end in used_ranges:
            remaining = []
            for a,b in pieces:
                if used_end <= a or used_start >= b:
                    remaining.append((a,b))
                else:
                    if a < used_start: remaining.append((a,used_start))
                    if used_end < b: remaining.append((used_end,b))
            pieces = remaining
        clips.extend((a,b,0.0) for a,b in pieces if b>a)
    available = sum(b-a for a,b,_ in clips)
    if available + 0.05 < duration:
        raise ValueError(f"Voice {duration:.2f}s nhưng hình trong block chỉ còn {available:.2f}s "
                         f"(thiếu {duration-available:.2f}s). Rút gọn lời kể rồi tạo lại voice block này.")
    return clips


def extend_within_scene(ranges, duration, scenes, source_end, following_ranges=(),
                        minimum_start=0, used_ranges=()):
    """Extend only the last cut, within its detected scene and before later blocks."""
    if not ranges:
        return ranges
    # Measure only footage that can actually be used, excluding prior blocks.
    available = bounded_candidates(ranges, 0, minimum_start, used_ranges)
    missing = duration - sum(b-a for a,b,_ in available)
    if missing <= 0.05:
        return ranges
    start, end = ranges[-1]
    # Strict end containment: at a scene boundary we must not take the next scene.
    containing = [r for scene in scenes if (r := _range(scene))
                  and r[0] < end and end < r[1]]
    if not containing:
        return ranges
    limit = min(source_end, min(b for a,b in containing))
    for a,b in following_ranges:
        if a < end < b:
            return ranges
        if a >= end:
            limit = min(limit, a)
    for a,b in used_ranges:
        if a < end < b:
            return ranges
        if a >= end:
            limit = min(limit, a)
    extended_end = min(end + missing, limit)
    if extended_end <= end:
        return ranges
    return list(ranges[:-1]) + [(start, extended_end)]
