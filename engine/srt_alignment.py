"""Triangulate source SRT, cut-video timeline, and generated narration."""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from engine.scene_cards import TextSignals


class SrtAlignmentValidator:
    """Keep recap narration locked to the SRT evidence of the cut video.

    The source SRT remains on the original movie timeline. Render blocks map
    that original range into the final cut-video timeline. This validator builds
    a virtual "cut SRT" from that mapping, then checks that generated script and
    voice still mention the evidence that is visible in each block.
    """

    VERSION = "srt_alignment_v1"
    MIN_OVERLAP = 0.10
    MAX_WEAK_RATIO = 0.45
    MAX_SNIPPETS_PER_BLOCK = 4

    ACTION_TERMS = {
        "dau lau",
        "dau",
        "lau",
        "ao",
        "xac",
        "chet",
        "giet",
        "mau",
        "bi mat",
        "phat hien",
        "cam",
        "nhat",
        "vot",
        "nang",
        "roi",
        "nga",
        "dao",
        "kiem",
        "lua",
        "no",
        "skull",
        "corpse",
        "blood",
        "dead",
        "murder",
        "pond",
    }

    TECHNICAL_ANCHORS = {
        "semantic_story",
        "action_lookahead",
        "scene_cut",
        "smart_cut",
        "keep_skip",
        "unknown",
    }

    @staticmethod
    def _clean(value: Any, limit: Optional[int] = None) -> str:
        return TextSignals.clean(value, limit)

    @staticmethod
    def _seconds(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @classmethod
    def _fold_text(cls, value: Any) -> str:
        folded = unicodedata.normalize("NFD", str(value or "").lower())
        folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
        folded = folded.replace("đ", "d")
        return re.sub(r"\s+", " ", folded).strip()

    @classmethod
    def _tokens(cls, value: Any, limit: int = 80) -> List[str]:
        folded = cls._fold_text(value)
        stopwords = {cls._fold_text(word) for word in TextSignals.STOPWORDS}
        tokens = re.findall(r"[a-z0-9]{3,}", folded)
        result: List[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in result:
                result.append(token)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _overlap_score(cls, left: Any, right: Any) -> float:
        left_set = set(cls._tokens(left, 100))
        right_set = set(cls._tokens(right, 100))
        if not left_set or not right_set:
            return 0.0
        return round(len(left_set & right_set) / float(min(len(left_set), len(right_set))), 3)

    @classmethod
    def _is_technical_anchor(cls, value: Any) -> bool:
        folded = cls._fold_text(value).replace(" ", "_")
        return not folded or folded in cls.TECHNICAL_ANCHORS

    @classmethod
    def _has_action_terms(cls, value: Any) -> bool:
        folded = cls._fold_text(value)
        if not folded:
            return False
        return any(term in folded for term in cls.ACTION_TERMS)

    @classmethod
    def _mentions_anchor(cls, script_text: Any, anchor_text: Any, strict: bool = False) -> bool:
        anchor_tokens = set(cls._tokens(anchor_text, 40))
        if not anchor_tokens:
            return True
        script_tokens = set(cls._tokens(script_text, 120))
        overlap = script_tokens & anchor_tokens
        required = 1 if len(anchor_tokens) <= 2 else 2
        if strict:
            required = max(required, min(4, max(2, round(len(anchor_tokens) * 0.40))))
        if len(overlap) < required:
            return False

        anchor_folded = cls._fold_text(anchor_text)
        script_folded = cls._fold_text(script_text)
        if "dau" in anchor_folded and "lau" in anchor_folded:
            if not ("dau" in script_folded and "lau" in script_folded):
                return False
            if "ao" in anchor_folded and "ao" not in script_folded:
                return False
        return True

    @classmethod
    def _normalize_subtitles(cls, subtitles_source: Any) -> List[Dict[str, Any]]:
        if not subtitles_source:
            return []
        if isinstance(subtitles_source, str):
            if not os.path.exists(subtitles_source):
                return []
            from engine.srt_processor import SRTParser

            try:
                subtitles = SRTParser.parse_srt(subtitles_source)
            except Exception:
                return []
        else:
            subtitles = list(subtitles_source or [])

        normalized: List[Dict[str, Any]] = []
        for index, sub in enumerate(subtitles, 1):
            if not isinstance(sub, dict):
                continue
            start = cls._seconds(sub.get("start_seconds", sub.get("start")), 0.0)
            end = cls._seconds(sub.get("end_seconds", sub.get("end")), start)
            text = cls._clean(sub.get("text") or sub.get("dialogue") or "")
            if not text:
                continue
            normalized.append(
                {
                    "index": sub.get("index", index),
                    "start_seconds": start,
                    "end_seconds": max(start, end),
                    "text": text,
                }
            )
        return normalized

    @classmethod
    def _collect_snippets(
        cls,
        subtitles: Sequence[Dict[str, Any]],
        start: float,
        end: float,
        max_snippets: int = MAX_SNIPPETS_PER_BLOCK,
        pad: float = 0.35,
    ) -> List[str]:
        snippets: List[str] = []
        if end < start:
            start, end = end, start
        for sub in subtitles or []:
            sub_start = cls._seconds(sub.get("start_seconds"), 0.0)
            sub_end = cls._seconds(sub.get("end_seconds"), sub_start)
            if sub_end < start - pad or sub_start > end + pad:
                continue
            text = cls._clean(sub.get("text"), 170)
            if text and text not in snippets:
                snippets.append(text)
            if len(snippets) >= max_snippets:
                break
        return snippets

    @classmethod
    def _anchor_from_snippets(cls, snippets: Sequence[str], limit: int = 360) -> str:
        return cls._clean(" | ".join(snippet for snippet in snippets if snippet), limit)

    @classmethod
    def _block_id(cls, block: Dict[str, Any], index: int) -> int:
        for key in ("block_id", "book_id"):
            try:
                value = int(block.get(key))
                if value > 0:
                    return value
            except Exception:
                continue
        return index

    @classmethod
    def _block_times(cls, block: Dict[str, Any]) -> Tuple[float, float, float, float]:
        final_start = cls._seconds(block.get("start_in_final_video"), cls._seconds(block.get("start"), 0.0))
        duration = cls._seconds(block.get("duration"), 0.0)
        final_end = cls._seconds(block.get("end_in_final_video"), final_start + duration)
        if final_end <= final_start and duration > 0:
            final_end = final_start + duration
        original_start = cls._seconds(block.get("original_start"), final_start)
        original_end = cls._seconds(block.get("original_end"), final_end)
        if original_end <= original_start:
            original_end = original_start + max(0.1, final_end - final_start)
        return final_start, final_end, original_start, original_end

    @classmethod
    def _choose_required_anchor(cls, block: Dict[str, Any]) -> Tuple[str, str]:
        candidates = [
            ("srt_must_mention", block.get("srt_must_mention")),
            ("must_mention", block.get("must_mention")),
            ("visual_anchor", block.get("visual_anchor")),
            ("triangulated_srt_anchor", block.get("triangulated_srt_anchor")),
            ("cut_visible_srt", block.get("cut_visible_srt")),
            ("source_srt_anchor", block.get("source_srt_anchor")),
            ("srt_anchor", block.get("srt_anchor")),
        ]
        for source, value in candidates:
            text = cls._clean(value, 220)
            if text and not cls._is_technical_anchor(text) and cls._has_action_terms(text):
                return text, source
        for source, value in candidates[:2]:
            text = cls._clean(value, 220)
            if text and not cls._is_technical_anchor(text) and len(cls._tokens(text, 20)) >= 2:
                return text, source
        return "", ""

    @classmethod
    def enrich_render_blocks(
        cls,
        render_blocks: Sequence[Dict[str, Any]],
        source_subtitles: Any = None,
        cut_subtitles: Any = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Attach source/cut SRT anchors to each render block."""

        source = cls._normalize_subtitles(source_subtitles)
        cut = cls._normalize_subtitles(cut_subtitles)
        blocks = [dict(block) for block in (render_blocks or []) if isinstance(block, dict)]
        blocks.sort(key=lambda item: cls._block_times(item)[0])

        enriched: List[Dict[str, Any]] = []
        block_reports: List[Dict[str, Any]] = []
        previous_original_end: Optional[float] = None

        for index, block in enumerate(blocks, 1):
            block_id = cls._block_id(block, index)
            final_start, final_end, original_start, original_end = cls._block_times(block)
            source_snippets = cls._collect_snippets(source, original_start, original_end)
            cut_snippets = cls._collect_snippets(cut, final_start, final_end) if cut else []
            skipped_snippets: List[str] = []
            if previous_original_end is not None and original_start > previous_original_end + 1.0:
                skipped_snippets = cls._collect_snippets(
                    source,
                    previous_original_end,
                    original_start,
                    max_snippets=2,
                    pad=0.0,
                )
            previous_original_end = max(previous_original_end or original_end, original_end)

            source_anchor = cls._anchor_from_snippets(source_snippets)
            cut_anchor = cls._anchor_from_snippets(cut_snippets)
            virtual_cut_anchor = cut_anchor or source_anchor
            existing_anchor = cls._clean(
                block.get("srt_anchor")
                or block.get("dialogue_text")
                or block.get("visual_anchor")
                or block.get("cut_reason")
                or "",
                360,
            )
            triangulated_anchor = virtual_cut_anchor or existing_anchor

            item = dict(block)
            item["block_id"] = block_id
            item.setdefault("book_id", block_id)
            if source_anchor:
                item["source_srt_anchor"] = source_anchor
            if cut_anchor:
                item["cut_srt_anchor"] = cut_anchor
                item["cut_srt_source"] = "cut_srt_file"
            elif source_anchor:
                item["cut_srt_anchor"] = source_anchor
                item["cut_srt_source"] = "virtual_from_source_srt"
            item["cut_visible_srt"] = virtual_cut_anchor
            item["triangulated_srt_anchor"] = triangulated_anchor
            item["source_only_context"] = cls._anchor_from_snippets(skipped_snippets, 260)
            item["srt_alignment_status"] = "ok" if triangulated_anchor else "missing_srt"

            required_anchor = ""
            if cls._has_action_terms(triangulated_anchor):
                required_anchor = triangulated_anchor
            elif not cls._is_technical_anchor(item.get("must_mention")):
                required_anchor = cls._clean(item.get("must_mention"), 220)
            if required_anchor:
                item["srt_must_mention"] = cls._clean(required_anchor, 220)
                if not item.get("must_mention") or cls._is_technical_anchor(item.get("must_mention")):
                    item["must_mention"] = item["srt_must_mention"]

            enriched.append(item)
            block_reports.append(
                {
                    "block_id": block_id,
                    "final_time": [round(final_start, 3), round(final_end, 3)],
                    "source_time": [round(original_start, 3), round(original_end, 3)],
                    "source_srt": source_anchor,
                    "cut_srt": virtual_cut_anchor,
                    "cut_srt_source": item.get("cut_srt_source", "none"),
                    "source_only_context": item.get("source_only_context", ""),
                    "required_anchor": item.get("srt_must_mention", ""),
                    "status": item["srt_alignment_status"],
                }
            )

        source_covered = sum(1 for item in block_reports if item.get("source_srt"))
        cut_covered = sum(1 for item in block_reports if item.get("cut_srt"))
        report = {
            "version": cls.VERSION,
            "mode": "virtual_cut_srt" if source and not cut else ("source_plus_cut_srt" if cut else "no_srt"),
            "block_count": len(block_reports),
            "source_srt_block_count": source_covered,
            "cut_srt_block_count": cut_covered,
            "source_srt_coverage_ratio": round(source_covered / max(1, len(block_reports)), 3),
            "cut_srt_coverage_ratio": round(cut_covered / max(1, len(block_reports)), 3),
            "source_only_context_count": sum(1 for item in block_reports if item.get("source_only_context")),
            "blocks": block_reports,
        }
        return enriched, report

    @classmethod
    def build_prompt_context(
        cls,
        render_blocks: Sequence[Dict[str, Any]],
        report: Optional[Dict[str, Any]] = None,
        max_blocks: int = 120,
    ) -> str:
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return ""
        lines = [
            "SRT TRIANGULATION LOCK:",
            "- CUT_VISIBLE_SRT is what exists inside the cut video block; narration must follow it.",
            "- SOURCE_ONLY_CONTEXT is skipped original-film dialogue between cut blocks; use only as bridge context, never as on-screen action.",
            "- If MUST_MENTION_SRT exists, the script block must mention that exact event/object in the same block.",
        ]
        if report:
            lines.append(
                "- Coverage: "
                f"source {report.get('source_srt_block_count', 0)}/{report.get('block_count', 0)}, "
                f"cut {report.get('cut_srt_block_count', 0)}/{report.get('block_count', 0)}."
            )
        for index, block in enumerate(blocks[:max_blocks], 1):
            block_id = cls._block_id(block, index)
            final_start, final_end, original_start, original_end = cls._block_times(block)
            lines.append(
                f"- Block {block_id} | cut_time {final_start:.1f}-{final_end:.1f}s"
                f" | source_time {original_start:.1f}-{original_end:.1f}s"
                f" | CUT_VISIBLE_SRT: {cls._clean(block.get('cut_visible_srt') or block.get('triangulated_srt_anchor') or '', 220) or '[none]'}"
                f" | MUST_MENTION_SRT: {cls._clean(block.get('srt_must_mention') or '', 160) or '[none]'}"
                f" | SOURCE_ONLY_CONTEXT: {cls._clean(block.get('source_only_context') or '', 160) or '[none]'}"
            )
        if len(blocks) > max_blocks:
            lines.append(f"... {len(blocks) - max_blocks} more blocks follow the same lock.")
        return "\n".join(lines)

    @classmethod
    def _voice_text_for_window(
        cls,
        voice_subtitles: Sequence[Dict[str, Any]],
        start: float,
        end: float,
    ) -> str:
        snippets = cls._collect_snippets(voice_subtitles, start, end, max_snippets=8, pad=0.5)
        return cls._anchor_from_snippets(snippets, 420)

    @classmethod
    def validate(
        cls,
        script_blocks: Iterable[Dict[str, Any]],
        render_blocks: Iterable[Dict[str, Any]],
        voice_subtitles: Any = None,
    ) -> Dict[str, Any]:
        render_by_id: Dict[int, Dict[str, Any]] = {}
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            block_id = cls._block_id(block, index)
            render_by_id[block_id] = block

        voice = cls._normalize_subtitles(voice_subtitles)
        block_reports: List[Dict[str, Any]] = []
        issues: List[Dict[str, Any]] = []

        for index, block in enumerate(script_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            block_id = cls._block_id(block, index)
            render_block = render_by_id.get(block_id, {})
            final_start, final_end, _, _ = cls._block_times(render_block)
            script_text = str(block.get("text") or "")
            evidence = " ".join(
                cls._clean(value)
                for value in (
                    render_block.get("triangulated_srt_anchor"),
                    render_block.get("cut_visible_srt"),
                    render_block.get("cut_srt_anchor"),
                    render_block.get("source_srt_anchor"),
                    render_block.get("srt_anchor"),
                    render_block.get("dialogue_text"),
                    block.get("srt_anchor"),
                    block.get("visual_hint"),
                )
                if value
            ).strip()
            required_anchor, required_source = cls._choose_required_anchor(render_block)
            confidence = cls._overlap_score(script_text, evidence) if evidence else 0.0
            warnings: List[str] = []
            errors: List[str] = []

            if not evidence:
                warnings.append("missing_srt_evidence")
            elif confidence < cls.MIN_OVERLAP and len(cls._tokens(script_text, 20)) >= 4:
                warnings.append("weak_srt_overlap")

            if required_anchor and not cls._mentions_anchor(script_text, required_anchor, strict=True):
                errors.append("missing_cut_evidence_anchor")

            source_only_context = render_block.get("source_only_context") or ""
            if source_only_context and cls._has_action_terms(source_only_context):
                if cls._mentions_anchor(script_text, source_only_context, strict=True):
                    cut_anchor = render_block.get("cut_visible_srt") or render_block.get("triangulated_srt_anchor") or ""
                    if not cls._mentions_anchor(cut_anchor, source_only_context, strict=True):
                        errors.append("mentions_skipped_source_context")

            voice_text = ""
            if voice:
                voice_text = cls._voice_text_for_window(voice, final_start, final_end)
                if not voice_text:
                    warnings.append("missing_voice_srt_window")
                elif required_anchor and not cls._mentions_anchor(voice_text, required_anchor, strict=True):
                    errors.append("voice_srt_missing_required_anchor")

            for warning in warnings:
                issues.append(
                    {
                        "block_id": block_id,
                        "level": "WARNING",
                        "type": warning,
                        "confidence": confidence,
                    }
                )
            for error in errors:
                issues.append(
                    {
                        "block_id": block_id,
                        "level": "ERROR",
                        "type": error,
                        "confidence": confidence,
                        "required_anchor": cls._clean(required_anchor, 180),
                    }
                )

            block_reports.append(
                {
                    "block_id": block_id,
                    "confidence": confidence,
                    "warnings": warnings,
                    "errors": errors,
                    "required_anchor": cls._clean(required_anchor, 180),
                    "required_anchor_source": required_source,
                    "evidence_preview": cls._clean(evidence, 260),
                    "source_only_context": cls._clean(source_only_context, 180),
                    "voice_preview": cls._clean(voice_text, 180),
                    "needs_rewrite": bool(errors or warnings),
                }
            )

        error_count = sum(1 for item in block_reports if item.get("errors"))
        weak_count = sum(1 for item in block_reports if item.get("warnings"))
        voice_issue_count = sum(
            1
            for item in block_reports
            if "missing_voice_srt_window" in (item.get("warnings") or [])
            or "voice_srt_missing_required_anchor" in (item.get("errors") or [])
        )
        weak_ratio = round(weak_count / max(1, len(block_reports)), 3)
        ready = error_count == 0 and weak_ratio <= cls.MAX_WEAK_RATIO
        return {
            "version": cls.VERSION,
            "ready": bool(ready),
            "ok": not issues,
            "block_count": len(block_reports),
            "issue_count": len(issues),
            "error_count": error_count,
            "weak_block_count": weak_count,
            "weak_block_ratio": weak_ratio,
            "voice_issue_count": voice_issue_count,
            "needs_rewrite": not ready,
            "issues": issues,
            "blocks": block_reports,
        }

    @classmethod
    def annotate_package(
        cls,
        package: Dict[str, Any],
        render_blocks: Sequence[Dict[str, Any]],
        voice_subtitles: Any = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        package = dict(package or {})
        script_blocks = [dict(block) for block in (package.get("script_blocks") or []) if isinstance(block, dict)]
        report = cls.validate(script_blocks, render_blocks or [], voice_subtitles=voice_subtitles)
        by_block = {
            item.get("block_id"): item
            for item in report.get("blocks", [])
            if isinstance(item, dict)
        }
        annotated: List[Dict[str, Any]] = []
        for index, block in enumerate(script_blocks, 1):
            item = dict(block)
            block_id = cls._block_id(item, index)
            block_report = by_block.get(block_id, {})
            item["srt_alignment_confidence"] = block_report.get("confidence", 0.0)
            item["srt_alignment_warnings"] = block_report.get("warnings", [])
            item["srt_alignment_errors"] = block_report.get("errors", [])
            item["srt_required_anchor"] = block_report.get("required_anchor", "")
            if block_report.get("needs_rewrite"):
                item["needs_rewrite"] = True
            annotated.append(item)
        package["script_blocks"] = annotated
        package["srt_alignment_report"] = report
        package["review_script_version"] = package.get("review_script_version") or cls.VERSION
        return package, report
