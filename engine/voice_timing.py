"""Voice duration repair helpers for block-based TTS."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
from typing import Any, Dict, Optional

from utils.helpers import FFmpegUtils

try:
    from engine.premium_pipeline import PremiumReviewPipeline
except Exception:  # pragma: no cover - keeps timing repair usable in isolation
    PremiumReviewPipeline = None


class VoiceTimingController:
    """Assess and repair block narration against real TTS duration."""

    MIN_OK_RATIO = 0.82
    MAX_OK_RATIO = 1.18
    MIN_ATEMPO_RATIO = float(os.environ.get("AUTORECAP_MIN_VOICE_SPEED", "1.0") or "1.0")
    MAX_ATEMPO_RATIO = float(os.environ.get("AUTORECAP_MAX_VOICE_SPEED", "1.5") or "1.5")
    WORDS_PER_SECOND = float(os.environ.get("AUTORECAP_WORDS_PER_SEC_NATURAL", "2.5") or "2.5")

    @staticmethod
    def text_hash(text: str) -> str:
        return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def word_count(text: str) -> int:
        return len(str(text or "").split())

    @staticmethod
    def _fold_text(text: Any) -> str:
        normalized = unicodedata.normalize("NFKD", str(text or ""))
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", ascii_text.lower()).strip()

    @classmethod
    def _restore_known_vietnamese(cls, text: Any) -> str:
        if PremiumReviewPipeline is not None:
            return PremiumReviewPipeline.restore_known_vietnamese_phrases(text)
        return str(text or "")

    @classmethod
    def _clean_anchor(cls, text: Any, limit: int = 140) -> str:
        cleaned = re.sub(r"\s+", " ", cls._restore_known_vietnamese(text)).strip()
        if not cleaned:
            return ""
        if PremiumReviewPipeline is not None and not PremiumReviewPipeline._is_usable_anchor(cleaned):
            return ""
        if len(cleaned) > limit:
            cleaned = cleaned[: max(0, limit - 3)].rstrip() + "..."
        return cleaned.rstrip(" .。")

    @classmethod
    def _content_tokens(cls, text: Any) -> list[str]:
        folded = cls._fold_text(text)
        stopwords = {
            "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do", "day",
            "khi", "neu", "den", "tren", "duoi", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
            "ve", "dang", "rat", "hon", "se", "duoc",
        }
        return [token for token in re.findall(r"[a-z0-9]+", folded) if len(token) > 2 and token not in stopwords]

    @classmethod
    def _similar_sentence_present(cls, text: Any, sentence: Any) -> bool:
        folded_text = cls._fold_text(text)
        folded_sentence = cls._fold_text(sentence)
        if not folded_text or not folded_sentence:
            return False
        if folded_sentence in folded_text:
            return True
        tokens = set(cls._content_tokens(sentence))
        if len(tokens) < 3:
            return False
        return len(tokens & set(cls._content_tokens(text))) / float(len(tokens)) >= 0.82

    @classmethod
    def _anchor_sentence(cls, anchor: Any) -> str:
        cleaned = cls._clean_anchor(anchor)
        if not cleaned:
            return ""
        folded = cls._fold_text(cleaned)
        has_skull_pond = "dau" in folded and "lau" in folded and "ao" in folded
        has_pickup = any(token in folded for token in ("cam len", "nhat len", "vot len"))
        has_so_so = "so so" in folded or "so-so" in folded
        if has_skull_pond and (has_pickup or has_so_so):
            return "Dưới ao, Sở Sở phát hiện chiếc đầu lâu rồi cầm nó lên, biến chi tiết này thành điểm rẽ quan trọng của vụ án."
        if has_skull_pond:
            return "Dưới ao, chiếc đầu lâu bất ngờ lộ ra, kéo nhịp điều tra sang một hướng đáng ngờ hơn."
        return f"Chi tiết {cleaned} làm rõ bước ngoặt mà cảnh này đang đặt ra."

    @classmethod
    def target_words(cls, target_duration: float) -> int:
        if target_duration <= 0:
            return 0
        return max(8, int(target_duration * cls.WORDS_PER_SECOND))

    @staticmethod
    def probe_duration(path: str, ffmpeg_bin: str = "ffmpeg") -> float:
        if not path or not os.path.exists(path):
            return 0.0
        result = subprocess.run(
            [ffmpeg_bin, "-i", path, "-f", "null", "-"],
            **FFmpegUtils.subprocess_kwargs(
                capture_output=True,
                text=True,
                check=False,
            ),
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr or "")
        if not match:
            return 0.0
        hours, minutes, seconds, fraction = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / (10 ** len(fraction))

    @classmethod
    def assess(cls, actual_duration: float, target_duration: float) -> Dict[str, Any]:
        if actual_duration <= 0 or target_duration <= 0:
            return {"status": "unknown", "ratio": 0.0, "needs_repair": False}
        ratio = actual_duration / target_duration
        if ratio < cls.MIN_OK_RATIO:
            status = "too_short"
        elif ratio > cls.MAX_OK_RATIO:
            status = "too_long"
        else:
            status = "ok"
        return {
            "status": status,
            "ratio": round(ratio, 3),
            "needs_repair": status != "ok",
        }

    @staticmethod
    def _sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _finish_sentence(text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if text and text[-1] not in ".!?":
            text += "."
        return text

    @classmethod
    def _padding_sentences(cls, block: Dict[str, Any], needed_words: int) -> list[str]:
        anchor = cls._clean_anchor(
            block.get("srt_anchor")
            or block.get("scene_anchor")
            or block.get("visual_anchor")
            or block.get("visual_hint")
            or ""
        )
        goal = cls._clean_anchor(
            block.get("narrative_goal") or block.get("book_title") or block.get("scene_role_label") or "",
            limit=120,
        )
        bridge = cls._clean_anchor(block.get("bridge_line") or block.get("bridge_hint") or "", limit=140)
        character = cls._clean_anchor(
            block.get("character_focus") or block.get("focus_characters") or block.get("main_characters") or "",
            limit=100,
        )
        if character:
            folded_character = unicodedata.normalize("NFKD", character).encode("ascii", "ignore").decode("ascii").lower()
            bad_focus_words = {"mat", "toc", "dep", "danh", "da", "voi", "canh", "chi", "tiet", "block", "book"}
            tokens = re.findall(r"[a-z]+", folded_character)
            if character.count(",") >= 2 or sum(1 for token in tokens if token in bad_focus_words) >= 2:
                character = ""
        visual = cls._clean_anchor(block.get("visual_anchor") or block.get("visual_hint") or "", limit=130)

        sentences = []
        if anchor:
            sentences.append(cls._anchor_sentence(anchor))
        if visual and visual != anchor:
            sentences.append(
                f"Hình ảnh {visual.lower()} giúp cảnh này có điểm tựa rõ hơn, để lời kể không trôi qua như một đoạn chuyển cảnh bình thường."
            )
        if goal:
            sentences.append(
                f"Nhịp kể ở đây tập trung làm rõ {goal.lower()}, để diễn biến kế tiếp không bị đứt khỏi mạch chính."
            )
        if bridge:
            sentences.append(f"Từ đây, {bridge} trở thành nhịp nối để cảnh sau giữ được cảm xúc và hướng điều tra.")
        sentences.extend([
            "Nhờ vậy, nhịp review có thêm khoảng thở để người xem kịp hiểu vì sao chi tiết này quan trọng với diễn biến tiếp theo.",
            "Cảnh này cũng làm rõ sức ép đang tăng dần, khiến lựa chọn của nhân vật phía sau trở nên đáng chú ý hơn.",
            "Thay vì lướt nhanh qua sự kiện, đoạn này cần được giữ lại như một mắt xích nối cảm xúc, manh mối và hướng rẽ kế tiếp.",
        ])
        return sentences

    @classmethod
    def expand_short_text(
        cls,
        text: str,
        target_duration: float,
        actual_duration: float,
        block: Dict[str, Any],
    ) -> str:
        current = cls._finish_sentence(text)
        deficit = max(0.0, target_duration * cls.MIN_OK_RATIO - actual_duration)
        needed_words = max(8, int(deficit * cls.WORDS_PER_SECOND) + 4)
        target_total = max(cls.target_words(target_duration), cls.word_count(current) + needed_words)
        max_words = max(target_total + 6, int(cls.target_words(target_duration) * 1.35))

        for sentence in cls._padding_sentences(block, needed_words):
            if cls._similar_sentence_present(current, sentence):
                continue
            candidate = cls._finish_sentence(current + " " + sentence)
            if cls.word_count(candidate) > max_words and cls.word_count(current) >= target_total:
                break
            current = candidate
            if cls.word_count(current) >= target_total:
                break
        if cls.word_count(current) < target_total:
            extras = [
                "Điểm đáng chú ý là nhịp phim ở đây không chỉ cung cấp thông tin, mà còn giữ cho mạch căng thẳng tiếp tục chạy về phía trước.",
                "Người xem vì thế có đủ thời gian cảm nhận sự thay đổi trong thái độ nhân vật, trước khi câu chuyện chuyển sang nút thắt tiếp theo.",
                "Chính khoảng dừng này giúp phần thuyết minh nghe liền mạch hơn, đồng thời vẫn bám vào chi tiết đang xuất hiện trong cảnh.",
                "Từ cảm xúc đến hành động, mọi thứ đều đang được xếp lại để chuẩn bị cho một bước ngoặt rõ hơn ở đoạn sau.",
            ]
            for sentence in extras:
                if cls._similar_sentence_present(current, sentence):
                    continue
                candidate = cls._finish_sentence(current + " " + sentence)
                if cls.word_count(candidate) > max_words and cls.word_count(current) >= max(8, int(target_total * 0.90)):
                    break
                current = candidate
                if cls.word_count(current) >= target_total:
                    break
        return current

    @classmethod
    def shorten_long_text(
        cls,
        text: str,
        target_duration: float,
        actual_duration: float,
        block: Optional[Dict[str, Any]] = None,
    ) -> str:
        sentences = cls._sentences(text)
        if len(sentences) <= 1:
            return cls._finish_sentence(text)
        max_words = max(12, int(cls.target_words(target_duration) * 1.12))
        kept = []
        total = 0
        for sentence in sentences:
            words = cls.word_count(sentence)
            if kept and total + words > max_words:
                break
            kept.append(sentence)
            total += words
        if len(kept) < 2 and len(sentences) >= 2:
            kept = sentences[:2]
        return cls._finish_sentence(" ".join(kept))

    @classmethod
    def repair_text(
        cls,
        text: str,
        target_duration: float,
        actual_duration: float,
        block: Dict[str, Any],
    ) -> Dict[str, Any]:
        assessment = cls.assess(actual_duration, target_duration)
        status = assessment["status"]
        repaired = cls._finish_sentence(text)
        action = "none"
        if status == "too_short":
            before = repaired
            repaired = cls.expand_short_text(repaired, target_duration, actual_duration, block)
            if repaired.strip() != before.strip():
                action = "expanded"
        elif status == "too_long" and actual_duration / max(target_duration, 0.001) > cls.MAX_ATEMPO_RATIO:
            repaired = cls.shorten_long_text(repaired, target_duration, actual_duration, block)
            action = "shortened"
        return {
            "text": repaired,
            "action": action,
            "before_status": status,
            "before_ratio": assessment.get("ratio", 0.0),
            "changed": repaired.strip() != cls._finish_sentence(text).strip(),
        }
