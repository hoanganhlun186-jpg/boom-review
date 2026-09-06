"""Transcript-first story outline builder."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from engine.scene_cards import TextSignals


class TranscriptFirstStoryPlanner:
    """Create a compact beginning-middle-end outline from scene cards."""

    ACTS = [
        ("hook_setup", 0.00, 0.18, "Open with the earliest real conflict and establish the main situation."),
        ("setup", 0.18, 0.38, "Explain who is involved and what pressure starts to build."),
        ("escalation", 0.38, 0.65, "Track the consequences, discoveries, and rising danger."),
        ("climax", 0.65, 0.84, "Focus on the strongest turn, reveal, or confrontation."),
        ("payoff", 0.84, 1.01, "Close with aftermath and a hook to keep watching."),
    ]

    @staticmethod
    def _timeline_bounds(cards: List[Dict[str, Any]]) -> tuple[float, float]:
        if not cards:
            return 0.0, 0.0
        start = min(float(card.get("start_s", 0.0) or 0.0) for card in cards)
        end = max(float(card.get("end_s", 0.0) or 0.0) for card in cards)
        return start, max(end, start + 1.0)

    @classmethod
    def _cards_for_act(
        cls,
        cards: List[Dict[str, Any]],
        start_ratio: float,
        end_ratio: float,
        timeline_start: float,
        timeline_end: float,
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        duration = max(1.0, timeline_end - timeline_start)
        act_start = timeline_start + duration * start_ratio
        act_end = timeline_start + duration * end_ratio
        candidates = [
            card for card in cards
            if float(card.get("end_s", 0.0) or 0.0) >= act_start
            and float(card.get("start_s", 0.0) or 0.0) <= act_end
        ]
        candidates.sort(
            key=lambda card: (
                float(card.get("importance_score", 0.0) or 0.0),
                float(card.get("plot_importance", 0.0) or 0.0),
                int(card.get("subtitle_count", 0) or 0),
            ),
            reverse=True,
        )
        chosen = sorted(candidates[:limit], key=lambda card: float(card.get("start_s", 0.0) or 0.0))
        return chosen

    @classmethod
    def build(
        cls,
        scene_cards: Iterable[Dict[str, Any]],
        target_duration_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        cards = [
            dict(card)
            for card in (scene_cards or [])
            if isinstance(card, dict) and card.get("recap_use", True)
        ]
        cards.sort(key=lambda item: float(item.get("start_s", 0.0) or 0.0))
        timeline_start, timeline_end = cls._timeline_bounds(cards)

        acts: List[Dict[str, Any]] = []
        used_scene_ids = set()
        for name, start_ratio, end_ratio, goal in cls.ACTS:
            chosen = cls._cards_for_act(cards, start_ratio, end_ratio, timeline_start, timeline_end)
            scene_refs = []
            for card in chosen:
                sid = card.get("scene_id")
                used_scene_ids.add(sid)
                scene_refs.append({
                    "scene_id": sid,
                    "time": f"{float(card.get('start_s', 0.0) or 0.0):.1f}-{float(card.get('end_s', 0.0) or 0.0):.1f}s",
                    "keywords": card.get("keywords", [])[:8],
                    "dialogue": TextSignals.clean(card.get("dialogue_text", ""), 180),
                    "score": card.get("importance_score", 0.0),
                })
            acts.append({
                "act": name,
                "goal": goal,
                "scene_refs": scene_refs,
            })

        full_keywords: List[str] = []
        for card in cards:
            for keyword in card.get("keywords", []) or []:
                if keyword not in full_keywords:
                    full_keywords.append(keyword)
                if len(full_keywords) >= 40:
                    break
            if len(full_keywords) >= 40:
                break

        return {
            "mode": "transcript_first_story_outline",
            "target_duration_seconds": float(target_duration_seconds or 0.0),
            "timeline_start": round(timeline_start, 3),
            "timeline_end": round(timeline_end, 3),
            "scene_count": len(cards),
            "acts": acts,
            "global_keywords": full_keywords,
        }

    @classmethod
    def to_prompt_context(cls, outline: Dict[str, Any], limit: int = 6000) -> str:
        if not outline:
            return ""
        lines = [
            "TRANSCRIPT-FIRST STORY OUTLINE:",
            "Use this to cover the whole movie in order. Do not only recap the first scenes.",
            f"Target duration: {outline.get('target_duration_seconds', 0):.1f}s",
            f"Timeline: {outline.get('timeline_start', 0):.1f}s -> {outline.get('timeline_end', 0):.1f}s",
        ]
        for act in outline.get("acts", []) or []:
            lines.append(f"\nACT {act.get('act')}: {act.get('goal')}")
            for ref in act.get("scene_refs", []) or []:
                dialogue = ref.get("dialogue") or "[no dialogue]"
                keywords = ", ".join(ref.get("keywords", []) or [])
                lines.append(
                    f"- scene {ref.get('scene_id')} @ {ref.get('time')}: {dialogue} | keys: {keywords}"
                )
        if outline.get("global_keywords"):
            lines.append("\nGlobal keywords: " + ", ".join(outline.get("global_keywords", [])[:40]))
        text = "\n".join(lines).strip()
        return text[:limit]

    @classmethod
    def write(cls, outline: Dict[str, Any], output_path: str) -> str:
        Path(os.path.dirname(output_path) or ".").mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(outline, handle, ensure_ascii=False, indent=2)
        return output_path
