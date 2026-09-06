"""Timeline word-budget helpers for recap blocks.

The goal is simple: every script block should have enough narration to sound
continuous, but not so much that TTS has to rush against the cut video.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


class TimelineBudgetAligner:
    VERSION = "timeline_budget_v1"
    DEFAULT_WORDS_PER_SECOND = 2.25
    MIN_WORDS_PER_BLOCK = 16
    MAX_WORDS_PER_BLOCK = 78

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def word_count(text: Any) -> int:
        return len(re.findall(r"[^\W_]+", str(text or ""), flags=re.UNICODE))

    @classmethod
    def estimate_tts_duration(cls, text: Any, words_per_second: float | None = None) -> float:
        words_per_second = float(words_per_second or cls.DEFAULT_WORDS_PER_SECOND)
        words = cls.word_count(text)
        punctuation_pause = min(3.0, len(re.findall(r"[,.;:!?]", str(text or ""))) * 0.08)
        return round((words / max(0.5, words_per_second)) + punctuation_pause, 3)

    @classmethod
    def _duration(cls, block: Dict[str, Any]) -> float:
        duration = cls._float(block.get("duration"))
        if duration > 0:
            return duration
        start = cls._float(block.get("start_in_final_video"))
        end = cls._float(block.get("end_in_final_video"))
        if end > start:
            return end - start
        return cls._float(block.get("duration_hint_seconds"), 0.0)

    @classmethod
    def apply_word_budgets(
        cls,
        render_blocks: Iterable[Dict[str, Any]],
        target_duration_seconds: float = 0.0,
        words_per_second: float | None = None,
        min_words: int | None = None,
        max_words: int | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        words_per_second = float(words_per_second or cls.DEFAULT_WORDS_PER_SECOND)
        min_words = int(min_words or cls.MIN_WORDS_PER_BLOCK)
        max_words = int(max_words or cls.MAX_WORDS_PER_BLOCK)
        blocks: List[Dict[str, Any]] = []
        total_duration = 0.0
        total_target = 0
        very_short = 0

        for index, raw in enumerate(render_blocks or [], 1):
            block = dict(raw or {})
            duration = max(0.1, cls._duration(block))
            score = max(
                cls._float(block.get("smart_score")),
                cls._float(block.get("cut_score")),
                cls._float(block.get("importance_score")),
            )
            target = int(round(duration * words_per_second))
            if score >= 85 and duration >= 7.0:
                target = int(round(target * 1.10))
            elif score <= 45 and duration <= 9.0:
                target = int(round(target * 0.90))
            target = max(min_words, min(max_words, target))
            if duration < 6.0:
                very_short += 1
                target = min(target, max(min_words, int(round(duration * (words_per_second + 0.35)))))

            block["target_words"] = target
            block["min_words"] = max(8, int(round(target * 0.68)))
            block["max_words"] = max(block["min_words"] + 4, int(round(target * 1.32)))
            block["duration_hint_seconds"] = round(duration, 3)
            block["timeline_budget_version"] = cls.VERSION
            block["timeline_budget_reason"] = (
                f"{duration:.1f}s at {words_per_second:.2f} wps"
                + ("; important scene" if score >= 85 else "")
            )
            total_duration += duration
            total_target += target
            blocks.append(block)

        target_duration_seconds = float(target_duration_seconds or total_duration or 0.0)
        estimated_seconds = round(total_target / max(0.5, words_per_second), 3)
        report = {
            "version": cls.VERSION,
            "block_count": len(blocks),
            "target_duration_seconds": round(target_duration_seconds, 3),
            "render_block_duration_seconds": round(total_duration, 3),
            "total_target_words": total_target,
            "estimated_tts_seconds": estimated_seconds,
            "estimated_tts_to_video_ratio": round(estimated_seconds / max(0.1, target_duration_seconds), 3),
            "words_per_second": words_per_second,
            "very_short_block_count": very_short,
            "min_words_per_block": min_words,
            "max_words_per_block": max_words,
        }
        return blocks, report

    @classmethod
    def validate_package(
        cls,
        package: Dict[str, Any],
        render_blocks: Iterable[Dict[str, Any]],
        words_per_second: float | None = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        words_per_second = float(words_per_second or cls.DEFAULT_WORDS_PER_SECOND)
        render_by_id: Dict[int, Dict[str, Any]] = {}
        for index, block in enumerate(render_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            for key in (block.get("block_id"), block.get("book_id"), index):
                try:
                    render_by_id[int(key)] = block
                except Exception:
                    pass

        issues: List[Dict[str, Any]] = []
        checked = 0
        too_short = 0
        too_long = 0
        for index, block in enumerate(package.get("script_blocks") or [], 1):
            if not isinstance(block, dict):
                continue
            checked += 1
            try:
                block_id = int(block.get("block_id") or block.get("book_id") or index)
            except Exception:
                block_id = index
            render_block = render_by_id.get(block_id, {})
            duration = max(0.1, cls._duration(render_block) or cls._float(block.get("duration_hint_seconds"), 0.0))
            estimated = cls.estimate_tts_duration(block.get("text") or "", words_per_second)
            ratio = estimated / max(0.1, duration)
            if ratio < 0.55:
                too_short += 1
                issues.append({"level": "WARNING", "type": "script_too_short_for_timeline", "block_id": block_id, "ratio": round(ratio, 3)})
            elif ratio > 1.45:
                too_long += 1
                issues.append({"level": "WARNING", "type": "script_too_long_for_timeline", "block_id": block_id, "ratio": round(ratio, 3)})

        report = {
            "version": cls.VERSION,
            "checked_blocks": checked,
            "too_short_blocks": too_short,
            "too_long_blocks": too_long,
            "issue_count": len(issues),
            "issues": issues[:80],
        }
        package = dict(package or {})
        package["timeline_budget_report"] = report
        return package, report

    @staticmethod
    def build_prompt_context(report: Dict[str, Any], max_chars: int = 2200) -> str:
        if not report:
            return ""
        text = (
            "TIMELINE BUDGET LOCK:\n"
            f"- Write every block close to target_words/min_words/max_words in render_blocks.\n"
            f"- Total target: {report.get('total_target_words', 0)} words for "
            f"{report.get('target_duration_seconds', 0)}s cut video.\n"
            "- If a block is short, add concrete action from its SRT/visual evidence, not generic filler.\n"
            "- If a block is long, compress the explanation but keep the key scene anchor.\n"
            "- Voice must sound continuous across adjacent blocks; do not restart with the same opening phrase.\n"
        )
        return text[:max_chars]
