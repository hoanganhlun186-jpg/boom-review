"""Transcript-first story context for recap generation."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


class TranscriptFirstContextBuilder:
    VERSION = "transcript_first_context_v1"

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _clean(value: Any, limit: int = 220) -> str:
        return " ".join(str(value or "").replace("\n", " ").split())[:limit]

    @classmethod
    def _overlap_subtitles(
        cls,
        subtitles: Iterable[Dict[str, Any]],
        start: float,
        end: float,
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        for sub in subtitles or []:
            if not isinstance(sub, dict):
                continue
            sub_start = cls._float(sub.get("start_seconds"))
            sub_end = cls._float(sub.get("end_seconds"), sub_start)
            if sub_end >= start - 0.5 and sub_start <= end + 0.5:
                hits.append(sub)
            if len(hits) >= limit:
                break
        return hits

    @classmethod
    def build(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        source_subtitles: Iterable[Dict[str, Any]],
        story_outline: Dict[str, Any] | None = None,
        max_spine_items: int = 36,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        blocks = [dict(block or {}) for block in (render_blocks or []) if isinstance(block, dict)]
        subtitles = [dict(sub or {}) for sub in (source_subtitles or []) if isinstance(sub, dict)]
        spine: List[Dict[str, Any]] = []
        subtitle_hits = 0

        for index, block in enumerate(blocks, 1):
            final_start = cls._float(block.get("start_in_final_video"))
            final_end = cls._float(block.get("end_in_final_video"), final_start + cls._float(block.get("duration"), 0.0))
            orig_start = cls._float(block.get("original_start"), final_start)
            orig_end = cls._float(block.get("original_end"), orig_start + cls._float(block.get("duration"), 0.0))
            hits = cls._overlap_subtitles(subtitles, orig_start, orig_end, limit=5)
            dialogue = " | ".join(cls._clean(hit.get("text"), 180) for hit in hits if hit.get("text"))
            if dialogue:
                subtitle_hits += 1
            anchor = cls._clean(
                dialogue
                or block.get("source_srt_anchor")
                or block.get("srt_anchor")
                or block.get("dialogue_text")
                or block.get("must_mention")
                or block.get("visual_anchor"),
                260,
            )
            block["transcript_spine_anchor"] = anchor
            block["transcript_spine_source"] = "srt" if dialogue else ("visual_or_existing" if anchor else "missing")
            spine.append(
                {
                    "block_id": block.get("block_id") or block.get("book_id") or index,
                    "final_start": round(final_start, 3),
                    "final_end": round(final_end, 3),
                    "original_start": round(orig_start, 3),
                    "original_end": round(orig_end, 3),
                    "anchor": anchor,
                    "source": block["transcript_spine_source"],
                    "chapter_id": block.get("chapter_id"),
                }
            )

        if len(spine) > max_spine_items:
            keep_indexes = set()
            count = len(spine)
            for i in range(min(8, count)):
                keep_indexes.add(i)
                keep_indexes.add(count - 1 - i)
            if count > 16:
                step = max(1, count // max(1, max_spine_items - len(keep_indexes)))
                for i in range(0, count, step):
                    keep_indexes.add(i)
            sampled = [spine[i] for i in sorted(keep_indexes)[:max_spine_items]]
        else:
            sampled = spine

        acts = []
        outline = story_outline or {}
        for item in outline.get("acts") or outline.get("chapters") or []:
            if isinstance(item, dict):
                acts.append(
                    {
                        "title": cls._clean(item.get("title") or item.get("name") or "", 120),
                        "summary": cls._clean(item.get("summary") or item.get("description") or "", 260),
                    }
                )

        report = {
            "version": cls.VERSION,
            "block_count": len(blocks),
            "source_subtitle_count": len(subtitles),
            "blocks_with_srt_spine": subtitle_hits,
            "blocks_with_any_spine": sum(1 for item in spine if item.get("anchor")),
            "srt_spine_ratio": round(subtitle_hits / max(1, len(blocks)), 3),
            "sampled_spine": sampled,
            "outline_acts": acts[:8],
        }
        return blocks, report

    @staticmethod
    def build_prompt_context(report: Dict[str, Any], max_chars: int = 8200) -> str:
        if not report:
            return ""
        lines = [
            "TRANSCRIPT-FIRST STORY LOCK:",
            "- Build the review from the chronological transcript spine first, then polish the narration.",
            "- The script must mention concrete events from the cut blocks, not generic plot talk.",
            "- If a block has no SRT, use its visual/must_mention anchor and connect it to nearby SRT blocks.",
        ]
        acts = report.get("outline_acts") or []
        if acts:
            lines.append("Story outline:")
            for act in acts[:6]:
                if isinstance(act, dict):
                    lines.append(f"- {act.get('title') or 'Act'}: {act.get('summary') or ''}")
        lines.append("Chronological spine sampled from cut video:")
        for item in (report.get("sampled_spine") or [])[:40]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- Block {item.get('block_id')} | cut {item.get('final_start')}->{item.get('final_end')}s | "
                f"source {item.get('original_start')}->{item.get('original_end')}s | {item.get('anchor') or '[missing anchor]'}"
            )
        return "\n".join(lines)[:max_chars]
