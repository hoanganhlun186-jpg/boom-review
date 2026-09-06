"""Translate source SRT files while preserving original cue timing."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from engine.ai_engine import AIEngine
from engine.srt_processor import SRTParser


class SRTTranslator:
    """Gemini/OpenRouter-backed SRT translator for source dialogue subtitles."""

    DEFAULT_BATCH_ITEMS = 45
    DEFAULT_BATCH_CHARS = 5200

    @staticmethod
    def default_output_path(srt_path: str) -> str:
        base, ext = os.path.splitext(srt_path or "")
        if not base:
            return ""
        lower = base.lower()
        if lower.endswith("_capcut"):
            return f"{base}_translated{ext or '.srt'}"
        if lower.endswith("_translated") or lower.endswith("_vi"):
            return srt_path
        return f"{base}_translated{ext or '.srt'}"

    @staticmethod
    def is_translated_srt_path(path: str) -> bool:
        name = os.path.basename(str(path or "")).lower()
        return (
            name.endswith("_translated.srt")
            or name.endswith("_capcut_translated.srt")
            or name.endswith("_vi.srt")
        )

    @staticmethod
    def _clean_text(text: Any) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return cleaned

    @staticmethod
    def _has_cjk(text: Any) -> bool:
        return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]", str(text or "")))

    @classmethod
    def _needs_translation(cls, subtitles: Iterable[Dict[str, Any]]) -> bool:
        text = " ".join(str(item.get("text", "")) for item in subtitles or [])
        if not text.strip():
            return False
        if cls._has_cjk(text):
            return True
        vietnamese_marks = (
            "ăâđêôơư"
            "áàảãạấầẩẫậắằẳẵặ"
            "éèẻẽẹếềểễệ"
            "íìỉĩị"
            "óòỏõọốồổỗộớờởỡợ"
            "úùủũụứừửữự"
            "ýỳỷỹỵ"
        )
        if any(char in text.lower() for char in vietnamese_marks):
            return False
        # Latin no-accent/foreign subtitles still benefit from a Vietnamese source SRT.
        return True

    @classmethod
    def _valid_cached_output(cls, source_path: str, output_path: str) -> bool:
        if not output_path or not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            return False
        try:
            if source_path and os.path.exists(source_path) and os.path.getmtime(output_path) < os.path.getmtime(source_path):
                return False
            source_count = len(SRTParser.parse_srt(source_path))
            output_subtitles = SRTParser.parse_srt(output_path)
            output_count = len(output_subtitles)
            if output_count and cls._needs_translation(output_subtitles):
                return False
            return output_count > 0 and (not source_count or output_count >= max(1, int(source_count * 0.92)))
        except Exception:
            return False

    @classmethod
    def _batches(
        cls,
        subtitles: List[Dict[str, Any]],
        max_items: int = DEFAULT_BATCH_ITEMS,
        max_chars: int = DEFAULT_BATCH_CHARS,
    ) -> List[List[Dict[str, Any]]]:
        batches: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_chars = 0
        for item in subtitles:
            text = cls._clean_text(item.get("text"))
            cost = len(text) + 20
            if current and (len(current) >= max_items or current_chars + cost > max_chars):
                batches.append(current)
                current = []
                current_chars = 0
            current.append(item)
            current_chars += cost
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _extract_json_object(raw: str) -> Dict[str, Any]:
        data = AIEngine._extract_json_payload(raw)
        if isinstance(data, dict):
            return data
        cleaned = str(raw or "").strip()
        if "```" in cleaned:
            cleaned = re.sub(r"```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).replace("```", "").strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return {"items": json.loads(cleaned[start : end + 1])}
            except Exception:
                pass
        return {}

    @classmethod
    def _translate_batch(cls, ai: AIEngine, batch: List[Dict[str, Any]]) -> Dict[int, str]:
        payload = [
            {
                "index": int(item.get("index") or idx),
                "text": cls._clean_text(item.get("text")),
            }
            for idx, item in enumerate(batch, 1)
        ]
        prompt = (
            "Bạn là biên dịch phụ đề phim chuyên nghiệp.\n"
            "Dịch từng dòng thoại sau sang tiếng Việt CÓ DẤU, tự nhiên, đúng nghĩa phim.\n"
            "GIỮ NGUYÊN index. KHÔNG thêm/bớt dòng. KHÔNG giải thích. KHÔNG markdown.\n"
            "Giữ tên riêng nhất quán; nếu là tiếng Trung/Anh thì dịch nghĩa câu thoại sang tiếng Việt.\n"
            "Nếu dòng gốc là tiếng Việt không dấu, hãy phục hồi dấu theo ngữ cảnh.\n"
            "Trả về DUY NHẤT JSON hợp lệ dạng:\n"
            '{"items":[{"index":1,"text":"..."}]}\n\n'
            f"DANH SÁCH CẦN DỊCH:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        raw = ai._try_generate(prompt)
        data = cls._extract_json_object(raw)
        items = data.get("items") if isinstance(data, dict) else []
        translations: Dict[int, str] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except Exception:
                continue
            text = cls._clean_text(item.get("text"))
            if text:
                translations[index] = text
        return translations

    @staticmethod
    def _format_srt(subtitles: List[Dict[str, Any]], translations: Dict[int, str]) -> str:
        lines: List[str] = []
        for ordinal, item in enumerate(subtitles, 1):
            try:
                index = int(item.get("index") or ordinal)
            except Exception:
                index = ordinal
            text = translations.get(index) or str(item.get("text") or "").strip()
            lines.append(str(index))
            lines.append(f"{item.get('start')} --> {item.get('end')}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    @classmethod
    def translate_file_to_vietnamese(
        cls,
        srt_path: str,
        ai_keys: Any,
        output_path: Optional[str] = None,
        force: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        if not srt_path or not os.path.exists(srt_path):
            raise FileNotFoundError(f"SRT không tồn tại: {srt_path}")
        output_path = output_path or cls.default_output_path(srt_path)
        if not output_path:
            raise ValueError("Không xác định được đường dẫn SRT dịch")
        if not force and cls._valid_cached_output(srt_path, output_path):
            if progress_callback:
                progress_callback(f"Dùng lại SRT đã dịch: {output_path}")
            return output_path

        subtitles = SRTParser.parse_srt(srt_path)
        if not subtitles:
            raise ValueError("SRT nguồn không có subtitle hợp lệ")
        if not force and cls.is_translated_srt_path(srt_path) and not cls._needs_translation(subtitles):
            return srt_path

        ai = AIEngine(ai_keys)
        translations: Dict[int, str] = {}
        batches = cls._batches(subtitles)
        for batch_index, batch in enumerate(batches, 1):
            if progress_callback:
                progress_callback(f"Gemini dịch SRT batch {batch_index}/{len(batches)} ({len(batch)} dòng)")
            batch_translations = cls._translate_batch(ai, batch)
            missing = [
                int(item.get("index") or 0)
                for item in batch
                if int(item.get("index") or 0) not in batch_translations
            ]
            if missing and len(batch) > 1:
                # Retry small chunks so one bad response does not ruin a long subtitle file.
                for item in batch:
                    item_translation = cls._translate_batch(ai, [item])
                    batch_translations.update(item_translation)
            translations.update(batch_translations)

        content = cls._format_srt(subtitles, translations)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        if progress_callback:
            progress_callback(f"Đã lưu SRT tiếng Việt: {output_path}")
        return output_path
