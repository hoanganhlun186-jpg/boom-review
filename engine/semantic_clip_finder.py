"""Semantic clip selection for recap videos."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine.scene_cards import TextSignals


class SemanticClipFinder:
    """Select source-video clips using scene cards instead of fixed keep/skip only."""

    @staticmethod
    def _duration(card: Dict[str, Any]) -> float:
        return max(0.0, float(card.get("end_s", 0.0) or 0.0) - float(card.get("start_s", 0.0) or 0.0))

    @classmethod
    def _card_score(cls, card: Dict[str, Any]) -> float:
        base = float(card.get("importance_score", 0.0) or 0.0)
        plot = float(card.get("plot_importance", 0.0) or 0.0)
        visual = float(card.get("visual_importance", 0.0) or 0.0)
        subtitles = min(1.0, float(card.get("subtitle_count", 0) or 0) / 5.0)
        duration = cls._duration(card)
        duration_bonus = 0.12 if 2.0 <= duration <= 14.0 else 0.0
        return min(1.0, base * 0.45 + plot * 0.25 + visual * 0.18 + subtitles * 0.12 + duration_bonus)

    @classmethod
    def _bucket_cards(cls, cards: List[Dict[str, Any]], bucket_count: int) -> List[List[Dict[str, Any]]]:
        if not cards:
            return []
        start = min(float(card.get("start_s", 0.0) or 0.0) for card in cards)
        end = max(float(card.get("end_s", 0.0) or 0.0) for card in cards)
        total = max(1.0, end - start)
        buckets = [[] for _ in range(max(1, bucket_count))]
        for card in cards:
            pos = (float(card.get("start_s", 0.0) or 0.0) - start) / total
            index = min(len(buckets) - 1, max(0, int(pos * len(buckets))))
            buckets[index].append(card)
        return buckets

    @classmethod
    def _clip_bounds(
        cls,
        card: Dict[str, Any],
        max_clip_duration: float,
        min_clip_duration: float,
        source_duration: Optional[float],
    ) -> Tuple[float, float]:
        start = float(card.get("start_s", 0.0) or 0.0)
        end = float(card.get("end_s", start) or start)
        duration = max(0.0, end - start)
        if duration > max_clip_duration:
            center = (start + end) / 2.0
            start = max(0.0, center - max_clip_duration / 2.0)
            end = start + max_clip_duration
        elif duration < min_clip_duration:
            pad = (min_clip_duration - duration) / 2.0
            start = max(0.0, start - pad)
            end = end + pad
        if source_duration and source_duration > 0:
            end = min(float(source_duration), end)
            start = max(0.0, min(start, end - 0.1))
        return round(start, 3), round(end, 3)

    @classmethod
    def _apply_action_lookahead(
        cls,
        card: Dict[str, Any],
        next_card: Optional[Dict[str, Any]],
        clip_start: float,
        clip_end: float,
        max_extra: float = 6.0,
    ) -> Tuple[float, float, List[Any], str]:
        scene_ids = [card.get("scene_id")]
        reason = "semantic_story"
        if not next_card:
            return clip_start, clip_end, scene_ids, reason
        has_dialogue = int(card.get("subtitle_count", 0) or 0) > 0
        next_has_little_dialogue = int(next_card.get("subtitle_count", 0) or 0) <= 1
        next_visual = float(next_card.get("visual_importance", 0.0) or 0.0)
        gap = float(next_card.get("start_s", 0.0) or 0.0) - float(card.get("end_s", 0.0) or 0.0)
        if has_dialogue and next_has_little_dialogue and gap <= 1.2 and next_visual >= 0.20:
            next_end = float(next_card.get("end_s", clip_end) or clip_end)
            extended = min(next_end, clip_end + max_extra)
            if extended > clip_end + 0.35:
                clip_end = extended
                scene_ids.append(next_card.get("scene_id"))
                reason = "semantic_story+action_lookahead"
        return round(clip_start, 3), round(clip_end, 3), scene_ids, reason

    @classmethod
    def extend_story_clip_with_action_lookahead(
        cls,
        scene_cards: Iterable[Dict[str, Any]],
        clip_start: float,
        clip_end: float,
        scene_ids: Optional[Iterable[Any]] = None,
        max_extra: float = 6.0,
    ) -> Dict[str, Any]:
        """Extend an existing story clip into the immediate action scene.

        AI_FULL already chooses the story range. This method only handles the
        RECAP2 dialogue-to-action case and never replaces that range with a
        globally ranked scene.
        """
        cards = [dict(card) for card in (scene_cards or []) if isinstance(card, dict)]
        cards.sort(key=lambda item: float(item.get("start_s", 0.0) or 0.0))
        if not cards:
            return {
                "start": round(float(clip_start or 0.0), 3),
                "end": round(float(clip_end or 0.0), 3),
                "scene_ids": list(scene_ids or []),
                "reason": "semantic_story",
                "extended": False,
            }

        requested_ids = {str(value) for value in (scene_ids or []) if value is not None}
        primary_index = None
        if requested_ids:
            for idx, card in enumerate(cards):
                if str(card.get("scene_id")) in requested_ids:
                    primary_index = idx

        if primary_index is None:
            end_value = float(clip_end or clip_start or 0.0)
            overlapping = [
                (idx, card)
                for idx, card in enumerate(cards)
                if float(card.get("start_s", 0.0) or 0.0) <= end_value + 0.5
                and float(card.get("end_s", 0.0) or 0.0) >= end_value - 1.5
            ]
            if overlapping:
                primary_index = overlapping[-1][0]

        if primary_index is None:
            return {
                "start": round(float(clip_start or 0.0), 3),
                "end": round(float(clip_end or 0.0), 3),
                "scene_ids": list(scene_ids or []),
                "reason": "semantic_story",
                "extended": False,
            }

        primary = cards[primary_index]
        next_card = cards[primary_index + 1] if primary_index + 1 < len(cards) else None
        start, end, detected_ids, reason = cls._apply_action_lookahead(
            primary,
            next_card,
            float(clip_start or 0.0),
            float(clip_end or 0.0),
            max_extra=max_extra,
        )
        merged_ids: List[Any] = []
        for value in list(scene_ids or []) + list(detected_ids or []):
            if value is not None and value not in merged_ids:
                merged_ids.append(value)
        return {
            "start": start,
            "end": end,
            "scene_ids": merged_ids,
            "reason": reason,
            "extended": end > float(clip_end or 0.0) + 0.35,
        }

    @classmethod
    def _merge_segments(cls, segments: List[Dict[str, Any]], gap_tolerance: float = 0.2) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for seg in sorted(segments, key=lambda item: float(item.get("start", 0.0) or 0.0)):
            if not merged:
                merged.append(dict(seg))
                continue
            prev = merged[-1]
            if float(seg.get("start", 0.0) or 0.0) <= float(prev.get("end", 0.0) or 0.0) + gap_tolerance:
                prev["end"] = max(float(prev.get("end", 0.0) or 0.0), float(seg.get("end", 0.0) or 0.0))
                prev["score"] = max(float(prev.get("score", 0.0) or 0.0), float(seg.get("score", 0.0) or 0.0))
                prev_ids = list(prev.get("scene_ids") or [])
                for sid in seg.get("scene_ids") or []:
                    if sid not in prev_ids:
                        prev_ids.append(sid)
                prev["scene_ids"] = prev_ids
                if seg.get("dialogue_text"):
                    prev["dialogue_text"] = TextSignals.clean(
                        f"{prev.get('dialogue_text', '')} {seg.get('dialogue_text', '')}", 500
                    )
            else:
                merged.append(dict(seg))
        return merged

    @classmethod
    def find_best_clips(
        cls,
        scene_cards: Iterable[Dict[str, Any]],
        target_duration: Optional[float],
        source_duration: Optional[float] = None,
        min_clip_duration: float = 3.0,
        max_clip_duration: float = 16.0,
    ) -> List[Dict[str, Any]]:
        cards = [
            dict(card)
            for card in (scene_cards or [])
            if isinstance(card, dict) and card.get("recap_use", True)
        ]
        if not cards:
            return []
        cards.sort(key=lambda item: float(item.get("start_s", 0.0) or 0.0))

        target = float(target_duration or 0.0)
        if target <= 0:
            full = max(float(card.get("end_s", 0.0) or 0.0) for card in cards)
            target = max(30.0, full * 0.22)
        target = max(6.0, target)

        bucket_count = max(5, min(14, int(target // 18) or 5))
        selected: List[Dict[str, Any]] = []
        selected_ids = set()

        for bucket in cls._bucket_cards(cards, bucket_count):
            if not bucket:
                continue
            best = max(bucket, key=cls._card_score)
            if best.get("scene_id") not in selected_ids:
                selected.append(best)
                selected_ids.add(best.get("scene_id"))

        ranked = sorted(cards, key=cls._card_score, reverse=True)
        current_duration = sum(min(max_clip_duration, max(min_clip_duration, cls._duration(card))) for card in selected)
        for card in ranked:
            if current_duration >= target:
                break
            if card.get("scene_id") in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(card.get("scene_id"))
            current_duration += min(max_clip_duration, max(min_clip_duration, cls._duration(card)))

        card_by_id = {card.get("scene_id"): card for card in cards}
        ordered_cards = sorted(selected, key=lambda item: float(item.get("start_s", 0.0) or 0.0))
        all_cards_by_start = sorted(cards, key=lambda item: float(item.get("start_s", 0.0) or 0.0))
        index_by_id = {card.get("scene_id"): idx for idx, card in enumerate(all_cards_by_start)}

        segments: List[Dict[str, Any]] = []
        total = 0.0
        for card in ordered_cards:
            if total >= target:
                break
            start, end = cls._clip_bounds(card, max_clip_duration, min_clip_duration, source_duration)
            next_card = None
            idx = index_by_id.get(card.get("scene_id"))
            if idx is not None and idx + 1 < len(all_cards_by_start):
                next_card = all_cards_by_start[idx + 1]
            start, end, scene_ids, reason = cls._apply_action_lookahead(card, next_card, start, end)
            duration = max(0.0, end - start)
            if duration <= 0.2:
                continue
            remaining = target - total
            if remaining > 0 and duration > remaining and remaining >= min_clip_duration:
                end = start + remaining
                duration = remaining
            dialogue = TextSignals.clean(card.get("dialogue_text", ""), 420)
            visual_anchor = TextSignals.clean(
                card.get("reason") or " ".join(card.get("keywords", []) or []) or dialogue,
                180,
            )
            segments.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "score": round(cls._card_score(card) * 100.0, 2),
                "reason": reason,
                "scene_ids": [sid for sid in scene_ids if sid is not None],
                "dialogue_text": dialogue,
                "visual_anchor": visual_anchor,
                "subtitle_count": card.get("subtitle_count", 0),
                "importance_score": card.get("importance_score", 0.0),
                "source": "semantic_clip_finder",
            })
            total += duration

        return cls._merge_segments(segments)
