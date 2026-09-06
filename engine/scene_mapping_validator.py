"""Validate narration-to-scene mappings for recap blocks."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from engine.scene_cards import TextSignals


class SceneMappingValidator:
    """Non-blocking diagnostics for script blocks.

    The validator never raises for content quality problems. It annotates blocks
    and returns a report so the pipeline can warn, rewrite, or continue.
    """

    LOW_CONFIDENCE = 0.08
    MEDIUM_CONFIDENCE = 0.16

    @staticmethod
    def _as_int_list(value: Any) -> List[int]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            raw = value
        else:
            raw = [value]
        result: List[int] = []
        for item in raw:
            try:
                result.append(int(item))
            except Exception:
                continue
        return result

    @classmethod
    def _block_scene_ids(
        cls,
        block: Dict[str, Any],
        render_block: Dict[str, Any],
    ) -> List[int]:
        scene_ids = cls._as_int_list(block.get("scene_ids"))
        if scene_ids:
            return scene_ids
        scene_ids = cls._as_int_list(render_block.get("scene_ids"))
        if scene_ids:
            return scene_ids
        try:
            return [int(render_block.get("scene_id"))]
        except Exception:
            return []

    @staticmethod
    def _scene_text(card: Dict[str, Any]) -> str:
        return " ".join([
            str(card.get("dialogue_text", "")),
            str(card.get("visual_notes", "")),
            str(card.get("reason", "")),
            " ".join(str(k) for k in card.get("keywords", []) or []),
        ]).strip()

    @classmethod
    def validate(
        cls,
        script_blocks: Iterable[Dict[str, Any]],
        scene_cards: Iterable[Dict[str, Any]],
        render_blocks: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        cards_by_id = {}
        for card in scene_cards or []:
            if not isinstance(card, dict):
                continue
            try:
                cards_by_id[int(card.get("scene_id"))] = card
            except Exception:
                continue

        render_by_block = {}
        for idx, rb in enumerate(render_blocks or [], 1):
            if not isinstance(rb, dict):
                continue
            for key in (rb.get("block_id"), rb.get("book_id"), idx):
                try:
                    render_by_block[int(key)] = rb
                except Exception:
                    continue

        issues: List[Dict[str, Any]] = []
        block_reports: List[Dict[str, Any]] = []

        for idx, block in enumerate(script_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            try:
                block_id = int(block.get("block_id", idx))
            except Exception:
                block_id = idx
            render_block = render_by_block.get(block_id, {})
            scene_ids = cls._block_scene_ids(block, render_block)
            text = str(block.get("text", "") or "")
            anchors = [
                str(block.get("srt_anchor", "") or ""),
                str(block.get("visual_anchor", "") or ""),
                str(block.get("scene_anchor", "") or ""),
                str(render_block.get("dialogue_text", "") or ""),
                str(render_block.get("visual_anchor", "") or ""),
            ]
            for sid in scene_ids:
                if sid in cards_by_id:
                    anchors.append(cls._scene_text(cards_by_id[sid]))
            anchor_text = " ".join(part for part in anchors if part).strip()
            confidence = TextSignals.overlap_score(text, anchor_text) if anchor_text else 0.0

            warnings: List[str] = []
            if scene_ids:
                # Chỉ flag invalid scene_id khi cards_by_id có đủ data (>= 5 scenes)
                # Tránh false positive khi scene detect không chạy hoặc ít scenes
                if cards_by_id and len(cards_by_id) >= 5:
                    invalid = [sid for sid in scene_ids if sid not in cards_by_id]
                    if invalid and len(invalid) == len(scene_ids):
                        # Chỉ flag khi TẤT CẢ scene_ids đều invalid (không phải 1 vài cái)
                        warnings.append(f"invalid_scene_id:{','.join(map(str, invalid[:5]))}")
            elif cards_by_id and len(cards_by_id) >= 5:
                warnings.append("missing_scene_ids")

            if not anchor_text:
                warnings.append("missing_scene_anchor")
            elif confidence < cls.LOW_CONFIDENCE and len(TextSignals.keywords(text, 30)) >= 6:
                # Chỉ flag khi text dài (>=6 keywords) nhưng overlap cực thấp
                # (SRT tiếng Trung → overlap với Việt bình thường thấp → không flag)
                warnings.append("low_scene_overlap")
            elif confidence < cls.MEDIUM_CONFIDENCE and len(TextSignals.keywords(text, 30)) >= 10:
                # Chỉ flag weak khi text rất dài mà vẫn rất thấp overlap
                warnings.append("weak_scene_overlap")

            report = {
                "block_id": block_id,
                "scene_ids": scene_ids,
                "confidence": round(confidence, 3),
                "warnings": warnings,
                "needs_human_review": bool(warnings),
            }
            block_reports.append(report)
            for warning in warnings:
                issues.append({
                    "block_id": block_id,
                    "level": "WARNING",
                    "type": warning,
                    "confidence": round(confidence, 3),
                })

        return {
            "ok": not issues,
            "issue_count": len(issues),
            "issues": issues,
            "blocks": block_reports,
        }

    @classmethod
    def annotate_package(
        cls,
        package: Dict[str, Any],
        scene_cards: Iterable[Dict[str, Any]],
        render_blocks: Iterable[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        script_blocks = package.get("script_blocks") or []
        report = cls.validate(script_blocks, scene_cards, render_blocks)
        by_block = {
            item.get("block_id"): item
            for item in report.get("blocks", [])
            if isinstance(item, dict)
        }
        annotated: List[Dict[str, Any]] = []
        for block in script_blocks:
            if not isinstance(block, dict):
                continue
            item = dict(block)
            try:
                bid = int(item.get("block_id", len(annotated) + 1))
            except Exception:
                bid = len(annotated) + 1
            block_report = by_block.get(bid, {})
            item["mapping_confidence"] = block_report.get("confidence", item.get("mapping_confidence", 0.0))
            stale_scene_warnings = {
                "invalid_scene_id",
                "missing_scene_ids",
                "missing_scene_anchor",
                "low_scene_overlap",
                "weak_scene_overlap",
                "under_target_words",
                "over_target_words",
            }
            previous_warnings = [
                str(w)
                for w in (item.get("warnings") or [])
                if str(w).split(":", 1)[0] not in stale_scene_warnings
            ]
            current_warnings = [str(w) for w in (block_report.get("warnings") or [])]
            item["warnings"] = sorted(set(previous_warnings + current_warnings))
            item["needs_human_review"] = bool(block_report.get("needs_human_review"))
            if block_report.get("scene_ids") and not item.get("scene_ids"):
                item["scene_ids"] = block_report.get("scene_ids")
            annotated.append(item)
        package["script_blocks"] = annotated
        package["scene_mapping_report"] = report
        return package, report
