"""Semantic scene retrieval checks for script-to-block grounding."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple


class SceneRetrievalValidator:
    VERSION = "scene_retrieval_v1"
    STOPWORDS = {
        "la", "thi", "ma", "va", "voi", "cua", "cho", "mot", "nhung", "cac", "da",
        "dang", "bi", "duoc", "trong", "ngoai", "khi", "sau", "truoc", "nay", "do",
        "day", "roi", "lai", "de", "tu", "ve", "vao", "ra", "len", "xuong", "nhu",
        "the", "neu", "khong", "co", "anh", "chi", "nguoi", "canh", "phim",
    }

    @staticmethod
    def normalize(text: Any) -> str:
        value = unicodedata.normalize("NFD", str(text or "").lower())
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        value = value.replace("đ", "d")
        return value

    @classmethod
    def tokens(cls, text: Any) -> set[str]:
        words = re.findall(r"[a-z0-9]+", cls.normalize(text))
        return {word for word in words if len(word) >= 3 and word not in cls.STOPWORDS}

    @staticmethod
    def _join_fields(block: Dict[str, Any], fields: Sequence[str]) -> str:
        values = []
        for field in fields:
            value = block.get(field)
            if isinstance(value, list):
                value = " ".join(str(item) for item in value)
            if value:
                values.append(str(value))
        return " ".join(values)

    @classmethod
    def block_evidence_text(cls, block: Dict[str, Any]) -> str:
        return cls._join_fields(
            block,
            (
                "must_mention",
                "visual_anchor",
                "visual_hint",
                "visual_notes",
                "dialogue_text",
                "srt_anchor",
                "source_srt_anchor",
                "cut_visible_srt",
                "triangulated_srt_anchor",
                "transcript_spine_anchor",
                "cut_reason",
                "scene_role_label",
            ),
        )

    @classmethod
    def score_text_to_block(cls, text: Any, block: Dict[str, Any]) -> float:
        query_tokens = cls.tokens(text)
        evidence_tokens = cls.tokens(cls.block_evidence_text(block))
        if not query_tokens or not evidence_tokens:
            return 0.0
        overlap = len(query_tokens & evidence_tokens)
        precision = overlap / max(1, len(query_tokens))
        recall = overlap / max(1, min(len(evidence_tokens), len(query_tokens) + 8))
        return round((precision * 0.72) + (recall * 0.28), 4)

    @classmethod
    def rank_candidates(
        cls,
        text: Any,
        render_blocks: Iterable[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        ranked = []
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            block_id = block.get("block_id") or block.get("book_id") or index
            score = cls.score_text_to_block(text, block)
            ranked.append(
                {
                    "block_id": block_id,
                    "score": score,
                    "anchor": str(block.get("must_mention") or block.get("visual_anchor") or block.get("srt_anchor") or "")[:220],
                }
            )
        ranked.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return ranked[:max(1, top_k)]

    @classmethod
    def validate(
        cls,
        package: Dict[str, Any],
        render_blocks: Iterable[Dict[str, Any]],
        min_own_score: float = 0.055,
        mismatch_margin: float = 0.16,
    ) -> Dict[str, Any]:
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        by_id: Dict[int, Dict[str, Any]] = {}
        for index, block in enumerate(blocks, 1):
            for key in (block.get("block_id"), block.get("book_id"), index):
                try:
                    by_id[int(key)] = block
                except Exception:
                    pass

        issues: List[Dict[str, Any]] = []
        checked = 0
        weak = 0
        mismatched = 0
        block_reports: List[Dict[str, Any]] = []
        for index, script_block in enumerate(package.get("script_blocks") or [], 1):
            if not isinstance(script_block, dict):
                continue
            text = script_block.get("text") or ""
            if not str(text).strip():
                continue
            checked += 1
            try:
                block_id = int(script_block.get("block_id") or script_block.get("book_id") or index)
            except Exception:
                block_id = index
            mapped_block = by_id.get(block_id, {})
            own_score = cls.score_text_to_block(text, mapped_block) if mapped_block else 0.0
            ranked = cls.rank_candidates(text, blocks, top_k=3)
            best = ranked[0] if ranked else {"block_id": block_id, "score": 0.0}
            best_id = best.get("block_id")
            try:
                best_id_int = int(best_id)
            except Exception:
                best_id_int = block_id
            best_score = float(best.get("score", 0.0) or 0.0)
            block_issue_types: List[str] = []
            if own_score < min_own_score:
                weak += 1
                block_issue_types.append("weak_scene_retrieval")
            if best_id_int != block_id and best_score >= max(min_own_score + mismatch_margin, own_score + mismatch_margin):
                mismatched += 1
                block_issue_types.append("retrieval_prefers_other_block")
            if block_issue_types:
                issues.append(
                    {
                        "level": "WARNING",
                        "type": ",".join(block_issue_types),
                        "block_id": block_id,
                        "own_score": round(own_score, 4),
                        "best_block_id": best_id,
                        "best_score": round(best_score, 4),
                    }
                )
            block_reports.append(
                {
                    "block_id": block_id,
                    "own_score": round(own_score, 4),
                    "top_candidates": ranked,
                    "issues": block_issue_types,
                }
            )

        weak_ratio = round(weak / max(1, checked), 3)
        mismatch_ratio = round(mismatched / max(1, checked), 3)
        ready = mismatch_ratio <= 0.35 and weak_ratio <= 0.55
        return {
            "version": cls.VERSION,
            "ready": ready,
            "checked_blocks": checked,
            "weak_block_count": weak,
            "mismatch_count": mismatched,
            "weak_ratio": weak_ratio,
            "mismatch_ratio": mismatch_ratio,
            "issue_count": len(issues),
            "issues": issues[:120],
            "blocks": block_reports[:200],
        }

    @classmethod
    def annotate_package(
        cls,
        package: Dict[str, Any],
        render_blocks: Iterable[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        package = dict(package or {})
        report = cls.validate(package, render_blocks)
        package["scene_retrieval_report"] = report
        return package, report
