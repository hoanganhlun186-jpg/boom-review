"""Validate whether generated review script blocks are grounded in source evidence."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from engine.scene_cards import TextSignals


class ScriptGroundingValidator:
    """Lightweight factual grounding check for per-block recap scripts."""

    VERSION = "evidence_v7"
    MIN_CONFIDENCE = 0.22

    @staticmethod
    def _words(text: str) -> List[str]:
        return TextSignals.keywords(text or "", limit=60)

    @staticmethod
    def _fold_text(text: Any) -> str:
        folded = unicodedata.normalize("NFD", str(text or "").lower())
        folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
        folded = folded.replace("đ", "d")
        return re.sub(r"\s+", " ", folded).strip()

    @classmethod
    def _fold_tokens(cls, text: Any) -> List[str]:
        folded = cls._fold_text(text)
        stopwords = {cls._fold_text(word) for word in TextSignals.STOPWORDS}
        tokens = re.findall(r"[a-z0-9]{3,}", folded)
        result: List[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in result:
                result.append(token)
        return result

    @classmethod
    def _is_technical_anchor(cls, value: Any) -> bool:
        folded = cls._fold_text(value)
        if not folded:
            return True
        technical = {
            "semantic_story",
            "action_lookahead",
            "scene_cut",
            "smart_cut",
            "keep_skip",
            "unknown",
        }
        return folded.replace(" ", "_") in technical or folded in technical

    @classmethod
    def _evidence_text(cls, render_block: Dict[str, Any], script_block: Dict[str, Any]) -> str:
        pieces = []
        for source in (render_block, script_block):
            if not isinstance(source, dict):
                continue
            for key in (
                "must_mention",
                "source_srt_anchor",
                "cut_srt_anchor",
                "dialogue_text",
                "srt_anchor",
                "visual_anchor",
                "visual_hint",
                "visual_notes",
                "scene_anchor",
                "must_mention",
                "cut_reason",
                "reason",
                "semantic_reason",
                "narrative_goal",
                "one_main_idea",
            ):
                value = source.get(key)
                if isinstance(value, (list, tuple)):
                    value = " ".join(str(item) for item in value if item)
                if value:
                    pieces.append(str(value))
        return " ".join(pieces)

    @classmethod
    def _primary_anchor_text(cls, render_block: Dict[str, Any], script_block: Dict[str, Any]) -> str:
        pieces = []
        for source in (render_block, script_block):
            if not isinstance(source, dict):
                continue
            for key in ("must_mention", "visual_anchor", "visual_notes", "srt_anchor", "dialogue_text", "scene_anchor", "visual_hint"):
                value = source.get(key)
                if isinstance(value, (list, tuple)):
                    value = " ".join(str(item) for item in value if item)
                if value and not cls._is_technical_anchor(value):
                    pieces.append(str(value))
        return " ".join(pieces)

    @classmethod
    def _strict_required_anchor(
        cls,
        render_block: Dict[str, Any],
        script_block: Dict[str, Any],
    ) -> Tuple[str, str]:
        candidates = []
        for source_name, source in (("render", render_block), ("script", script_block)):
            if not isinstance(source, dict):
                continue
            candidates.extend(
                [
                    ("must_mention", source.get("must_mention")),
                    ("visual_anchor", source.get("visual_anchor")),
                    ("visual_notes", source.get("visual_notes")),
                    ("srt_anchor", source.get("srt_anchor")),
                    ("dialogue_text", source.get("dialogue_text")),
                    ("scene_anchor", source.get("scene_anchor")),
                ]
            )
        for key, value in candidates:
            if isinstance(value, (list, tuple)):
                value = " ".join(str(item) for item in value if item)
            cleaned = TextSignals.clean(value, 220)
            if cleaned and not cls._is_technical_anchor(cleaned) and cls._fold_tokens(cleaned):
                return cleaned, key
        return "", ""

    @classmethod
    def _confidence(cls, script_text: str, evidence_text: str) -> float:
        script_words = set(cls._words(script_text))
        evidence_words = set(cls._words(evidence_text))
        if not script_words:
            return 0.0
        if not evidence_words:
            return 0.0
        overlap = script_words & evidence_words
        # A block can still be valid with paraphrase, so combine direct overlap with evidence coverage.
        direct = len(overlap) / max(1, len(script_words))
        coverage = len(overlap) / max(1, min(len(script_words), len(evidence_words)))
        return round(min(1.0, direct * 0.70 + coverage * 0.30), 3)

    @classmethod
    def _mentions_anchor(cls, script_text: str, anchor_text: str, strict: bool = False) -> bool:
        anchor_words = set(cls._fold_tokens(anchor_text))
        if not anchor_words:
            return True
        script_words = set(cls._fold_tokens(script_text))
        overlap = script_words & anchor_words
        required = 1 if len(anchor_words) <= 2 else 2
        if strict:
            required = max(required, min(4, max(2, round(len(anchor_words) * 0.45))))
        if len(overlap) < required:
            return False

        anchor_folded = cls._fold_text(anchor_text)
        script_folded = cls._fold_text(script_text)
        if "dau" in anchor_folded and "lau" in anchor_folded:
            if not ("dau" in script_folded and "lau" in script_folded):
                return False
            if "ao" in anchor_folded and "ao" not in script_folded:
                return False
            pickup_words = ("cam", "nhat", "vot", "nang", "dua")
            if any(word in anchor_folded for word in pickup_words):
                if not any(word in script_folded for word in pickup_words):
                    return False
        return True

    @classmethod
    def _unsupported_terms(cls, script_text: str, evidence_text: str) -> List[str]:
        script_words = cls._words(script_text)
        evidence_words = set(cls._words(evidence_text))
        unsupported = []
        for word in script_words:
            if word not in evidence_words and word not in unsupported:
                unsupported.append(word)
            if len(unsupported) >= 10:
                break
        return unsupported

    @classmethod
    def validate(
        cls,
        script_blocks: Iterable[Dict[str, Any]],
        render_blocks: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        render_by_id = {}
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            try:
                block_id = int(block.get("block_id") or block.get("book_id") or index)
            except Exception:
                block_id = index
            render_by_id[block_id] = block

        block_reports: List[Dict[str, Any]] = []
        issues: List[str] = []
        for index, script_block in enumerate(script_blocks or [], 1):
            if not isinstance(script_block, dict):
                continue
            try:
                block_id = int(script_block.get("block_id") or script_block.get("book_id") or index)
            except Exception:
                block_id = index
            render_block = render_by_id.get(block_id, {})
            text = str(script_block.get("text") or "")
            evidence = cls._evidence_text(render_block, script_block)
            strict_anchor, anchor_source = cls._strict_required_anchor(render_block, script_block)
            primary_anchor = strict_anchor or cls._primary_anchor_text(render_block, script_block)
            confidence = cls._confidence(text, evidence)
            unsupported = cls._unsupported_terms(text, evidence)
            has_evidence = bool(cls._words(evidence))
            block_issues: List[str] = []
            if not has_evidence:
                block_issues.append("missing_block_evidence")
            elif confidence < cls.MIN_CONFIDENCE:
                block_issues.append("weak_evidence_overlap")
            if primary_anchor and not cls._mentions_anchor(text, primary_anchor, strict=bool(strict_anchor)):
                block_issues.append("missing_required_scene_anchor")
            if block_issues:
                issues.append(f"block_{block_id}:{','.join(block_issues)}")
            block_reports.append(
                {
                    "block_id": block_id,
                    "grounding_confidence": confidence,
                    "issues": block_issues,
                    "unsupported_terms": unsupported,
                    "evidence_preview": TextSignals.clean(evidence, 260),
                    "required_anchor_preview": TextSignals.clean(primary_anchor, 180),
                    "required_anchor_source": anchor_source,
                    "strict_anchor": bool(strict_anchor),
                    "needs_rewrite": bool(block_issues),
                }
            )

        weak_count = sum(1 for item in block_reports if item.get("needs_rewrite"))
        avg = (
            sum(float(item.get("grounding_confidence", 0.0) or 0.0) for item in block_reports)
            / len(block_reports)
            if block_reports
            else 0.0
        )
        return {
            "version": cls.VERSION,
            "block_count": len(block_reports),
            "issue_count": len(issues),
            "weak_block_count": weak_count,
            "average_confidence": round(avg, 3),
            "needs_rewrite": weak_count > 0,
            "issues": issues,
            "blocks": block_reports,
        }

    @classmethod
    def annotate_package(
        cls,
        package: Dict[str, Any],
        render_blocks: Sequence[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        package = dict(package or {})
        script_blocks = [dict(block) for block in (package.get("script_blocks") or []) if isinstance(block, dict)]
        report = cls.validate(script_blocks, render_blocks or [])
        report_by_id = {item.get("block_id"): item for item in report.get("blocks", [])}
        annotated = []
        for block in script_blocks:
            try:
                block_id = int(block.get("block_id") or block.get("book_id") or len(annotated) + 1)
            except Exception:
                block_id = len(annotated) + 1
            block_report = report_by_id.get(block_id, {})
            item = dict(block)
            item["grounding_confidence"] = block_report.get("grounding_confidence", 0.0)
            item["grounding_warnings"] = block_report.get("issues", [])
            item["unsupported_terms"] = block_report.get("unsupported_terms", [])
            annotated.append(item)
        package["script_blocks"] = annotated
        package["script_grounding_report"] = report
        package["review_script_version"] = cls.VERSION
        return package, report
