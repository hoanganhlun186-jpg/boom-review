"""Chapter/map-reduce planning for long recap timelines."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


class ChapterRecapPlanner:
    VERSION = "chapter_recap_plan_v1"

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _text(*values: Any, limit: int = 180) -> str:
        parts = []
        for value in values:
            text = str(value or "").replace("\n", " ").strip()
            if text:
                parts.append(text)
        return " | ".join(parts)[:limit]

    @classmethod
    def _block_duration(cls, block: Dict[str, Any]) -> float:
        duration = cls._float(block.get("duration"))
        if duration > 0:
            return duration
        start = cls._float(block.get("start_in_final_video"))
        end = cls._float(block.get("end_in_final_video"))
        return max(0.0, end - start)

    @classmethod
    def build_chapter_plan(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        target_chapter_seconds: float = 180.0,
        max_chapters: int = 10,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        blocks = [dict(block or {}) for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return [], {"version": cls.VERSION, "chapter_count": 0, "block_count": 0, "chapters": []}

        target_chapter_seconds = max(60.0, float(target_chapter_seconds or 180.0))
        total_duration = sum(max(0.1, cls._block_duration(block)) for block in blocks)
        desired_chapters = max(1, min(int(max_chapters or 10), int(round(total_duration / target_chapter_seconds)) or 1))
        target_per_chapter = max(60.0, total_duration / desired_chapters)

        chapters: List[Dict[str, Any]] = []
        current: List[Dict[str, Any]] = []
        current_duration = 0.0

        def flush() -> None:
            nonlocal current, current_duration
            if not current:
                return
            chapter_id = len(chapters) + 1
            first = current[0]
            last = current[-1]
            start = cls._float(first.get("start_in_final_video"))
            last_start = cls._float(last.get("start_in_final_video"))
            end = cls._float(last.get("end_in_final_video"), last_start + cls._block_duration(last))
            chapter_words = sum(int(block.get("target_words") or 0) for block in current)
            anchor = cls._text(
                first.get("must_mention"),
                first.get("srt_anchor"),
                first.get("source_srt_anchor"),
                first.get("visual_anchor"),
                limit=220,
            )
            title = f"Chapter {chapter_id}: {start:.0f}-{end:.0f}s"
            goal = (
                "Keep a continuous recap arc from the first included scene to the last, "
                "using concrete SRT/visual anchors from these blocks."
            )
            chapters.append(
                {
                    "chapter_id": chapter_id,
                    "title": title,
                    "start_in_final_video": round(start, 3),
                    "end_in_final_video": round(end, 3),
                    "duration": round(max(0.0, end - start), 3),
                    "block_ids": [block.get("block_id") or block.get("book_id") or i + 1 for i, block in enumerate(current)],
                    "block_count": len(current),
                    "target_words": chapter_words,
                    "opening_anchor": anchor,
                    "goal": goal,
                }
            )
            for position, block in enumerate(current, 1):
                block["chapter_id"] = chapter_id
                block["chapter_title"] = title
                block["chapter_goal"] = goal
                block["chapter_position"] = position
                block["chapter_block_count"] = len(current)
                block["chapter_target_words"] = chapter_words
            current = []
            current_duration = 0.0

        for block in blocks:
            duration = max(0.1, cls._block_duration(block))
            if current and len(chapters) < desired_chapters - 1 and current_duration + duration > target_per_chapter * 1.15:
                flush()
            current.append(block)
            current_duration += duration
        flush()

        report = {
            "version": cls.VERSION,
            "chapter_count": len(chapters),
            "block_count": len(blocks),
            "total_duration": round(total_duration, 3),
            "target_chapter_seconds": round(target_chapter_seconds, 3),
            "chapters": chapters,
            "coverage": {
                "first_block_id": blocks[0].get("block_id") or blocks[0].get("book_id") or 1,
                "last_block_id": blocks[-1].get("block_id") or blocks[-1].get("book_id") or len(blocks),
                "all_blocks_assigned": all(block.get("chapter_id") for block in blocks),
            },
        }
        return blocks, report

    @staticmethod
    def build_prompt_context(report: Dict[str, Any], max_chapters: int = 10, max_chars: int = 5200) -> str:
        chapters = [chapter for chapter in (report or {}).get("chapters") or [] if isinstance(chapter, dict)]
        if not chapters:
            return ""
        lines = [
            "CHAPTER MAP-REDUCE LOCK:",
            "- First understand the whole chapter plan, then write each block as one continuous recap.",
            "- Do not write isolated captions. Each block must connect to the previous and next block.",
            "- Preserve every chapter's key anchor; never drop the tail/end of the cut video.",
        ]
        for chapter in chapters[:max_chapters]:
            block_ids = chapter.get("block_ids") or []
            if len(block_ids) > 8:
                block_text = f"{block_ids[0]}..{block_ids[-1]} ({len(block_ids)} blocks)"
            else:
                block_text = ", ".join(str(item) for item in block_ids)
            lines.append(
                f"- Chapter {chapter.get('chapter_id')}: blocks {block_text}, "
                f"{chapter.get('start_in_final_video')}->{chapter.get('end_in_final_video')}s, "
                f"~{chapter.get('target_words', 0)} words. Anchor: {chapter.get('opening_anchor') or '[none]'}"
            )
        return "\n".join(lines)[:max_chars]
