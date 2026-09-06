"""Vietnamese text quality guards for TTS-safe narration."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


class VietnameseTextGuard:
    """Detect Vietnamese narration that lost diacritics before TTS."""

    VERSION = "vi_diacritics_v1"
    DIACRITIC_RE = re.compile(r"[\u00c0-\u1ef9]")
    TOKEN_RE = re.compile(r"[A-Za-z\u00c0-\u1ef9]+")
    COMMON_ASCII_VI = {
        "va", "la", "cua", "trong", "mot", "nguoi", "khong", "nhung", "thi",
        "voi", "nay", "do", "khi", "canh", "phim", "nhan", "vat", "tiep",
        "theo", "bat", "ngo", "phat", "hien", "dau", "lau", "duoi", "ao",
        "vuong", "gia", "so", "so", "chuyen", "cau", "noi", "loi", "thoai",
        "bi", "mat", "su", "that", "dang", "xuat", "hien", "luc", "nay",
    }

    @classmethod
    def tokens(cls, text: str) -> List[str]:
        return [item.lower() for item in cls.TOKEN_RE.findall(str(text or ""))]

    @classmethod
    def diacritic_density(cls, text: str) -> float:
        raw = str(text or "")
        letters = cls.TOKEN_RE.findall(raw)
        total_chars = sum(len(item) for item in letters)
        if total_chars <= 0:
            return 0.0
        return len(cls.DIACRITIC_RE.findall(raw)) / total_chars

    @classmethod
    def needs_diacritic_repair(cls, text: str) -> bool:
        raw = str(text or "").strip()
        if len(raw) < 24:
            return False
        tokens = cls.tokens(raw)
        if len(tokens) < 5:
            return False
        if cls.diacritic_density(raw) >= 0.012:
            return False
        common_hits = sum(1 for token in tokens if token in cls.COMMON_ASCII_VI)
        return common_hits >= max(3, min(8, len(tokens) // 8))

    @classmethod
    def report_blocks(cls, script_blocks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        blocks = []
        for index, block in enumerate(script_blocks or [], 1):
            if not isinstance(block, dict):
                continue
            block_id = block.get("block_id") or block.get("book_id") or index
            text = str(block.get("text") or "")
            needs = cls.needs_diacritic_repair(text)
            blocks.append({
                "block_id": block_id,
                "needs_repair": needs,
                "diacritic_density": round(cls.diacritic_density(text), 4),
                "text_preview": text[:120],
            })
        weak = [item for item in blocks if item.get("needs_repair")]
        return {
            "version": cls.VERSION,
            "block_count": len(blocks),
            "weak_block_count": len(weak),
            "needs_repair": bool(weak),
            "blocks": blocks,
        }

    @classmethod
    def package_needs_repair(cls, package: Dict[str, Any]) -> bool:
        if not isinstance(package, dict):
            return False
        if cls.needs_diacritic_repair(package.get("script", "")):
            return True
        return cls.report_blocks(package.get("script_blocks") or []).get("needs_repair", False)
