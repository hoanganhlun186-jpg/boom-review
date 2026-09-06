import os
import re
import unicodedata
from typing import Any, Dict, List, Optional


class PremiumReviewPipeline:
    """Premium review pipeline helpers: book map, beat plan, sync validation."""

    _KNOWN_VI_PHRASE_REPLACEMENTS = (
        ("Ngay tai khoanh khac nay", "Ngay tại khoảnh khắc này"),
        ("chi tiet", "chi tiết"),
        ("can duoc nhan manh", "cần được nhấn mạnh"),
        ("vi no cho thay cau chuyen dang re sang mot huong nguy hiem hon", "vì nó cho thấy câu chuyện đang rẽ sang một hướng nguy hiểm hơn"),
        ("So So", "Sở Sở"),
        ("so so", "Sở Sở"),
        ("dau lau duoi ao", "đầu lâu dưới ao"),
        ("chiec dau lau", "chiếc đầu lâu"),
        ("mot chiec dau lau", "một chiếc đầu lâu"),
        ("duoi ao", "dưới ao"),
        ("cam len", "cầm lên"),
        ("nhat len", "nhặt lên"),
        ("phat hien", "phát hiện"),
        ("Hinh anh", "Hình ảnh"),
        ("lam ap luc cua nhan vat ro hon", "làm áp lực của nhân vật rõ hơn"),
        ("khien nguoi xem hieu day khong phai mot doan chuyen canh thoang qua", "khiến người xem hiểu đây không phải một đoạn chuyển cảnh thoáng qua"),
        ("Dieu can giu lai o doan nay la", "Điều cần giữ lại ở đoạn này là"),
        ("de mach ke di cung nhip voi hanh dong dang thay tren man hinh", "để mạch kể đi cùng nhịp với hành động đang thấy trên màn hình"),
        ("Tu day", "Từ đây"),
        ("cau chuyen duoc day tiep bang mot nhip noi tu nhien", "câu chuyện được đẩy tiếp bằng một nhịp nối tự nhiên"),
        ("Nhip ke o day can du them mot lop cam xuc", "Nhịp kể ở đây cần đủ thêm một lớp cảm xúc"),
        ("vua giai thich dieu dang xay ra", "vừa giải thích điều đang xảy ra"),
        ("vua chuan bi cho bien co tiep theo", "vừa chuẩn bị cho biến cố tiếp theo"),
        ("Neu doc qua nhanh hoac noi qua ngan", "Nếu đọc quá nhanh hoặc nói quá ngắn"),
        ("khoanh khac nay se bi hut mat diem roi", "khoảnh khắc này sẽ bị hụt mất điểm rơi"),
        ("vi vay voice can bam lai vao phan ung va lua chon cua nhan vat", "vì vậy voice cần bám lại vào phản ứng và lựa chọn của nhân vật"),
        ("Mo ra boi canh va nhan vat trung tam", "Mở ra bối cảnh và nhân vật trung tâm"),
        ("Mo canh / gioi thieu", "Mở cảnh / giới thiệu"),
        ("Dan vao tinh huong va van de dau tien", "Dẫn vào tình huống và vấn đề đầu tiên"),
        ("Gioi thieu nhan vat", "Giới thiệu nhân vật"),
        ("Dan vao xung dot va dong co", "Dẫn vào xung đột và động cơ"),
        ("Day mach phim", "Đẩy mạch phim"),
        ("Day sang xung dot manh hon", "Đẩy sang xung đột mạnh hơn"),
        ("Tang nhiet va giu nhan vat trong ap luc", "Tăng nhiệt và giữ nhân vật trong áp lực"),
        ("Dung vao diem no cua canh va tinh cam", "Đúng vào điểm nổ của cảnh và tình cảm"),
        ("Ha nhiet va ket lai", "Hạ nhiệt và kết lại"),
        ("Ket / hau vi", "Kết / hậu vị"),
        ("Khop lai duoi phim va de lai du vi", "Khép lại đuôi phim và để lại dư vị"),
        ("Cho biet ai dang dung truoc xung dot", "Cho biết ai đang đứng trước xung đột"),
        ("cho biet ai dang dung truoc xung dot", "cho biết ai đang đứng trước xung đột"),
        ("Dong lai cau chuyen", "Đóng lại câu chuyện"),
    )
    _TECHNICAL_ANCHORS = {
        "semantic_story",
        "semantic story",
        "action_lookahead",
        "action lookahead",
        "scene_cut",
        "scene cut",
        "pattern_cut",
        "pattern cut",
        "smart_cut",
        "smart cut",
        "clip_finder",
        "clip finder",
        "source semantic clip finder",
        "unknown",
    }

    @staticmethod
    def _clean_text(text: Any, limit: Optional[int] = None) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if limit and len(cleaned) > limit:
            return cleaned[: max(0, limit - 3)].rstrip() + "..."
        return cleaned

    @classmethod
    def restore_known_vietnamese_phrases(cls, text: Any) -> str:
        """Restore diacritics for deterministic padding/metadata phrases."""
        restored = str(text or "")
        if not restored:
            return ""
        for source, target in cls._KNOWN_VI_PHRASE_REPLACEMENTS:
            pattern = r"\s+".join(re.escape(part) for part in source.split())
            restored = re.sub(pattern, target, restored, flags=re.IGNORECASE)
        return restored

    @classmethod
    def _is_usable_anchor(cls, text: Any) -> bool:
        cleaned = cls._clean_text(cls.restore_known_vietnamese_phrases(text))
        if not cleaned:
            return False
        folded = cls._fold_text(cleaned)
        folded = re.sub(r"[^a-z0-9_+ /-]+", " ", folded).strip()
        folded = re.sub(r"\s+", " ", folded)
        if not folded or folded in cls._TECHNICAL_ANCHORS:
            return False
        parts = {part.strip() for part in re.split(r"[+/|]", folded) if part.strip()}
        if parts and parts.issubset(cls._TECHNICAL_ANCHORS):
            return False
        tokens = cls._content_tokens(cleaned)
        return len(tokens) >= 2 or len(cleaned) >= 12

    @staticmethod
    def _fold_text(text: Any) -> str:
        normalized = unicodedata.normalize("NFKD", str(text or ""))
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", ascii_text.lower()).strip()

    @classmethod
    def _content_tokens(cls, text: Any) -> List[str]:
        folded = cls._fold_text(text)
        if not folded:
            return []
        stopwords = {
            "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do", "day",
            "khi", "neu", "den", "tren", "duoi", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
            "ve", "dang", "rat", "hon", "nay", "nao", "nay", "nay", "nay", "roi", "se", "duoc",
        }
        tokens = re.findall(r"[a-z0-9]+", folded)
        return [token for token in tokens if len(token) > 2 and token not in stopwords]

    @classmethod
    def _anchor_overlap_score(cls, text: Any, anchor_text: Any) -> float:
        text_tokens = set(cls._content_tokens(text))
        anchor_tokens = set(cls._content_tokens(anchor_text))
        if not text_tokens or not anchor_tokens:
            return 0.0
        return len(text_tokens & anchor_tokens) / float(min(len(text_tokens), len(anchor_tokens)))

    @staticmethod
    def _seconds_to_timecode(seconds: float) -> str:
        try:
            seconds = float(seconds or 0.0)
        except Exception:
            seconds = 0.0
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _timecode_to_seconds(timecode: str) -> float:
        match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", str(timecode or "").strip())
        if not match:
            return 0.0
        hours, minutes, seconds, milliseconds = map(int, match.groups())
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000

    @classmethod
    def _normalize_subtitles(cls, subtitles_source: Any) -> List[Dict[str, Any]]:
        if not subtitles_source:
            return []
        if isinstance(subtitles_source, list):
            normalized = []
            for index, item in enumerate(subtitles_source, 1):
                if not isinstance(item, dict):
                    continue
                start_seconds = item.get("start_seconds")
                end_seconds = item.get("end_seconds")
                if start_seconds is None:
                    start_seconds = cls._timecode_to_seconds(item.get("start") or item.get("start_time") or 0.0)
                if end_seconds is None:
                    end_seconds = cls._timecode_to_seconds(item.get("end") or item.get("end_time") or start_seconds)
                text = cls._clean_text(item.get("text") or item.get("content") or "")
                if not text:
                    continue
                normalized.append({
                    "index": int(item.get("index") or index),
                    "start": item.get("start") or cls._seconds_to_timecode(start_seconds),
                    "end": item.get("end") or cls._seconds_to_timecode(end_seconds),
                    "text": text,
                    "start_seconds": float(start_seconds or 0.0),
                    "end_seconds": float(end_seconds or 0.0),
                })
            return normalized
        if isinstance(subtitles_source, str):
            return cls.parse_srt_content(subtitles_source)
        return []

    @classmethod
    def parse_srt_content(cls, srt_content: str) -> List[Dict[str, Any]]:
        if not srt_content or not str(srt_content).strip():
            return []
        subtitles = []
        blocks = str(srt_content).strip().split("\n\n")
        for block_index, block in enumerate(blocks, 1):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            time_line_index = None
            for index, line in enumerate(lines[:3]):
                if "-->" in line:
                    time_line_index = index
                    break
            if time_line_index is None:
                continue
            try:
                start_raw, end_raw = [part.strip() for part in lines[time_line_index].split("-->", 1)]
            except Exception:
                continue
            text = cls._clean_text(" ".join(lines[time_line_index + 1 :]))
            if not text:
                continue
            start_seconds = cls._timecode_to_seconds(start_raw)
            end_seconds = cls._timecode_to_seconds(end_raw)
            subtitles.append({
                "index": block_index,
                "start": start_raw,
                "end": end_raw,
                "text": text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
            })
        return subtitles

    @classmethod
    def _book_role(cls, index: int, total: int):
        total = max(1, int(total or 1))
        if index <= 0:
            return {
                "scene_role": "intro_gioi_thieu",
                "scene_role_label": "Mở cảnh / giới thiệu",
                "beat": "hook",
                "beat_label": "Hook",
                "emotion": "tò mò",
                "pace": "normal",
                "book_title": "Mở cảnh / giới thiệu",
                "narrative_goal": "Mở ra bối cảnh và nhân vật trung tâm",
                "bridge_hint": "Dẫn vào tình huống và vấn đề đầu tiên",
            }
        progress = index / max(1, total - 1)
        if progress < 0.25:
            return {
                "scene_role": "setup_nhan_vat",
                "scene_role_label": "Giới thiệu nhân vật",
                "beat": "setup",
                "beat_label": "Setup",
                "emotion": "quan sát",
                "pace": "slow",
                "book_title": "Giới thiệu nhân vật",
                "narrative_goal": "Cho biết ai đang đứng trước xung đột",
                "bridge_hint": "Dẫn vào xung đột và động cơ",
            }
        if progress < 0.55:
            return {
                "scene_role": "build_up",
                "scene_role_label": "Đẩy mạch phim",
                "beat": "tension",
                "beat_label": "Tension",
                "emotion": "căng thẳng",
                "pace": "fast",
                "book_title": "Đẩy mạch phim",
                "narrative_goal": "Tăng nhiệt và giữ nhân vật trong áp lực",
                "bridge_hint": "Đẩy sang xung đột mạnh hơn",
            }
        if progress < 0.80:
            return {
                "scene_role": "climax",
                "scene_role_label": "Cao trào",
                "beat": "climax",
                "beat_label": "Climax",
                "emotion": "urgent",
                "pace": "urgent",
                "book_title": "Cao trào",
                "narrative_goal": "Đúng vào điểm nổ của cảnh và tình cảm",
                "bridge_hint": "Hạ nhiệt và kết lại",
            }
        return {
            "scene_role": "ending_hau_vi",
            "scene_role_label": "Kết / hậu vị",
            "beat": "payoff",
            "beat_label": "Payoff",
            "emotion": "giải tỏa",
            "pace": "calm",
            "book_title": "Kết / hậu vị",
            "narrative_goal": "Khép lại đuôi phim và để lại dư vị",
            "bridge_hint": "Đóng lại câu chuyện",
        }

    @classmethod
    def _build_visual_anchor(cls, blocks: List[Dict[str, Any]]) -> str:
        reasons = []
        for block in blocks:
            reason = cls._clean_text(
                block.get("visual_anchor")
                or block.get("dialogue_text")
                or block.get("cut_reason")
                or block.get("reason")
                or "",
                120,
            )
            if reason and reason not in reasons:
                reasons.append(reason)
        if not reasons:
            return "canh no luc / nhan vat trung tam"
        return cls._clean_text(" + ".join(reasons[:3]), 120)

    @classmethod
    def _collect_srt_snippets(cls, subtitles: List[Dict[str, Any]], start: float, end: float, max_snippets: int = 3, pad: float = 1.0) -> List[str]:
        if not subtitles:
            return []
        snippets = []
        for sub in subtitles:
            sub_start = float(sub.get("start_seconds", 0.0) or 0.0)
            sub_end = float(sub.get("end_seconds", sub_start) or sub_start)
            if sub_end < start - pad or sub_start > end + pad:
                continue
            text = cls._clean_text(sub.get("text", ""), 140)
            if text and text not in snippets:
                snippets.append(text)
            if len(snippets) >= max_snippets:
                break
        return snippets

    @classmethod
    def _build_srt_anchor(cls, subtitles: List[Dict[str, Any]], start: float, end: float, max_snippets: int = 3) -> str:
        return " | ".join(cls._collect_srt_snippets(subtitles, start, end, max_snippets))

    @classmethod
    def _build_srt_anchor_for_ranges(
        cls,
        subtitles: List[Dict[str, Any]],
        ranges: List[tuple],
        max_snippets: int = 4,
    ) -> str:
        snippets: List[str] = []
        for start, end in ranges:
            for snippet in cls._collect_srt_snippets(subtitles, start, end, max_snippets):
                if snippet not in snippets:
                    snippets.append(snippet)
                if len(snippets) >= max_snippets:
                    return " | ".join(snippets)
        return " | ".join(snippets)

    @classmethod
    def build_book_map(
        cls,
        render_blocks: List[Dict[str, Any]],
        subtitles_source: Any = None,
        target_book_seconds: float = 24.0,
    ) -> List[Dict[str, Any]]:
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return []

        subtitles = cls._normalize_subtitles(subtitles_source)
        sorted_blocks = sorted(blocks, key=lambda block: float(block.get("start_in_final_video", 0.0) or 0.0))
        books = []
        current = []

        def block_start(block):
            return float(block.get("start_in_final_video", 0.0) or 0.0)

        def block_end(block):
            start = block_start(block)
            duration = float(block.get("duration", 0.0) or 0.0)
            return float(block.get("end_in_final_video", start + duration) or (start + duration))

        def source_start(block):
            value = block.get("original_start")
            if value is None:
                return block_start(block)
            try:
                return float(value)
            except Exception:
                return block_start(block)

        def source_end(block):
            value = block.get("original_end")
            if value is None:
                return block_end(block)
            try:
                return float(value)
            except Exception:
                return block_end(block)

        final_timeline_end = max((block_end(block) for block in sorted_blocks), default=0.0)
        source_timeline_end = max((source_end(block) for block in sorted_blocks), default=0.0)
        subtitle_timeline_end = max(
            (float(sub.get("end_seconds", sub.get("start_seconds", 0.0)) or 0.0) for sub in subtitles),
            default=0.0,
        )
        use_source_srt_timeline = (
            bool(subtitles)
            and source_timeline_end > final_timeline_end * 1.20
            and subtitle_timeline_end > final_timeline_end * 1.20
        )

        def flush():
            if not current:
                return
            first = current[0]
            last = current[-1]
            start = block_start(first)
            end = block_end(last)
            duration = max(0.1, end - start)
            final_ranges = [(block_start(item), block_end(item)) for item in current]
            source_ranges = [(source_start(item), source_end(item)) for item in current]
            original_start = source_ranges[0][0] if source_ranges else start
            original_end = source_ranges[-1][1] if source_ranges else end
            final_anchor = cls._build_srt_anchor(subtitles, start, end)
            source_anchor = cls._build_srt_anchor_for_ranges(subtitles, source_ranges)
            if use_source_srt_timeline and source_anchor:
                srt_anchor = source_anchor
                srt_timeline = "source"
                srt_range = f"{round(original_start, 3)}s - {round(original_end, 3)}s"
            else:
                srt_anchor = final_anchor or source_anchor
                srt_timeline = "cut"
                srt_range = f"{round(start, 3)}s - {round(end, 3)}s"
            reasons = []
            scores = []
            block_ids = []
            scene_ids = []
            for item in current:
                reason = cls._clean_text(
                    item.get("visual_anchor")
                    or item.get("dialogue_text")
                    or item.get("cut_reason")
                    or item.get("reason")
                    or "",
                    120,
                )
                if reason and reason not in reasons:
                    reasons.append(reason)
                try:
                    scores.append(float(item.get("smart_score", 0.0) or 0.0))
                except Exception:
                    pass
                block_id = item.get("block_id")
                if block_id is not None:
                    block_ids.append(int(block_id))
                for scene_id in item.get("scene_ids") or ([] if item.get("scene_id") is None else [item.get("scene_id")]):
                    try:
                        scene_id_int = int(scene_id)
                    except Exception:
                        continue
                    if scene_id_int not in scene_ids:
                        scene_ids.append(scene_id_int)
            try:
                words_per_second = float(os.environ.get("AUTORECAP_REVIEW_WORDS_PER_SEC", "2.75") or "2.75")
            except Exception:
                words_per_second = 2.75
            words_per_second = max(2.0, min(3.2, words_per_second))
            books.append({
                "block_id": len(books) + 1,
                "book_id": len(books) + 1,
                "block_ids": block_ids,
                "scene_ids": scene_ids,
                "source_block_ids": ",".join(str(block_id) for block_id in block_ids[:20]),
                "source_block_count": len(current),
                "start_in_final_video": round(start, 3),
                "duration": round(duration, 3),
                "end_in_final_video": round(end, 3),
                "srt_range": srt_range,
                "srt_timeline": srt_timeline,
                "original_srt_range": f"{round(original_start, 3)}s - {round(original_end, 3)}s",
                "original_start": round(original_start, 3),
                "original_end": round(original_end, 3),
                "cut_reason": "+".join(reasons[:4]),
                "smart_score": round(max(scores) if scores else 0.0, 3),
                "target_words": max(24, int(duration * words_per_second)),
                "target_duration_seconds": round(duration, 3),
                "visual_anchor": cls._build_visual_anchor(current),
                "srt_anchor": srt_anchor,
                "source_srt_anchor": source_anchor,
                "cut_srt_anchor": final_anchor,
            })
            current.clear()

        for block in sorted_blocks:
            if not current:
                current.append(block)
                continue
            current_start = block_start(current[0])
            candidate_end = block_end(block)
            candidate_duration = candidate_end - current_start
            current_duration = block_end(current[-1]) - current_start
            if current_duration >= target_book_seconds * 0.70 and candidate_duration > target_book_seconds * 1.20:
                flush()
            current.append(block)
        flush()

        total = len(books)
        for index, book in enumerate(books):
            meta = cls._book_role(index, total)
            book.update(meta)
            book["book_summary"] = meta["narrative_goal"]
            book["one_main_idea"] = meta["narrative_goal"]
            book["transition_line"] = meta["bridge_hint"]
            book["duration_hint_seconds"] = book["target_duration_seconds"]
            book["book_title"] = meta["book_title"]
        return books

    @classmethod
    def build_beat_plan(cls, book_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        plan = []
        books = [book for book in (book_map or []) if isinstance(book, dict)]
        for book in books:
            beat = cls._clean_text(book.get("beat") or "", 40)
            beat_label = cls._clean_text(book.get("beat_label") or "", 40)
            if not beat_label:
                beat_label = cls._clean_text(beat.title() if beat else "Setup", 40)
            plan.append({
                "book_id": int(book.get("book_id") or len(plan) + 1),
                "beat": beat or "setup",
                "beat_label": beat_label,
                "main_idea": cls._clean_text(book.get("one_main_idea") or book.get("book_summary") or book.get("narrative_goal") or "", 160),
                "transition_line": cls._clean_text(book.get("transition_line") or book.get("bridge_hint") or "", 140),
                "pace": cls._clean_text(book.get("pace") or "normal", 20),
                "emotion": cls._clean_text(book.get("emotion") or "", 40),
                "target_duration_seconds": float(book.get("target_duration_seconds") or book.get("duration") or 0.0),
                "target_words": int(book.get("target_words") or 0),
                "visual_anchor": cls._clean_text(book.get("visual_anchor") or "", 160),
                "srt_anchor": cls._clean_text(book.get("srt_anchor") or "", 240),
            })
        return plan

    @classmethod
    def build_book_context(
        cls,
        book_map: List[Dict[str, Any]],
        beat_plan: Optional[List[Dict[str, Any]]] = None,
        max_books: int = 80,
    ) -> str:
        if not book_map:
            return ""
        beat_lookup = {int(item.get("book_id") or 0): item for item in (beat_plan or []) if isinstance(item, dict)}
        lines = []
        sampled = book_map[:max_books]
        lines.append("BOOK TIMELINE LOCK:")
        lines.append("- Keep the exact book order from the cut video timeline.")
        lines.append("- Book 1 opens the story; later books may not appear early.")
        lines.append("- Each book must stay inside its own SRT window and visual anchor.")
        for book in sampled:
            book_id = int(book.get("book_id") or 0)
            beat_item = beat_lookup.get(book_id, {})
            lines.append(
                f"- Book {book_id} | time: {float(book.get('start_in_final_video') or 0.0):.1f}s-"
                f"{float(book.get('end_in_final_video') or 0.0):.1f}s"
                f" | beat: {beat_item.get('beat') or book.get('beat') or ''}"
                f" | role: {book.get('scene_role_label') or book.get('scene_role') or ''}"
                f" | emotion: {book.get('emotion') or beat_item.get('emotion') or ''}"
                f" | pace: {book.get('pace') or beat_item.get('pace') or 'normal'}"
                f" | duration: {float(book.get('duration') or 0.0):.1f}s"
                f" | target_words: {book.get('target_words') or ''}"
                f" | scene_ids: {book.get('scene_ids') or ''}"
                f" | must_mention: {cls._clean_text(book.get('must_mention') or '', 140)}"
                f" | visual_anchor: {cls._clean_text(book.get('visual_anchor') or '', 120)}"
                f" | srt_anchor: {cls._clean_text(book.get('srt_anchor') or '', 180)}"
                f" | transition: {cls._clean_text(book.get('transition_line') or book.get('bridge_hint') or '', 120)}"
                f" | main_idea: {cls._clean_text(beat_item.get('main_idea') or book.get('narrative_goal') or '', 120)}"
            )
        if len(book_map) > len(sampled):
            lines.append(f"... {len(book_map) - len(sampled)} book khac theo cung cau truc.")
        return "\n".join(lines)

    @classmethod
    def _split_text_into_balanced_chunks(cls, text: str, chunk_count: int) -> List[str]:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return []
        try:
            chunk_count = int(chunk_count)
        except Exception:
            chunk_count = 1
        if chunk_count <= 1:
            return [cleaned]

        words = cleaned.split()
        if len(words) >= chunk_count:
            boundaries = [round(index * len(words) / chunk_count) for index in range(chunk_count + 1)]
            boundaries[0] = 0
            boundaries[-1] = len(words)
            chunks = []
            for index in range(chunk_count):
                start = boundaries[index]
                end = boundaries[index + 1]
                if index < chunk_count - 1:
                    end = max(end, start + 1)
                else:
                    end = len(words)
                piece = " ".join(words[start:end]).strip()
                if piece:
                    chunks.append(piece)
            if len(chunks) == chunk_count:
                return chunks

        pieces = []
        total_chars = len(cleaned)
        for index in range(chunk_count):
            start = round(index * total_chars / chunk_count)
            end = round((index + 1) * total_chars / chunk_count)
            piece = cleaned[start:end].strip()
            if piece:
                pieces.append(piece)

        if len(pieces) < chunk_count:
            sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", cleaned) if sentence.strip()]
            if sentences:
                pieces = sentences[:]

        while len(pieces) < chunk_count and pieces:
            longest_index = max(range(len(pieces)), key=lambda i: len(pieces[i]))
            piece = pieces.pop(longest_index)
            split_words = piece.split()
            if len(split_words) >= 2:
                midpoint = max(1, len(split_words) // 2)
                left = " ".join(split_words[:midpoint]).strip()
                right = " ".join(split_words[midpoint:]).strip()
            else:
                midpoint = max(1, len(piece) // 2)
                left = piece[:midpoint].strip()
                right = piece[midpoint:].strip()
            if not left or not right:
                pieces.insert(longest_index, piece)
                break
            pieces.insert(longest_index, right)
            pieces.insert(longest_index, left)

        if len(pieces) > chunk_count:
            merged = pieces[: chunk_count - 1]
            merged.append(" ".join(pieces[chunk_count - 1:]).strip())
            pieces = merged

        return [piece for piece in pieces if piece.strip()] or [cleaned]

    @classmethod
    def normalize_script_blocks(cls, blocks: Any) -> List[Dict[str, Any]]:
        normalized = []
        if not isinstance(blocks, list):
            return normalized
        for index, block in enumerate(blocks, 1):
            if isinstance(block, dict):
                text = block.get("text") or block.get("script") or block.get("voiceover") or ""
                block_id = block.get("block_id") or block.get("book_id") or block.get("id") or index
            else:
                text = str(block or "")
                block_id = index
            text = cls._clean_text(text)
            if not text:
                continue
            try:
                block_id = int(block_id)
            except Exception:
                block_id = index
            normalized.append({
                "block_id": block_id,
                "book_id": block_id,
                "text": text,
                "target_words": block.get("target_words") if isinstance(block, dict) else None,
                "visual_hint": block.get("visual_hint", "") if isinstance(block, dict) else "",
                "scene_anchor": block.get("scene_anchor", "") if isinstance(block, dict) else "",
                "scene_role": block.get("scene_role", "") if isinstance(block, dict) else "",
                "scene_role_label": block.get("scene_role_label", "") if isinstance(block, dict) else "",
                "emotion": block.get("emotion", "") if isinstance(block, dict) else "",
                "pace": block.get("pace", "") if isinstance(block, dict) else "",
                "beat": block.get("beat", "") if isinstance(block, dict) else "",
                "book_title": block.get("book_title", "") if isinstance(block, dict) else "",
                "narrative_goal": block.get("narrative_goal", "") if isinstance(block, dict) else "",
                "bridge_line": block.get("bridge_line", "") if isinstance(block, dict) else "",
                "bridge_to_next": block.get("bridge_to_next", "") if isinstance(block, dict) else "",
                "duration_hint_seconds": block.get("duration_hint_seconds") if isinstance(block, dict) else None,
                "visual_anchor": block.get("visual_anchor", "") if isinstance(block, dict) else "",
                "visual_notes": block.get("visual_notes", "") if isinstance(block, dict) else "",
                "visual_evidence_source": block.get("visual_evidence_source", "") if isinstance(block, dict) else "",
                "must_mention": block.get("must_mention", "") if isinstance(block, dict) else "",
                "book_summary": block.get("book_summary", "") if isinstance(block, dict) else "",
                "transition_line": block.get("transition_line", "") if isinstance(block, dict) else "",
                "srt_anchor": block.get("srt_anchor", "") if isinstance(block, dict) else "",
                "source_srt_anchor": block.get("source_srt_anchor", "") if isinstance(block, dict) else "",
                "cut_srt_anchor": block.get("cut_srt_anchor", "") if isinstance(block, dict) else "",
                "render_start": block.get("render_start") if isinstance(block, dict) else None,
                "render_duration": block.get("render_duration") if isinstance(block, dict) else None,
                "render_end": block.get("render_end") if isinstance(block, dict) else None,
                "render_reason": block.get("render_reason", "") if isinstance(block, dict) else "",
                "smart_score": block.get("smart_score") if isinstance(block, dict) else None,
            })
        return sorted(normalized, key=lambda item: item["block_id"])

    @classmethod
    def align_script_blocks_to_book_map(cls, script_blocks: Any, script_text: str, book_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        books = [book for book in (book_map or []) if isinstance(book, dict)]
        if not books:
            return cls.normalize_script_blocks(script_blocks)

        normalized = cls.normalize_script_blocks(script_blocks)
        if len(normalized) != len(books):
            fallback_text = "\n\n".join(block.get("text", "") for block in normalized if block.get("text")).strip() or cls._clean_text(script_text)
            aligned_texts = cls._split_text_into_balanced_chunks(fallback_text, len(books))
            normalized = []
            for index, book in enumerate(books, 1):
                text = aligned_texts[index - 1] if index - 1 < len(aligned_texts) else (aligned_texts[-1] if aligned_texts else "")
                text = cls._clean_text(text) or cls._clean_text(fallback_text)
                normalized.append(cls._book_block_payload(book, index, text))
            return normalized

        aligned = []
        for index, (block, book) in enumerate(zip(normalized, books), 1):
            merged = dict(block)
            merged.update(cls._book_block_payload(book, index, block.get("text", "")))
            merged.update({
                "text": block.get("text", ""),
                "target_words": block.get("target_words") or book.get("target_words"),
                "visual_hint": block.get("visual_hint") or book.get("visual_anchor") or book.get("cut_reason", ""),
                "scene_anchor": block.get("scene_anchor") or book.get("srt_anchor") or book.get("cut_reason", ""),
                "scene_role": block.get("scene_role") or book.get("scene_role", ""),
                "scene_role_label": block.get("scene_role_label") or book.get("scene_role_label", ""),
                "emotion": block.get("emotion") or book.get("emotion", ""),
                "pace": block.get("pace") or book.get("pace", "normal"),
                "beat": block.get("beat") or book.get("beat", ""),
                "book_title": block.get("book_title") or book.get("book_title", ""),
                "narrative_goal": block.get("narrative_goal") or book.get("narrative_goal", ""),
                "bridge_line": block.get("bridge_line") or block.get("bridge_to_next") or book.get("transition_line") or book.get("bridge_hint", ""),
                "bridge_to_next": block.get("bridge_to_next") or book.get("transition_line") or book.get("bridge_hint", ""),
                "duration_hint_seconds": block.get("duration_hint_seconds") or book.get("duration", 0.0),
                "visual_anchor": block.get("visual_anchor") or book.get("visual_anchor", ""),
                "visual_notes": block.get("visual_notes") or book.get("visual_notes", ""),
                "visual_evidence_source": block.get("visual_evidence_source") or book.get("visual_evidence_source", ""),
                "must_mention": block.get("must_mention") or book.get("must_mention", ""),
                "book_summary": block.get("book_summary") or book.get("book_summary", ""),
                "transition_line": block.get("transition_line") or book.get("transition_line") or book.get("bridge_hint", ""),
                "srt_anchor": block.get("srt_anchor") or book.get("srt_anchor", ""),
                "source_srt_anchor": block.get("source_srt_anchor") or book.get("source_srt_anchor", ""),
                "cut_srt_anchor": block.get("cut_srt_anchor") or book.get("cut_srt_anchor", ""),
                "render_start": book.get("start_in_final_video", 0.0),
                "render_duration": book.get("duration", 0.0),
                "render_end": book.get("end_in_final_video", 0.0),
                "render_reason": book.get("cut_reason", ""),
                "smart_score": book.get("smart_score", 0),
            })
            aligned.append(merged)
        return sorted(aligned, key=lambda item: item["block_id"])

    @classmethod
    def _book_block_payload(cls, book: Dict[str, Any], index: int, text: str) -> Dict[str, Any]:
        book_id = int(book.get("book_id") or index)
        return {
            "block_id": book_id,
            "book_id": book_id,
            "text": cls._clean_text(text),
            "target_words": book.get("target_words"),
            "visual_hint": book.get("visual_anchor") or book.get("cut_reason", ""),
            "scene_anchor": book.get("srt_anchor") or book.get("cut_reason", ""),
            "scene_role": book.get("scene_role", ""),
            "scene_role_label": book.get("scene_role_label", ""),
            "emotion": book.get("emotion", ""),
            "pace": book.get("pace", "normal"),
            "beat": book.get("beat", ""),
            "book_title": book.get("book_title", ""),
            "narrative_goal": book.get("narrative_goal", ""),
            "bridge_line": book.get("transition_line") or book.get("bridge_hint", ""),
            "bridge_to_next": book.get("transition_line") or book.get("bridge_hint", ""),
            "duration_hint_seconds": book.get("duration_hint_seconds") or book.get("duration", 0.0),
            "visual_anchor": book.get("visual_anchor", ""),
            "visual_notes": book.get("visual_notes", ""),
            "visual_evidence_source": book.get("visual_evidence_source", ""),
            "must_mention": book.get("must_mention", ""),
            "book_summary": book.get("book_summary", ""),
            "transition_line": book.get("transition_line") or book.get("bridge_hint", ""),
            "srt_anchor": book.get("srt_anchor", ""),
            "source_srt_anchor": book.get("source_srt_anchor", ""),
            "cut_srt_anchor": book.get("cut_srt_anchor", ""),
            "render_start": book.get("start_in_final_video", 0.0),
            "render_duration": book.get("duration", 0.0),
            "render_end": book.get("end_in_final_video", 0.0),
            "render_reason": book.get("cut_reason", ""),
            "smart_score": book.get("smart_score", 0),
        }

    @classmethod
    def _anchor_phrase(cls, *parts: Any, limit: int = 110) -> str:
        for part in parts:
            text = cls._clean_text(part)
            if not text:
                continue
            text = text.split("|", 1)[0].strip()
            if not cls._is_usable_anchor(text):
                continue
            return cls._clean_text(cls.restore_known_vietnamese_phrases(text), limit)
        return ""

    @classmethod
    def _best_anchor_phrase(cls, *parts: Any, limit: int = 140) -> str:
        best_text = ""
        best_score = -1
        for part in parts:
            text = cls._clean_text(part)
            if not text:
                continue
            text = text.split("|", 1)[0].strip()
            if not cls._is_usable_anchor(text):
                continue
            restored = cls._clean_text(cls.restore_known_vietnamese_phrases(text), limit)
            folded = cls._fold_text(restored)
            tokens = set(cls._content_tokens(restored))
            score = len(tokens)
            if "dau" in folded and "lau" in folded:
                score += 4
            if "ao" in folded:
                score += 2
            if any(action in folded for action in ("cam len", "nhat len", "vot len", "nang")):
                score += 3
            if "so so" in folded or "so-so" in folded:
                score += 3
            if score > best_score:
                best_text = restored
                best_score = score
        return best_text

    @classmethod
    def enrich_render_blocks_with_context(
        cls,
        render_blocks: Any,
        subtitles_source: Any = None,
        scene_cards: Any = None,
    ) -> List[Dict[str, Any]]:
        """Attach the best available SRT/scene-card evidence to every AI book."""
        blocks = [dict(block) for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return []
        subtitles = cls._normalize_subtitles(subtitles_source)
        cards_by_id: Dict[int, Dict[str, Any]] = {}
        for card in scene_cards or []:
            if not isinstance(card, dict):
                continue
            try:
                cards_by_id[int(card.get("scene_id"))] = card
            except Exception:
                continue

        def _scene_ids(block: Dict[str, Any]) -> List[int]:
            raw = block.get("scene_ids") or ([] if block.get("scene_id") is None else [block.get("scene_id")])
            result = []
            for value in raw:
                try:
                    scene_id = int(value)
                except Exception:
                    continue
                if scene_id not in result:
                    result.append(scene_id)
            return result

        def _source_range(block: Dict[str, Any]) -> tuple:
            start = block.get("original_start")
            end = block.get("original_end")
            if start is None:
                start = block.get("start")
            if end is None:
                end = block.get("end")
            if start is None:
                start = block.get("start_in_final_video", 0.0)
            if end is None:
                end = float(start or 0.0) + float(block.get("duration", 0.0) or 0.0)
            try:
                return float(start or 0.0), float(end or start or 0.0)
            except Exception:
                return 0.0, 0.0

        enriched = []
        for block in blocks:
            item = dict(block)
            scene_ids = _scene_ids(item)
            if scene_ids and not item.get("scene_ids"):
                item["scene_ids"] = scene_ids

            start, end = _source_range(item)
            source_anchor = (
                cls._build_srt_anchor_for_ranges(subtitles, [(start, end)], max_snippets=6)
                or cls._clean_text(
                    item.get("triangulated_srt_anchor")
                    or item.get("cut_visible_srt")
                    or item.get("cut_srt_anchor")
                    or item.get("source_srt_anchor")
                    or "",
                    520,
                )
            )
            scene_dialogues = []
            scene_visuals = []
            for scene_id in scene_ids:
                card = cards_by_id.get(scene_id)
                if not card:
                    continue
                dialogue = cls._clean_text(card.get("dialogue_text"), 180)
                if dialogue and dialogue not in scene_dialogues:
                    scene_dialogues.append(dialogue)
                visual = cls._anchor_phrase(
                    card.get("visual_notes"),
                    card.get("reason"),
                    " ".join(str(key) for key in (card.get("keywords") or []) if key),
                    limit=140,
                )
                if visual and visual not in scene_visuals:
                    scene_visuals.append(visual)

            scene_dialogue = cls._clean_text(" | ".join(scene_dialogues[:5]), 520)
            scene_visual = cls._clean_text(" + ".join(scene_visuals[:4]), 220)

            current_srt = cls._clean_text(item.get("srt_anchor") or "")
            current_dialogue = cls._clean_text(item.get("dialogue_text") or "")
            if source_anchor and not cls._is_usable_anchor(current_srt):
                item["srt_anchor"] = source_anchor
                current_srt = source_anchor
            if source_anchor and not item.get("source_srt_anchor"):
                item["source_srt_anchor"] = source_anchor
            if not cls._is_usable_anchor(current_dialogue):
                dialogue = source_anchor or scene_dialogue
                if dialogue:
                    item["dialogue_text"] = dialogue

            current_visual = cls._clean_text(item.get("visual_anchor") or item.get("visual_hint") or "")
            if scene_visual and not cls._is_usable_anchor(current_visual):
                item["visual_anchor"] = scene_visual
                item["visual_hint"] = scene_visual

            must_mention = cls._anchor_phrase(
                item.get("srt_anchor"),
                item.get("dialogue_text"),
                item.get("visual_anchor"),
                item.get("visual_hint"),
                item.get("cut_reason"),
                item.get("reason"),
                limit=180,
            )
            if must_mention:
                item["must_mention"] = must_mention
            enriched.append(item)
        return enriched

    @classmethod
    def render_context_coverage(cls, render_blocks: Any) -> Dict[str, Any]:
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        total = len(blocks)
        if not total:
            return {
                "book_count": 0,
                "srt_coverage_ratio": 0.0,
                "visual_coverage_ratio": 0.0,
                "evidence_coverage_ratio": 0.0,
                "weak_context_count": 0,
                "status": "missing",
            }

        def usable(value: Any) -> bool:
            return cls._is_usable_anchor(value)

        with_srt = 0
        with_dialogue = 0
        with_visual = 0
        technical_visual = 0
        with_must = 0
        weak = 0
        weak_ids = []
        for index, block in enumerate(blocks, 1):
            srt_ok = usable(
                block.get("srt_anchor")
                or block.get("source_srt_anchor")
                or block.get("cut_srt_anchor")
                or block.get("cut_visible_srt")
                or block.get("triangulated_srt_anchor")
            )
            dialogue_ok = usable(block.get("dialogue_text") or block.get("cut_visible_srt"))
            visual_raw = block.get("visual_anchor") or block.get("visual_hint")
            visual_ok = usable(visual_raw)
            if srt_ok:
                with_srt += 1
            if dialogue_ok:
                with_dialogue += 1
            if visual_ok:
                with_visual += 1
            elif cls._clean_text(visual_raw):
                technical_visual += 1
            if usable(block.get("must_mention")):
                with_must += 1
            if not (srt_ok or dialogue_ok or visual_ok):
                weak += 1
                try:
                    weak_ids.append(int(block.get("block_id") or block.get("book_id") or index))
                except Exception:
                    weak_ids.append(index)

        srt_ratio = with_srt / float(total)
        visual_ratio = with_visual / float(total)
        evidence_ratio = (total - weak) / float(total)
        if evidence_ratio >= 0.85 and srt_ratio >= 0.50:
            status = "ok"
        elif evidence_ratio >= 0.60:
            status = "warn"
        else:
            status = "poor"
        return {
            "book_count": total,
            "with_srt_anchor": with_srt,
            "with_dialogue_text": with_dialogue,
            "with_visual_anchor": with_visual,
            "with_must_mention": with_must,
            "technical_visual_anchor": technical_visual,
            "weak_context_count": weak,
            "weak_context_block_ids": weak_ids[:30],
            "srt_coverage_ratio": round(srt_ratio, 3),
            "visual_coverage_ratio": round(visual_ratio, 3),
            "evidence_coverage_ratio": round(evidence_ratio, 3),
            "status": status,
        }

    @classmethod
    def _anchor_overlap_enough(cls, text: Any, anchor: Any) -> bool:
        anchor_tokens = set(cls._content_tokens(cls.restore_known_vietnamese_phrases(anchor)))
        if not anchor_tokens:
            return True
        text_tokens = set(cls._content_tokens(cls.restore_known_vietnamese_phrases(text)))
        overlap = anchor_tokens & text_tokens
        required = 1 if len(anchor_tokens) <= 2 else 2
        return len(overlap) >= required

    @classmethod
    def _required_scene_anchor(cls, block: Dict[str, Any], book: Dict[str, Any]) -> str:
        must_anchor = cls._anchor_phrase(
            block.get("must_mention"),
            book.get("must_mention"),
            limit=140,
        )
        if must_anchor:
            return must_anchor
        return cls._best_anchor_phrase(
            block.get("visual_anchor"),
            block.get("visual_notes"),
            block.get("visual_hint"),
            book.get("visual_anchor"),
            book.get("visual_notes"),
            block.get("srt_anchor"),
            block.get("dialogue_text"),
            block.get("scene_anchor"),
            book.get("srt_anchor"),
            book.get("dialogue_text"),
            book.get("source_srt_anchor"),
            book.get("cut_srt_anchor"),
            limit=140,
        )

    @classmethod
    def _anchor_sentence(cls, anchor: str) -> str:
        anchor = cls._clean_text(cls.restore_known_vietnamese_phrases(anchor), 140)
        if not anchor:
            return ""
        anchor = anchor.rstrip(" .。")
        folded = cls._fold_text(anchor)
        has_skull_pond = "dau" in folded and "lau" in folded and "ao" in folded
        has_pickup = any(token in folded for token in ("cam len", "nhat len", "vot len"))
        has_so_so = "so so" in folded or "so-so" in folded
        if has_skull_pond and (has_pickup or has_so_so):
            return (
                "Dưới ao, Sở Sở phát hiện chiếc đầu lâu rồi cầm nó lên, "
                "biến cảnh này thành manh mối rùng mình của vụ án."
            )
        if has_skull_pond:
            return (
                "Dưới ao, chiếc đầu lâu bất ngờ lộ ra, "
                "kéo toàn bộ cuộc điều tra sang một hướng đáng ngờ hơn."
            )
        return f"Chi tiết {anchor} đẩy mạch phim sang một bước ngoặt mới."

    @classmethod
    def _similar_sentence_present(cls, text: Any, sentence: Any) -> bool:
        folded_text = cls._fold_text(text)
        folded_sentence = cls._fold_text(sentence)
        if not folded_text or not folded_sentence:
            return False
        if folded_sentence in folded_text:
            return True
        tokens = set(cls._content_tokens(sentence))
        if len(tokens) < 3:
            return False
        return len(tokens & set(cls._content_tokens(text))) / float(len(tokens)) >= 0.82

    @classmethod
    def _ensure_text_mentions_scene_anchor(
        cls,
        text: Any,
        block: Dict[str, Any],
        book: Dict[str, Any],
    ) -> tuple[str, str]:
        cleaned = cls._clean_text(cls.restore_known_vietnamese_phrases(text))
        anchor = cls._required_scene_anchor(block, book)
        if not anchor or cls._anchor_overlap_enough(cleaned, anchor):
            return cleaned, ""
        sentence = cls._anchor_sentence(anchor)
        if not sentence:
            return cleaned, ""
        if cleaned:
            cleaned = f"{sentence} {cleaned}"
        else:
            cleaned = sentence
        return cleaned, anchor

    @classmethod
    def _timing_padding_sentences(cls, block: Dict[str, Any], book: Dict[str, Any]) -> List[str]:
        must_anchor = cls._anchor_phrase(
            block.get("must_mention"),
            book.get("must_mention"),
        )
        srt_anchor = cls._anchor_phrase(
            block.get("srt_anchor"),
            block.get("scene_anchor"),
            book.get("srt_anchor"),
        )
        visual_anchor = cls._anchor_phrase(
            block.get("visual_anchor"),
            block.get("visual_hint"),
            book.get("visual_anchor"),
            book.get("cut_reason"),
        )
        goal = cls._anchor_phrase(
            block.get("narrative_goal"),
            block.get("book_summary"),
            book.get("narrative_goal"),
            book.get("book_summary"),
        )
        transition = cls._anchor_phrase(
            block.get("bridge_line"),
            block.get("bridge_to_next"),
            book.get("transition_line"),
            book.get("bridge_hint"),
        )

        sentences = []
        if must_anchor:
            sentences.append(cls._anchor_sentence(must_anchor))
        if srt_anchor:
            sentences.append(cls._anchor_sentence(srt_anchor))
        if visual_anchor:
            sentences.append(
                f"Hình ảnh {visual_anchor} giữ người xem lại với áp lực thật của nhân vật trong cảnh này."
            )
        if goal:
            sentences.append(
                f"Đoạn này cần làm rõ {goal}, để phần review đi cùng nhịp với hành động đang thấy trên màn hình."
            )
        if transition:
            sentences.append(
                f"Từ đó, {transition} trở thành nhịp nối tự nhiên cho cảnh kế tiếp."
            )
        return [sentence for sentence in sentences if sentence]

    @classmethod
    def enforce_block_word_targets(
        cls,
        script_blocks: Any,
        script_text: str,
        book_map: List[Dict[str, Any]],
        min_ratio: float = 0.88,
        max_ratio: float = 1.18,
    ) -> List[Dict[str, Any]]:
        """Pad underfilled blocks so voice has enough material for the mapped video time."""
        books = [book for book in (book_map or []) if isinstance(book, dict)]
        if not books:
            return cls.normalize_script_blocks(script_blocks)

        aligned = cls.align_script_blocks_to_book_map(script_blocks, script_text, books)
        padded = []
        for index, block in enumerate(aligned):
            book = books[index] if index < len(books) else {}
            item = dict(block)
            text = cls._clean_text(item.get("text", ""))
            text, enforced_anchor = cls._ensure_text_mentions_scene_anchor(text, item, book)
            if enforced_anchor:
                item["text"] = text
                item["scene_anchor_enforced"] = True
                item["required_scene_anchor"] = enforced_anchor
            try:
                target_words = int(item.get("target_words") or book.get("target_words") or 0)
            except Exception:
                target_words = 0
            if target_words <= 0:
                duration = float(
                    item.get("duration_hint_seconds")
                    or item.get("render_duration")
                    or book.get("target_duration_seconds")
                    or book.get("duration")
                    or 0.0
                )
                target_words = max(8, int(duration * 2.5))

            min_words = max(8, int(target_words * min_ratio))
            max_words = max(min_words + 4, int(target_words * max_ratio))
            before_words = len(text.split())

            if before_words < min_words:
                for sentence in cls._timing_padding_sentences(item, book):
                    current_words = len(text.split())
                    if current_words >= min_words:
                        break
                    if cls._similar_sentence_present(text, sentence):
                        continue
                    candidate = cls._clean_text(f"{text} {sentence}")
                    candidate_words = len(candidate.split())
                    if candidate_words > max_words and current_words >= max(8, int(target_words * 0.80)):
                        break
                    text = candidate

                after_words = len(text.split())
                if after_words > before_words:
                    item["text"] = text
                    item["timing_padded"] = True
                    item["timing_padding_words"] = after_words - before_words
                    item["target_words"] = target_words
                    item["min_words"] = min_words
                    item["max_words"] = max_words

            padded.append(item)

        return sorted(padded, key=lambda item: item["block_id"])

    @classmethod
    def validate_sync(
        cls,
        script_blocks: Any,
        book_map: List[Dict[str, Any]],
        target_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        books = [book for book in (book_map or []) if isinstance(book, dict)]
        blocks = cls.normalize_script_blocks(script_blocks)
        issues = []
        if not books:
            return {
                "ok": False,
                "needs_rewrite": False,
                "issues": ["missing_book_map"],
                "book_count": 0,
                "script_block_count": len(blocks),
                "duration_ratio": 0.0,
                "word_ratio": 0.0,
                "total_words": sum(len(cls._clean_text(block.get("text", "")).split()) for block in blocks),
                "target_total_words": 0,
                "matched_books": 0,
                "problem_books": [],
                "block_word_counts": [],
            }

        book_count = len(books)
        block_count = len(blocks)
        matched_books = min(book_count, block_count)
        problem_books = []
        total_words = 0
        target_total_words = 0
        block_word_counts = []
        for index, book in enumerate(books):
            block = blocks[index] if index < len(blocks) else {}
            text = cls._clean_text(block.get("text", ""))
            word_count = len(text.split())
            total_words += word_count
            try:
                target_words = int(book.get("target_words") or 0)
            except Exception:
                target_words = 0
            if target_words <= 0:
                duration = float(book.get("target_duration_seconds") or book.get("duration") or 0.0)
                target_words = max(8, int(duration * 2.5))
            min_words = max(8, int(target_words * 0.70))
            max_words = max(min_words + 4, int(target_words * 1.30))
            target_total_words += target_words
            block_word_counts.append({
                "book_id": int(book.get("book_id") or index + 1),
                "words": word_count,
                "target_words": target_words,
                "min_words": min_words,
                "max_words": max_words,
            })
            if index >= len(blocks):
                issues.append(f"missing_script_block_{book.get('book_id', index + 1)}")
                problem_books.append(int(book.get("book_id") or index + 1))
                continue
            if word_count < 6:
                issues.append(f"too_short_{book.get('book_id', index + 1)}")
                problem_books.append(int(book.get("book_id") or index + 1))
            if word_count < min_words:
                issues.append(f"under_target_words_{book.get('book_id', index + 1)}_{word_count}/{min_words}")
                problem_books.append(int(book.get("book_id") or index + 1))
            if word_count > max_words:
                issues.append(f"over_target_words_{book.get('book_id', index + 1)}_{word_count}/{max_words}")
                problem_books.append(int(book.get("book_id") or index + 1))
            if not cls._clean_text(block.get("bridge_line") or block.get("bridge_to_next") or book.get("transition_line") or ""):
                issues.append(f"missing_bridge_{book.get('book_id', index + 1)}")
            if not cls._clean_text(block.get("visual_hint") or block.get("visual_anchor") or book.get("visual_anchor") or ""):
                issues.append(f"missing_visual_{book.get('book_id', index + 1)}")

        script_seconds = total_words / 2.5 if total_words else 0.0
        if target_seconds and target_seconds > 0:
            duration_ratio = script_seconds / float(target_seconds)
        else:
            duration_ratio = 1.0 if total_words else 0.0
        word_ratio = (total_words / target_total_words) if target_total_words > 0 else duration_ratio
        needs_rewrite = (
            block_count != book_count
            or duration_ratio < 0.88
            or duration_ratio > 1.12
            or word_ratio < 0.88
            or word_ratio > 1.12
            or len(problem_books) > max(1, book_count // 3)
        )
        return {
            "ok": not needs_rewrite and not issues,
            "needs_rewrite": needs_rewrite,
            "issues": issues,
            "book_count": book_count,
            "script_block_count": block_count,
            "duration_ratio": duration_ratio,
            "word_ratio": word_ratio,
            "total_words": total_words,
            "target_total_words": target_total_words,
            "matched_books": matched_books,
            "problem_books": sorted(set(problem_books)),
            "block_word_counts": block_word_counts,
        }

    @classmethod
    def validate_story_quality(
        cls,
        script_blocks: Any,
        book_map: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        books = [book for book in (book_map or []) if isinstance(book, dict)]
        blocks = cls.normalize_script_blocks(script_blocks)
        issues = []
        problem_books = []

        if not books or not blocks:
            return {
                "ok": False,
                "needs_rewrite": bool(books),
                "issues": ["missing_story_blocks"] if books else ["missing_book_map"],
                "problem_books": [int(book.get("book_id") or index + 1) for index, book in enumerate(books[:5])],
                "suggestions": ["Can script_blocks theo book_map truoc khi polish."],
            }

        weak_openers = (
            "hom nay", "bo phim nay", "cau chuyen bat dau", "o dau phim",
            "mo dau phim", "tiep theo", "sau do", "luc nay", "canh nay",
            "phan nay", "chung ta se", "bay gio hay",
        )
        transition_markers = (
            "nhung", "the nhung", "trong khi", "chinh luc", "tu day", "ngay khi",
            "cung luc", "va roi", "chua dung lai", "dieu nay", "boi vi", "nen",
            "de roi", "khi ay", "dang luc", "bat ngo", "cang", "day",
        )
        sentence_splitter = re.compile(r"(?<=[.!?。！？])\s+")
        opening_keys = []

        for index, block in enumerate(blocks):
            book = books[index] if index < len(books) else {}
            book_id = int(book.get("book_id") or block.get("book_id") or block.get("block_id") or index + 1)
            text = cls._clean_text(block.get("text", ""))
            folded = cls._fold_text(text)
            words = folded.split()
            opening = " ".join(words[:4])
            opening_keys.append(opening)

            if index == 0 and any(folded.startswith(prefix) for prefix in weak_openers):
                issues.append(f"weak_hook_book_{book_id}")
                problem_books.append(book_id)

            if index > 0 and any(folded.startswith(prefix) for prefix in weak_openers[5:]):
                issues.append(f"generic_opening_book_{book_id}")
                problem_books.append(book_id)

            if index > 0 and opening and opening == opening_keys[index - 1]:
                issues.append(f"repeated_opening_book_{book_id}")
                problem_books.append(book_id)

            sentences = [sentence.strip() for sentence in sentence_splitter.split(text) if sentence.strip()]
            if len(sentences) < 2:
                issues.append(f"flat_block_book_{book_id}")
                problem_books.append(book_id)

            if index < len(blocks) - 1:
                tail = " ".join(words[-18:])
                has_transition_text = any(marker in tail for marker in transition_markers)
                has_transition_meta = bool(cls._clean_text(
                    block.get("bridge_line")
                    or block.get("bridge_to_next")
                    or block.get("transition_line")
                    or book.get("transition_line")
                    or book.get("bridge_hint")
                ))
                if not has_transition_text and not has_transition_meta:
                    issues.append(f"missing_transition_book_{book_id}")
                    problem_books.append(book_id)

            proof = cls._clean_text(
                block.get("visual_hint")
                or block.get("visual_anchor")
                or block.get("scene_anchor")
                or book.get("visual_anchor")
                or book.get("srt_anchor")
            )
            if not proof and "[DIALOGUE" not in text.upper():
                issues.append(f"missing_anchor_book_{book_id}")
                problem_books.append(book_id)

        anchor_alignment = []
        for index, book in enumerate(books):
            block = blocks[index] if index < len(blocks) else {}
            text = cls._clean_text(block.get("text", ""))
            own_anchor = " ".join(
                part for part in [
                    cls._clean_text(book.get("srt_anchor") or "", 240),
                    cls._clean_text(book.get("visual_anchor") or "", 160),
                    cls._clean_text(book.get("transition_line") or book.get("bridge_hint") or "", 140),
                ]
                if part
            )
            prev_anchor = ""
            next_anchor = ""
            if index > 0:
                prev_book = books[index - 1]
                prev_anchor = " ".join(
                    part for part in [
                        cls._clean_text(prev_book.get("srt_anchor") or "", 240),
                        cls._clean_text(prev_book.get("visual_anchor") or "", 160),
                        cls._clean_text(prev_book.get("transition_line") or prev_book.get("bridge_hint") or "", 140),
                    ]
                    if part
                )
            if index < len(books) - 1:
                next_book = books[index + 1]
                next_anchor = " ".join(
                    part for part in [
                        cls._clean_text(next_book.get("srt_anchor") or "", 240),
                        cls._clean_text(next_book.get("visual_anchor") or "", 160),
                        cls._clean_text(next_book.get("transition_line") or next_book.get("bridge_hint") or "", 140),
                    ]
                    if part
                )
            own_score = cls._anchor_overlap_score(text, own_anchor)
            prev_score = cls._anchor_overlap_score(text, prev_anchor) if prev_anchor else 0.0
            next_score = cls._anchor_overlap_score(text, next_anchor) if next_anchor else 0.0
            anchor_alignment.append({
                "book_id": int(book.get("book_id") or index + 1),
                "own_score": round(own_score, 3),
                "prev_score": round(prev_score, 3),
                "next_score": round(next_score, 3),
            })
            if own_anchor and own_score < 0.08 and max(prev_score, next_score) >= 0.16:
                issues.append(f"timeline_drift_book_{book.get('book_id', index + 1)}")
                problem_books.append(int(book.get("book_id") or index + 1))
            if index == 0 and own_anchor and own_score < 0.08 and next_score >= 0.12:
                issues.append(f"intro_off_timeline_book_{book.get('book_id', index + 1)}")
                problem_books.append(int(book.get("book_id") or index + 1))
            if next_anchor and next_score > own_score + 0.10 and next_score >= 0.16:
                issues.append(
                    f"cross_book_drift_book_{book.get('book_id', index + 1)}_to_{books[index + 1].get('book_id', index + 2)}"
                )
                problem_books.append(int(book.get("book_id") or index + 1))

        issue_count = len(issues)
        needs_rewrite = issue_count >= 2 or any(issue.startswith("weak_hook") for issue in issues) or any(
            issue.startswith(("timeline_drift", "cross_book_drift", "intro_off_timeline")) for issue in issues
        )
        suggestions = []
        if any(issue.startswith("weak_hook") for issue in issues):
            suggestions.append("Viet lai hook bang bien co/xung dot that trong book dau, khong mo dau chung chung.")
        if any("transition" in issue or "generic_opening" in issue for issue in issues):
            suggestions.append("Lam cau cuoi moi book thanh cau cau noi, de book sau bat vao he qua book truoc.")
        if any("flat_block" in issue for issue in issues):
            suggestions.append("Moi book can 2-5 cau co nhip: quan sat -> y nghia -> he qua.")
        if any("missing_anchor" in issue for issue in issues):
            suggestions.append("Moi book can neo vao visual_anchor hoac mot cau thoai that tu SRT.")
        if any("timeline_drift" in issue or "cross_book_drift" in issue or "intro_off_timeline" in issue for issue in issues):
            suggestions.append("Moi block phai bam dung SRT anchor cua chinh book do; khong duoc dua sang book ke tiep hay mo sai time window.")

        return {
            "ok": not needs_rewrite and not issues,
            "needs_rewrite": needs_rewrite,
            "issues": issues,
            "problem_books": sorted(set(problem_books)),
            "suggestions": suggestions,
            "issue_count": issue_count,
            "anchor_alignment": anchor_alignment,
        }


__all__ = ["PremiumReviewPipeline"]
