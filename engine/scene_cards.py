"""Scene card helpers for recap planning.

The GUI does not need to know about these structures. They are durable JSON
artifacts used by the backend to keep subtitles, keyframes, and scene scores
attached to the same timeline.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class TextSignals:
    """Small Vietnamese-aware text helpers shared by recap planners."""

    STOPWORDS = {
        "anh", "chi", "cho", "con", "cua", "cung", "dang", "day", "den",
        "deu", "duoc", "hay", "hon", "khi", "khong", "lam", "la", "lai",
        "len", "luc", "mot", "nay", "neu", "nhu", "nhung", "nhung",
        "qua", "ra", "rang", "roi", "sau", "thi", "toi", "trong", "tu",
        "vao", "ve", "voi", "vua", "va",
        "có", "của", "được", "không", "một", "những", "trong", "với",
        "vào", "đến", "rằng", "nhưng", "này", "đó", "thì", "cho",
    }

    ACTION_KEYWORDS = {
        "chet", "giet", "mau", "cuu", "chay", "duoi", "danh", "dau",
        "nga", "roi", "khoc", "so", "bi mat", "phat hien", "xac",
        "dau lau", "ao", "nuoc", "lua", "dao", "kiem", "no",
        "chết", "giết", "máu", "cứu", "chạy", "đuổi", "đánh", "đấu",
        "ngã", "rơi", "khóc", "sợ", "bí mật", "phát hiện", "xác",
        "đầu lâu", "ao", "nước", "lửa", "dao", "kiếm", "nổ",
    }

    AD_KEYWORDS = {
        "1xbet", "789bet", "casino", "betting", "subscribe", "dang ky",
        "like", "share", "theo doi", "quang cao", "tai app", "link",
        "đăng ký", "theo dõi", "quảng cáo",
    }

    @classmethod
    def clean(cls, value: Any, limit: Optional[int] = None) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if limit and len(text) > limit:
            return text[: max(0, limit - 1)].rstrip() + "..."
        return text

    @classmethod
    def keywords(cls, text: Any, limit: int = 24) -> List[str]:
        folded = str(text or "").lower()
        tokens = re.findall(r"[\w\u00C0-\u024F\u1E00-\u1EFF]{3,}", folded)
        result: List[str] = []
        for token in tokens:
            if token in cls.STOPWORDS:
                continue
            if token not in result:
                result.append(token)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def overlap_score(cls, left: Any, right: Any) -> float:
        left_set = set(cls.keywords(left, 80))
        right_set = set(cls.keywords(right, 80))
        if not left_set or not right_set:
            return 0.0
        return len(left_set & right_set) / float(min(len(left_set), len(right_set)))


class SubtitleSceneMapper:
    """Map each subtitle line to one scene using maximum time overlap."""

    @staticmethod
    def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
        return max(0.0, min(a_end, b_end) - max(a_start, b_start))

    @classmethod
    def map_subtitles_to_scenes(
        cls,
        scenes: Iterable[Dict[str, Any]],
        subtitles: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        scene_list = [dict(scene) for scene in (scenes or []) if isinstance(scene, dict)]
        scene_list.sort(key=lambda item: float(item.get("start_s", item.get("start", 0.0)) or 0.0))
        sub_list = [dict(sub) for sub in (subtitles or []) if isinstance(sub, dict)]

        mapped: Dict[Any, Dict[str, Any]] = {}
        for scene in scene_list:
            scene_id = scene.get("scene_id", len(mapped) + 1)
            start = float(scene.get("start_s", scene.get("start", 0.0)) or 0.0)
            end = float(scene.get("end_s", scene.get("end", start)) or start)
            mapped[scene_id] = {
                "scene_id": scene_id,
                "scene_start_s": start,
                "scene_end_s": end,
                "subtitles": [],
                "subtitle_count": 0,
                "dialogue_text": "",
            }

        for sub in sub_list:
            sub_start = float(sub.get("start_seconds", sub.get("start_s", 0.0)) or 0.0)
            sub_end = float(sub.get("end_seconds", sub.get("end_s", sub_start)) or sub_start)
            best_scene_id = None
            best_overlap = 0.0
            for scene in scene_list:
                scene_id = scene.get("scene_id")
                scene_start = float(scene.get("start_s", scene.get("start", 0.0)) or 0.0)
                scene_end = float(scene.get("end_s", scene.get("end", scene_start)) or scene_start)
                overlap = cls._overlap(sub_start, sub_end, scene_start, scene_end)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_scene_id = scene_id
            if best_scene_id is None or best_scene_id not in mapped:
                continue
            mapped[best_scene_id]["subtitles"].append({
                "index": sub.get("index", 0),
                "text": sub.get("text", ""),
                "start_s": sub_start,
                "end_s": sub_end,
            })

        result: List[Dict[str, Any]] = []
        for scene in scene_list:
            scene_id = scene.get("scene_id")
            row = mapped.get(scene_id)
            if not row:
                continue
            row["subtitle_count"] = len(row["subtitles"])
            row["dialogue_text"] = TextSignals.clean(" ".join(
                str(item.get("text", "")) for item in row["subtitles"]
            ))
            result.append(row)
        return result


class SceneCardBuilder:
    """Build compact, durable scene cards for semantic clip selection."""

    @staticmethod
    def _keyframe_for_scene(scene_id: Any, keyframes: Iterable[str]) -> str:
        files = [str(path) for path in (keyframes or []) if path]
        sid = str(scene_id)
        for path in files:
            name = os.path.basename(path)
            if f"_{int(scene_id):04d}" in name if str(scene_id).isdigit() else sid in name:
                return path
        try:
            index = int(scene_id)
            if 0 <= index < len(files):
                return files[index]
            if 1 <= index <= len(files):
                return files[index - 1]
        except Exception:
            pass
        return ""

    @classmethod
    def _importance(cls, scene: Dict[str, Any], mapped: Dict[str, Any]) -> Dict[str, Any]:
        duration = max(0.0, float(scene.get("duration_s", 0.0) or 0.0))
        dialogue = mapped.get("dialogue_text", "")
        subtitle_count = int(mapped.get("subtitle_count", 0) or 0)
        cut_score = float(scene.get("cut_score", scene.get("score", 0.0)) or 0.0)

        density = subtitle_count / max(duration, 1.0)
        keyword_hits = sum(1 for key in TextSignals.ACTION_KEYWORDS if key in dialogue.lower())
        dialogue_score = min(1.0, density * 2.4 + min(subtitle_count, 6) * 0.06)
        action_score = min(1.0, keyword_hits * 0.18)
        visual_score = min(1.0, cut_score / 80.0) if cut_score else 0.25
        duration_score = 1.0
        if duration < 1.0:
            duration_score = 0.25
        elif duration > 30.0:
            duration_score = 0.72

        raw = (dialogue_score * 0.45) + (action_score * 0.25) + (visual_score * 0.20) + (duration_score * 0.10)
        text_lower = dialogue.lower()
        recap_use = duration >= 0.5 and not any(key in text_lower for key in TextSignals.AD_KEYWORDS)
        if not dialogue and cut_score < 1.0 and duration < 2.0:
            recap_use = False

        return {
            "plot_importance": round(min(1.0, dialogue_score + action_score), 3),
            "visual_importance": round(visual_score, 3),
            "importance_score": round(min(1.0, raw), 3),
            "recap_use": recap_use,
        }

    @classmethod
    def build(
        cls,
        scenes: Iterable[Dict[str, Any]],
        subtitle_map: Iterable[Dict[str, Any]],
        keyframes: Iterable[str],
    ) -> List[Dict[str, Any]]:
        map_by_id = {
            item.get("scene_id"): dict(item)
            for item in (subtitle_map or [])
            if isinstance(item, dict)
        }
        cards: List[Dict[str, Any]] = []
        for ordinal, scene in enumerate((scenes or []), 1):
            if not isinstance(scene, dict):
                continue
            scene_id = scene.get("scene_id", ordinal)
            start = float(scene.get("start_s", scene.get("start", 0.0)) or 0.0)
            end = float(scene.get("end_s", scene.get("end", start)) or start)
            mapped = map_by_id.get(scene_id, {})
            dialogue = TextSignals.clean(mapped.get("dialogue_text", ""))
            signals = cls._importance(scene, mapped)
            keywords = TextSignals.keywords(dialogue or scene.get("reason", ""))
            card = {
                "scene_id": scene_id,
                "ordinal": ordinal,
                "start_s": round(start, 3),
                "end_s": round(end, 3),
                "duration_s": round(max(0.0, end - start), 3),
                "subtitle_count": int(mapped.get("subtitle_count", 0) or 0),
                "dialogue_text": dialogue,
                "keywords": keywords,
                "keyframe": cls._keyframe_for_scene(scene_id, keyframes),
                "cut_score": float(scene.get("cut_score", scene.get("score", 0.0)) or 0.0),
                "reason": scene.get("reason", ""),
                **signals,
            }
            cards.append(card)
        return cards

    @classmethod
    def write(cls, cards: List[Dict[str, Any]], output_path: str) -> str:
        Path(os.path.dirname(output_path) or ".").mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(cards, handle, ensure_ascii=False, indent=2)
        return output_path
