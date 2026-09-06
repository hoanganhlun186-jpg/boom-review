
"""
AutoRecapPro V2 - Script Editor Window (stable rescue build)

This module is intentionally conservative: it keeps the pipeline contract stable
while avoiding fragile background UI updates. The editor lets the user review,
edit, auto-time, AI-repair selected/problem blocks, save, and confirm so the
pipeline continues with script_editor_synced metadata.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path

try:
    from core.recap_prompt_pack import recap_prompt_pack_text, recap_block_prompt_rules, narrative_role_for_position, narrative_role_rules_text
except Exception:
    def recap_prompt_pack_text(section=None):
        return ""
    def recap_block_prompt_rules():
        return ""

try:
    import customtkinter as ctk
except Exception:  # pragma: no cover
    ctk = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


_MAX_TTS_SPEED = 1.5
_MIN_TTS_SPEED = 1.0
_WORDS_PER_SEC = 2.75


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    junk_patterns = [
        "\\b(M\u1eaft|Mat|\u0111\u00e1nh|danh|T\u00f3c|Toc|\u0111\u1eb9p|dep)(?:\\s*,\\s*\\1){1,}\\b",
        "\\b([A-Za-z\u00c0-\u1ef9]{2,})(?:\\s+\\1){2,}\\b",
    ]
    for pattern in junk_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .|-")


def _normalized_script_text(text: str) -> str:
    return " ".join(
        re.findall(
            r"[a-z0-9\u00C0-\u024F\u1E00-\u1EFF]+",
            _clean_text(text).lower(),
        )
    )


def _script_sentences(text: str) -> set[str]:
    return {
        normalized
        for part in re.split(r"[.!?;:\n]+", _clean_text(text))
        if len((normalized := _normalized_script_text(part)).split()) >= 8
    }


def _duplicate_match(text: str, previous: list[tuple[int, str, set, set]]):
    """Return the first real duplicate using the same gates as AI_FULL."""
    normalized = _normalized_script_text(text)
    tokens = set(normalized.split())
    sentences = _script_sentences(text)
    if not normalized:
        return None
    for previous_id, previous_text, previous_tokens, previous_sentences in previous:
        if normalized == previous_text:
            return previous_id, "exact"
        union = tokens | previous_tokens
        similarity = len(tokens & previous_tokens) / max(1, len(union))
        if min(len(tokens), len(previous_tokens)) >= 12 and similarity >= 0.86:
            return previous_id, "near_duplicate"
        if sentences & previous_sentences:
            return previous_id, "repeated_sentence"
    return None


def _estimate_tts_duration(text: str) -> float:
    text = _clean_text(text)
    if not text:
        return 0.0
    words = len(text.split())
    chars = len(text)
    return max(1.2, max(words / _WORDS_PER_SEC, chars / 34.0))


def _rate_for_duration(text: str, duration: float) -> tuple[str, float, str]:
    est = _estimate_tts_duration(text)
    if duration <= 0 or est <= 0:
        return "+0%", est, "kh\u00f4ng r\u00f5 th\u1eddi l\u01b0\u1ee3ng"
    if est <= duration:
        return "+0%", est, f"voice {est:.1f}s / c\u1ea3nh {duration:.1f}s"
    need = est / max(0.1, duration)
    used = min(_MAX_TTS_SPEED, max(_MIN_TTS_SPEED, need))
    pct = int(round((used - 1.0) * 100))
    final = est / used
    return f"+{pct}%", final, f"voice {final:.1f}s / c\u1ea3nh {duration:.1f}s"


def _word_bounds_for_duration(duration: float) -> tuple[int, int, int]:
    """Khoang tu an toan de block khong con do theo rule editor."""
    if duration <= 0:
        return 12, 28, 70
    min_words = max(12, int(round(duration * 0.58 * _WORDS_PER_SEC)))
    preferred_words = max(min_words + 2, int(round(duration * 0.86 * _WORDS_PER_SEC)))
    max_words = max(preferred_words + 6, int(round(duration * _MAX_TTS_SPEED * _WORDS_PER_SEC * 0.90)))
    return min_words, preferred_words, max_words


def _fit_text_to_duration(text: str, evidence: str, duration: float) -> str:
    """Safety net sau AI repair: noi/rut gon de het canh bao do co ban."""
    text = _clean_text(text)
    evidence = _clean_text(evidence)
    min_words, _preferred, max_words = _word_bounds_for_duration(duration)
    words = text.split()
    if max_words > 0 and len(words) > max_words:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept = []
        count = 0
        for sentence in sentences:
            sw = sentence.split()
            if kept and count + len(sw) > max_words:
                break
            kept.append(sentence)
            count += len(sw)
        text = _clean_text(" ".join(kept) or " ".join(words[:max_words]))
        words = text.split()
    if len(words) >= min_words:
        return text

    anchors = []
    if evidence:
        for part in re.split(r"[|.;!?]+", evidence):
            part = _clean_text(part)
            if part and len(part.split()) >= 3 and part not in anchors:
                anchors.append(part)
            if len(anchors) >= 3:
                break
    fillers = [
        "Chi tiết này khiến áp lực trong cảnh tăng lên, buộc nhân vật phải đối diện với lựa chọn khó hơn.",
        "Từ đây, mạch truyện chuyển sang một nút thắt mới, kéo người xem theo sát biến cố tiếp theo.",
        "Điều đáng chú ý là phản ứng của nhân vật không chỉ giải thích hiện tại, mà còn mở ra hậu quả cho cảnh sau.",
    ]
    additions = anchors + fillers
    idx = 0
    while len(text.split()) < min_words and additions and idx < len(additions) * 3:
        addition = additions[idx % len(additions)]
        if addition and addition not in text:
            text = _clean_text(text + " " + addition)
        idx += 1
    words = text.split()
    if max_words > 0 and len(words) > max_words:
        text = _clean_text(" ".join(words[:max_words]))
    return text


def _block_id(block: dict, fallback: int) -> int:
    try:
        return int(block.get("block_id") or block.get("book_id") or fallback)
    except Exception:
        return fallback


def _review_clip_for_block(block: dict) -> dict:
    clip = block.get("review_clip")
    return clip if isinstance(clip, dict) else {}


def _ensure_review_clip(block: dict, output_dir: Path | None = None) -> dict:
    clip = dict(_review_clip_for_block(block))
    def _float_value(*values, default=0.0):
        for value in values:
            if value is not None and value != "":
                try:
                    return float(value)
                except Exception:
                    pass
        return float(default)

    start = _float_value(
        clip.get("start"),
        block.get("original_start"),
        block.get("source_start"),
        block.get("start_s"),
        block.get("start_in_final_video"),
    )
    end = _float_value(
        clip.get("end"),
        block.get("original_end"),
        block.get("source_end"),
        block.get("end_s"),
        block.get("end_in_final_video"),
        default=start + _float_value(block.get("duration_hint_seconds"), block.get("duration"), default=0.0),
    )
    if end <= start:
        end = start + _float_value(block.get("duration_hint_seconds"), block.get("duration"), default=0.0)
    scene_ids = clip.get("scene_ids") or block.get("source_block_ids") or block.get("scene_ids") or []
    if isinstance(scene_ids, str):
        scene_ids = [int(x) for x in re.findall(r"\d+", scene_ids)]
    elif not isinstance(scene_ids, list):
        scene_ids = [scene_ids] if scene_ids else []
    clean_scene_ids = []
    for sid in scene_ids:
        try:
            clean_scene_ids.append(int(sid))
        except Exception:
            pass
    bid = _block_id(block, 0)
    thumbnail = clip.get("thumbnail") or ""
    if not thumbnail and output_dir and bid:
        thumbnail = str(output_dir / "cut_keyframes" / f"block_{bid:04d}.jpg")
    clip.update({
        "source_video": clip.get("source_video") or block.get("source_video") or "",
        "start": round(max(0.0, start), 3),
        "end": round(max(0.0, end), 3),
        "duration": round(max(0.0, end - start), 3),
        "scene_ids": clean_scene_ids,
        "source_block_ids": clean_scene_ids,
        "thumbnail": thumbnail,
        "mode": clip.get("mode") or "source_video_timestamp",
    })
    block["review_clip"] = clip
    block["review_clip_source"] = block.get("review_clip_source") or "script_editor"
    block["original_start"] = clip["start"]
    block["original_end"] = clip["end"]
    block["source_start"] = clip["start"]
    block["source_end"] = clip["end"]
    if clean_scene_ids:
        block["scene_ids"] = clean_scene_ids
        block["source_block_ids"] = clean_scene_ids
    return clip


class ScriptEditorWindow(ctk.CTkToplevel if ctk else object):
    """Stable Script Editor used by FullPipeline after AI_FULL."""

    def __init__(self, parent, pipeline, on_confirm=None, on_cancel=None):
        if ctk is None:
            raise RuntimeError("customtkinter is required for ScriptEditorWindow")
        from ui.tk_qt_bridge import editor_master
        master, self._qt_bridge = editor_master(parent)
        super().__init__(master)
        self.parent = parent
        self.pipeline = pipeline
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self._confirmed = False
        self._ai_fix_running = False
        self._block_entries = []
        self._preview_images = []
        self._issue_ids = set()
        self._search_var = ctk.StringVar(master=self, value="")
        self._voice_preview_running = False
        self._preview_cancel_event = None
        self._active_preview_process = None
        self._bottom_preview_image = None
        self._selected_preview = None
        self._embedded_video_capture = None
        self._embedded_video_after_id = None
        self._embedded_video_token = 0
        self._embedded_video_end = 0.0

        self.title("Script Editor - Ch\u1ec9nh s\u1eeda xong -> T\u1ea1o Voice")
        self.resizable(True, True)
        self.minsize(980, 640)
        self._set_initial_window_size()
        self.configure(fg_color="#070b12")
        self.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        try:
            self.parent._active_script_editor = self
        except Exception:
            pass

        self.ai_pkg = getattr(pipeline, "ai_package", {}) or {}
        self.render_blocks = list(getattr(pipeline, "render_blocks", None) or self.ai_pkg.get("render_blocks") or [])
        self.script_blocks = self._load_script_blocks()

        self._build_ui()
        self._render_blocks()
        self._safe_status(f"Script Editor: {len(self.script_blocks)} blocks s\u1eb5n s\u00e0ng \u0111\u1ec3 ch\u1ec9nh s\u1eeda", "#22c55e")
        try:
            # Keep native Windows controls so the editor can be maximized,
            # restored, minimized and resized independently. Do not grab the
            # parent because users may need other tools while editing.
            self.focus_force()
        except Exception:
            pass

    def _set_initial_window_size(self):
        """Open large by default while retaining native resize controls."""
        try:
            screen_w = max(1024, int(self.winfo_screenwidth()))
            screen_h = max(720, int(self.winfo_screenheight()))
            width = max(980, min(1680, screen_w - 80))
            height = max(640, min(980, screen_h - 80))
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry("1280x820")

        # Windows accepts the zoomed state after Tk completes one UI cycle.
        def maximize():
            if not self._ui_alive():
                return
            try:
                self.state("zoomed")
            except Exception:
                pass

        try:
            self.after_idle(maximize)
        except Exception:
            pass

    # ---------------- UI safety ----------------
    def _ui_alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _safe_after(self, delay_ms: int, callback):
        def _run():
            if not self._ui_alive():
                return
            try:
                callback()
            except Exception:
                pass
        try:
            if self._qt_bridge is not None:
                return self._qt_bridge.schedule(delay_ms, _run)
            return self.after(delay_ms, _run)
        except Exception:
            return None

    def _safe_status(self, text: str, color: str = "#94a3b8"):
        if self._qt_bridge is not None and threading.current_thread() is not threading.main_thread():
            self._safe_after(0, lambda: self._safe_status(text, color))
            return
        if not self._ui_alive():
            return
        try:
            value = str(text or "")
            if len(value) > 170:
                value = value[:167] + "..."
            self._status_lbl.configure(text=value, text_color=color)
        except Exception:
            pass

    def _safe_confirm_button(self, state: str = "normal", text: str = "XÁC NHẬN - TẠO VOICE"):
        if self._qt_bridge is not None and threading.current_thread() is not threading.main_thread():
            self._safe_after(0, lambda: self._safe_confirm_button(state, text))
            return
        if not self._ui_alive():
            return
        try:
            self._confirm_btn.configure(state=state, text=text)
        except Exception:
            pass

    # ---------------- Data ----------------
    def _load_script_blocks(self) -> list[dict]:
        blocks = [dict(b) for b in (self.ai_pkg.get("script_blocks") or []) if isinstance(b, dict)]
        if not blocks:
            blocks = []
            for idx, rb in enumerate(self.render_blocks, 1):
                if not isinstance(rb, dict):
                    continue
                text = rb.get("text") or rb.get("script_text") or rb.get("editor_text") or rb.get("voice_text") or rb.get("srt_anchor") or rb.get("dialogue_text") or ""
                item = dict(rb)
                item["block_id"] = _block_id(item, idx)
                item["text"] = _clean_text(text)
                blocks.append(item)
        for idx, block in enumerate(blocks, 1):
            block.setdefault("block_id", idx)
            block["text"] = _clean_text(block.get("text") or block.get("script_text") or block.get("editor_text") or "")
        return blocks

    def _render_by_id(self) -> dict[int, dict]:
        return {_block_id(rb, idx): rb for idx, rb in enumerate(self.render_blocks, 1) if isinstance(rb, dict)}

    def _duration_for(self, block: dict, idx: int) -> float:
        rb = self._render_by_id().get(_block_id(block, idx), {})
        clip = _review_clip_for_block(block)
        for src in (clip, block, rb):
            try:
                dur = float(src.get("duration") or src.get("duration_hint_seconds") or 0.0)
                if dur > 0:
                    return dur
            except Exception:
                pass
            try:
                start = float(src.get("start_in_final_video") or src.get("original_start") or src.get("start_s") or 0.0)
                end = float(src.get("end_in_final_video") or src.get("original_end") or src.get("end_s") or 0.0)
                if end > start:
                    return end - start
            except Exception:
                pass
        return 0.0

    def _time_label_for(self, block: dict, idx: int) -> str:
        rb = self._render_by_id().get(_block_id(block, idx), {})
        clip = _review_clip_for_block(block)
        strategy = str(self.ai_pkg.get("script_block_strategy") or "").strip().lower()
        block_has_source = (
            bool(clip)
            or
            block.get("original_start") is not None
            or block.get("source_start") is not None
            or block.get("start_s") is not None
        )
        src = clip or (block if strategy == "chapter_budget_story_segment" or block_has_source else (rb or block))
        try:
            start = float(src.get("start") or src.get("start_in_final_video") or src.get("start_s") or src.get("original_start") or 0.0)
            dur = self._duration_for(block, idx)
            label = f"clip {start:.1f}->{start + dur:.1f}s" if clip and dur > 0 else (f"cut {start:.1f}->{start + dur:.1f}s" if dur > 0 else f"Block {_block_id(block, idx)}")
            try:
                orig_start = float(src.get("start") if clip and src.get("start") is not None else src.get("original_start") if src.get("original_start") is not None else src.get("source_start") or 0.0)
                orig_end = float(src.get("end") if clip and src.get("end") is not None else src.get("original_end") if src.get("original_end") is not None else src.get("source_end") or 0.0)
                if orig_end > orig_start:
                    if abs(orig_start - start) > 0.2:
                        label += f"\ngốc {orig_start:.1f}->{orig_end:.1f}s"
                    elif strategy == "chapter_budget_story_segment":
                        label = f"gốc {orig_start:.1f}->{orig_end:.1f}s"
            except Exception:
                pass
            return label
        except Exception:
            return f"Block {_block_id(block, idx)}"

    def _evidence_for(self, block: dict, idx: int) -> str:
        rb = self._render_by_id().get(_block_id(block, idx), {})
        parts = []
        for key in ("srt_reference", "srt_anchor", "dialogue_text", "visual_hint", "visual_anchor", "required_anchor", "cut_visible_srt"):
            value = block.get(key) or rb.get(key)
            if value:
                parts.append(str(value))
        return _clean_text(" | ".join(parts))[:900]

    def _output_dir(self) -> Path:
        return Path(
            getattr(self.pipeline, "output_dir", "")
            or self.ai_pkg.get("output_dir")
            or "exports"
        )

    def _uses_recap2_budget_flow(self) -> bool:
        strategy = str(self.ai_pkg.get("script_block_strategy") or "").strip().lower()
        return bool(self.ai_pkg.get("recap2_beat_mode")) or strategy == "chapter_budget_story_segment"

    def _resolve_preview_path(self, value) -> str:
        if not value:
            return ""
        if isinstance(value, (list, tuple)):
            for item in value:
                found = self._resolve_preview_path(item)
                if found:
                    return found
            return ""
        if isinstance(value, dict):
            for key in ("path", "image_path", "keyframe", "thumbnail", "scene_alias", "legacy_path"):
                found = self._resolve_preview_path(value.get(key))
                if found:
                    return found
            return ""
        raw = str(value).strip().strip('"')
        if not raw:
            return ""
        candidates = [Path(raw)]
        base = self._output_dir()
        candidates.extend([
            base / raw,
            base / "keyframes" / raw,
            base / "cut_keyframes" / raw,
            base / "cut_frames" / raw,
        ])
        for path in candidates:
            try:
                if path.exists() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    return str(path)
            except Exception:
                pass
        return ""

    def _preview_image_path_for(self, block: dict, idx: int) -> str:
        bid = _block_id(block, idx)
        rb = self._render_by_id().get(bid, {})
        clip = _review_clip_for_block(block)
        for source in (clip, block, rb):
            for key in (
                "thumbnail", "thumbnail_path", "keyframe", "keyframe_path",
                "image_path", "frame_path", "keyframe_refs", "keyframes",
            ):
                found = self._resolve_preview_path(source.get(key))
                if found:
                    return found
        base = self._output_dir()
        for folder in ("cut_keyframes", "cut_frames", "keyframes"):
            for name in (f"block_{bid:04d}.jpg", f"scene_{bid:04d}.jpg", f"kf_{bid:04d}.jpg"):
                path = base / folder / name
                if path.exists():
                    return str(path)
        return ""

    def _review_anchor_for(self, block: dict, idx: int) -> str:
        rb = self._render_by_id().get(_block_id(block, idx), {})
        for key in ("scene_role_label", "scene_role", "visual_anchor", "visual_hint", "srt_anchor", "dialogue_text"):
            value = block.get(key) or rb.get(key)
            if value:
                return _clean_text(value)[:72]
        return "Chưa có anchor cảnh"

    def _build_left_review_panel(self, parent, block: dict, idx: int, issues: list):
        bid = _block_id(block, idx)
        panel = ctk.CTkFrame(parent, fg_color="#0b1220", corner_radius=8, width=172)
        panel.pack(side="left", fill="y", padx=10, pady=10)
        panel.pack_propagate(False)

        img_path = self._preview_image_path_for(block, idx)
        if Image is not None and img_path:
            try:
                with Image.open(img_path) as img:
                    img.thumbnail((156, 88))
                    preview = ctk.CTkImage(light_image=img.copy(), dark_image=img.copy(), size=(156, 88))
                self._preview_images.append(preview)
                ctk.CTkLabel(panel, image=preview, text="").pack(padx=8, pady=(8, 4))
            except Exception:
                ctk.CTkLabel(panel, text="Review cảnh", text_color="#38bdf8", height=88).pack(fill="x", padx=8, pady=(8, 4))
        else:
            ctk.CTkLabel(panel, text="Review cảnh\n(chưa có ảnh)", text_color="#38bdf8", height=88, justify="center").pack(fill="x", padx=8, pady=(8, 4))

        ctk.CTkLabel(panel, text=f"Block {bid}", text_color="#93c5fd", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="center")
        ctk.CTkLabel(panel, text=self._time_label_for(block, idx), text_color="#94a3b8", font=ctk.CTkFont(size=11)).pack(anchor="center")
        status = "Cần sửa" if issues else "OK"
        ctk.CTkLabel(panel, text=status, text_color="#f87171" if issues else "#22c55e", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="center", pady=(2, 0))
        ctk.CTkLabel(panel, text=self._review_anchor_for(block, idx), text_color="#cbd5e1", font=ctk.CTkFont(size=10), wraplength=150, justify="center").pack(fill="x", padx=8, pady=(3, 8))
        return panel

    # ---------------- Build UI ----------------
    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="#0b101a", height=56)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="SCRIPT EDITOR", text_color="#22d3ee", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=18)
        self._status_lbl = ctk.CTkLabel(header, text="", text_color="#94a3b8", font=ctk.CTkFont(size=12))
        self._status_lbl.pack(side="right", padx=18)

        toolbar = ctk.CTkFrame(self, fg_color="#111827", height=54)
        toolbar.pack(fill="x")
        self._confirm_btn = ctk.CTkButton(toolbar, text="XÁC NHẬN - TẠO VOICE", fg_color="#059669", command=self._on_confirm)
        self._confirm_btn.pack(side="left", padx=(12, 6), pady=10)
        ctk.CTkButton(toolbar, text="H\u1ee6Y", fg_color="#dc2626", command=self._on_cancel).pack(side="left", padx=6, pady=10)
        ctk.CTkButton(
            toolbar,
            text="\u1ea8N",
            width=70,
            fg_color="#475569",
            command=self._hide_editor,
        ).pack(side="left", padx=6, pady=10)
        ctk.CTkButton(toolbar, text="Lưu", fg_color="#334155", command=lambda: self._save_blocks(silent=False)).pack(side="left", padx=6, pady=10)
        ctk.CTkButton(toolbar, text="Auto-fill trống", fg_color="#7c3aed", command=self._auto_fill_empty).pack(side="left", padx=6, pady=10)
        ctk.CTkButton(toolbar, text="Tự chỉnh tốc độ", fg_color="#c2410c", command=lambda: self._auto_time_balance(silent=False)).pack(side="left", padx=6, pady=10)
        ctk.CTkButton(toolbar, text="AI s\u1eeda review", fg_color="#be185d", command=self._ai_fix_timing).pack(side="left", padx=6, pady=10)
        self._stop_voice_btn = ctk.CTkButton(
            toolbar,
            text="DỪNG VOICE",
            width=112,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            state="disabled",
            command=self._stop_voice_preview,
        )
        self._stop_voice_btn.pack(side="left", padx=6, pady=10)
        ctk.CTkButton(toolbar, text="Reset", fg_color="#1e293b", command=self._reload_from_package).pack(side="left", padx=6, pady=10)
        budget_seconds = float(
            self.ai_pkg.get("target_review_seconds")
            or self.ai_pkg.get("planned_duration_seconds")
            or (float(getattr(self.pipeline, "max_video_minutes", 0) or 0) * 60.0)
            or 0.0
        )
        budget_text = f"Ngân sách chapter: {budget_seconds / 60.0:g} phút" if budget_seconds else "Ngân sách chapter: tự động"
        ctk.CTkLabel(toolbar, text=budget_text, text_color="#facc15").pack(side="right", padx=(4, 10), pady=10)
        ctk.CTkEntry(toolbar, textvariable=self._search_var, width=210).pack(side="right", padx=12, pady=10)
        ctk.CTkButton(toolbar, text="T\u00ecm", width=54, fg_color="#334155", command=self._apply_search).pack(side="right", padx=4, pady=10)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="#05070d")
        self._scroll.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self._preview_panel = ctk.CTkFrame(self, fg_color="#07111f", height=430, corner_radius=8)
        self._preview_panel.pack(fill="x", padx=8, pady=(0, 6))
        self._preview_panel.pack_propagate(False)

        preview_top = ctk.CTkFrame(self._preview_panel, fg_color="transparent", height=232)
        preview_top.pack(fill="x", padx=10, pady=(10, 4))
        preview_top.pack_propagate(False)

        preview_screen = ctk.CTkFrame(preview_top, fg_color="#020617", width=430, height=232, corner_radius=8)
        preview_screen.pack(side="top", anchor="center")
        preview_screen.pack_propagate(False)
        self._bottom_image_lbl = ctk.CTkLabel(preview_screen, text="Chọn block để xem preview", text_color="#94a3b8")
        self._bottom_image_lbl.pack(fill="both", expand=True, padx=8, pady=8)

        preview_info = ctk.CTkFrame(self._preview_panel, fg_color="transparent", height=86)
        preview_info.pack(fill="x", padx=12, pady=(0, 4))
        preview_info.pack_propagate(False)
        left_info = ctk.CTkFrame(preview_info, fg_color="transparent")
        left_info.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(left_info, text="Review cảnh đã chọn", text_color="#22d3ee", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w")
        self._bottom_info_lbl = ctk.CTkLabel(left_info, text="Chưa chọn block", text_color="#cbd5e1", justify="left", anchor="w", wraplength=1250)
        self._bottom_info_lbl.pack(fill="x", pady=(3, 0))
        self._bottom_issue_lbl = ctk.CTkLabel(left_info, text="", text_color="#22c55e", justify="left", anchor="w", wraplength=1250)
        self._bottom_issue_lbl.pack(fill="x", pady=(2, 0))
        btn_row = ctk.CTkFrame(preview_info, fg_color="transparent")
        btn_row.pack(side="right", padx=(12, 0), pady=12)
        ctk.CTkButton(btn_row, text="Phát video block + voice", width=185, fg_color="#2563eb", command=self._play_selected_review_clip).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Dừng video", width=105, fg_color="#b91c1c", command=self._stop_selected_block_preview).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Về block đang chọn", width=150, fg_color="#0f766e", command=self._scroll_to_selected_preview).pack(side="left", padx=(0, 8))

        preview_timeline = ctk.CTkFrame(self._preview_panel, fg_color="#020617", height=112, corner_radius=8)
        preview_timeline.pack(fill="x", padx=12, pady=(0, 10))
        preview_timeline.pack_propagate(False)
        ctk.CTkLabel(preview_timeline, text="Timeline: VIDEO ở trên, VOICE/kịch bản ở dưới", text_color="#facc15", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=10, pady=(5, 0))
        self._timeline_canvas = tk.Canvas(preview_timeline, height=84, bg="#020617", highlightthickness=0)
        self._timeline_canvas.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        bottom = ctk.CTkFrame(self, fg_color="#0b1220", height=86)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self._bottom_warning_lbl = ctk.CTkLabel(bottom, text="", text_color="#22c55e", justify="left", anchor="w")
        self._bottom_warning_lbl.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(bottom, text="RECAP2: Script Editor l\u00e0 b\u01b0\u1edbc duy\u1ec7t k\u1ecbch b\u1ea3n. Sau khi b\u1ea5m X\u00e1c nh\u1eadn, TTS/render s\u1ebd d\u00f9ng text \u0111\u00e3 l\u01b0u \u1edf \u0111\u00e2y.", text_color="#94a3b8").pack(anchor="w", padx=12)

    def _render_blocks(self):
        for child in self._scroll.winfo_children():
            child.destroy()
        self._block_entries.clear()
        self._preview_images.clear()
        seen_details = []
        self._issue_ids.clear()
        query = _clean_text(self._search_var.get()).lower()
        for idx, block in enumerate(self.script_blocks, 1):
            bid = _block_id(block, idx)
            text = _clean_text(block.get("text") or "")
            if query and query not in text.lower() and query not in str(bid):
                continue
            dur = self._duration_for(block, idx)
            est = _estimate_tts_duration(text)
            rate, final_est, msg = _rate_for_duration(text, dur)
            issues = []
            if not text:
                issues.append("block trống")
            duplicate = _duplicate_match(text, seen_details)
            normalized = _normalized_script_text(text)
            tokens = set(normalized.split())
            sentences = _script_sentences(text)
            if duplicate:
                duplicate_of, duplicate_reason = duplicate
                issues.append(f"tr\u00f9ng/g\u1ea7n tr\u00f9ng v\u1edbi block {duplicate_of}")
                block["_duplicate_text"] = True
                block["_duplicate_of_block"] = duplicate_of
                block["_duplicate_reason"] = duplicate_reason
            else:
                block.pop("_duplicate_text", None)
                block.pop("_duplicate_of_block", None)
                block.pop("_duplicate_reason", None)
            if normalized:
                seen_details.append((bid, normalized, tokens, sentences))
            if not self._uses_recap2_budget_flow() and dur > 0 and text:
                if est < dur * 0.55:
                    issues.append("voice ng\u1eafn h\u01a1n c\u1ea3nh")
                if est / max(0.1, dur) > _MAX_TTS_SPEED:
                    issues.append("voice qu\u00e1 d\u00e0i")
            block["_editor_issues"] = issues
            if issues:
                self._issue_ids.add(bid)

            card_color = "#1f1520" if issues else "#111827"
            card = ctk.CTkFrame(self._scroll, fg_color=card_color, corner_radius=8)
            card.pack(fill="x", padx=8, pady=6)
            left_panel = self._build_left_review_panel(card, block, idx, issues)

            right = ctk.CTkFrame(card, fg_color="transparent")
            right.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=8)
            if self._uses_recap2_budget_flow():
                top_text = f"{len(text.split())} từ | TTS {rate} | voice dự kiến {final_est:.1f}s | nhịp RECAP2"
            else:
                top_text = f"{len(text.split())} từ | TTS {rate} | {msg}"
            if issues:
                top_text += " | CẢNH BÁO: " + ", ".join(issues)
            top_row = ctk.CTkFrame(right, fg_color="transparent")
            top_row.pack(fill="x")
            rate_lbl = ctk.CTkLabel(top_row, text=top_text, text_color="#f87171" if issues else "#22c55e", anchor="w")
            rate_lbl.pack(side="left", fill="x", expand=True)
            evidence = self._evidence_for(block, idx)
            if evidence:
                ctk.CTkLabel(right, text=evidence[:260], text_color="#64748b", anchor="w", justify="left").pack(fill="x", pady=(3, 4))
            box = ctk.CTkTextbox(right, height=82, fg_color="#142033", text_color="#e5e7eb", wrap="word")
            ctk.CTkButton(top_row, text="Xem", width=70, fg_color="#1d4ed8", command=lambda i=idx, b=box, bl=block, iss=list(issues): self._select_block_preview(i, bl, iss, b)).pack(side="right", padx=(8, 0))
            ctk.CTkButton(top_row, text="Review block", width=112, fg_color="#2563eb", command=lambda i=idx, b=box, bl=block, d=dur: self._review_single_block(i, b, bl, d)).pack(side="right", padx=(8, 0))
            voice_btn = ctk.CTkButton(top_row, text="Nghe voice", width=104, fg_color="#0f766e")
            voice_btn.configure(command=lambda i=idx, b=box, bl=block, d=dur, btn=voice_btn: self._preview_block_voice(i, b, bl, d, btn))
            voice_btn.pack(side="right", padx=(8, 0))
            box.pack(fill="x", expand=False)
            box.insert("1.0", text)
            def _select_this_block(_event=None, i=idx, b=box, bl=block, iss=list(issues)):
                self._select_block_preview(i, bl, iss, b)
            for widget in (card, left_panel, right, box, rate_lbl):
                try:
                    widget.bind("<Button-1>", _select_this_block, add="+")
                except Exception:
                    try:
                        widget.bind("<Button-1>", _select_this_block)
                    except Exception:
                        pass
            self._block_entries.append((box, block, rate_lbl, dur))
        self._update_bottom_warning()
        if self.script_blocks:
            selected = self._selected_preview or (1, self.script_blocks[0], self.script_blocks[0].get("_editor_issues") or [])
            try:
                self._select_block_preview(selected[0], selected[1], selected[2], selected[3] if len(selected) > 3 else None)
            except Exception:
                pass

    def _update_bottom_warning(self):
        ids = sorted(self._issue_ids)
        if ids:
            preview = ", ".join(str(x) for x in ids[:40])
            if len(ids) > 40:
                preview += f", +{len(ids)-40}"
            text = f"C\u1ea2NH B\u00c1O: c\u00f2n {len(ids)} block c\u1ea7n xem l\u1ea1i: {preview}"
            color = "#f87171"
        else:
            text = "OK: kh\u00f4ng c\u00f2n block tr\u1ed1ng, tr\u00f9ng ho\u1eb7c l\u1ed7i n\u1ed9i dung trong editor"
            color = "#22c55e"
        try:
            self._bottom_warning_lbl.configure(text=text, text_color=color)
        except Exception:
            pass

    def _timeline_total_duration(self) -> float:
        total = 0.0
        for idx, block in enumerate(self.script_blocks, 1):
            clip = _review_clip_for_block(block)
            try:
                end = float(clip.get("end") or block.get("original_end") or block.get("source_end") or 0.0)
                total = max(total, end)
            except Exception:
                pass
        return max(total, 1.0)

    def _draw_bottom_timeline(self, selected_bid: int, selected_start: float, selected_end: float):
        canvas = getattr(self, "_timeline_canvas", None)
        if canvas is None:
            return
        try:
            canvas.delete("all")
            w = max(int(canvas.winfo_width() or 980), 640)
            h = max(int(canvas.winfo_height() or 84), 76)
            total = self._timeline_total_duration()
            x0, x1 = 54, w - 12
            video_y0, video_y1 = 10, 32
            voice_y0, voice_y1 = 46, 68
            canvas.create_text(6, (video_y0 + video_y1) / 2, text="VIDEO", fill="#60a5fa", anchor="w", font=("Arial", 9, "bold"))
            canvas.create_rectangle(x0, video_y0, x1, video_y1, fill="#1e3a8a", outline="#3b82f6")
            canvas.create_text(6, (voice_y0 + voice_y1) / 2, text="VOICE", fill="#f472b6", anchor="w", font=("Arial", 9, "bold"))
            for idx, block in enumerate(self.script_blocks, 1):
                bid = _block_id(block, idx)
                clip = _review_clip_for_block(block)
                try:
                    start = float(clip.get("start") or block.get("original_start") or block.get("source_start") or 0.0)
                    end = float(clip.get("end") or block.get("original_end") or block.get("source_end") or start)
                except Exception:
                    start, end = 0.0, 0.0
                if end <= start:
                    continue
                sx = x0 + (start / total) * (x1 - x0)
                ex = x0 + (end / total) * (x1 - x0)
                color = "#ef4444" if block.get("_editor_issues") else "#8b5cf6"
                if bid == selected_bid:
                    color = "#22c55e"
                canvas.create_rectangle(sx, voice_y0, max(sx + 2, ex), voice_y1, fill=color, outline="#c4b5fd")
                if ex - sx > 10:
                    canvas.create_text((sx + ex) / 2, (voice_y0 + voice_y1) / 2, text=str(bid), fill="white", font=("Arial", 8))
            sx = x0 + (selected_start / total) * (x1 - x0)
            ex = x0 + (selected_end / total) * (x1 - x0)
            canvas.create_line(sx, 4, sx, h - 4, fill="#22c55e", width=2)
            canvas.create_rectangle(sx, video_y0 - 4, max(sx + 4, ex), video_y1 + 4, outline="#22c55e", width=2)
            canvas.create_text(x0, h - 4, text="0s", fill="#94a3b8", anchor="sw", font=("Arial", 8))
            canvas.create_text(x1, h - 4, text=f"{total:.0f}s", fill="#94a3b8", anchor="se", font=("Arial", 8))
        except Exception:
            pass

    def _preview_video_window(self, idx: int, block: dict):
        clip = _review_clip_for_block(block)
        rb = self._render_by_id().get(_block_id(block, idx), {})
        source = ""
        for candidate in (
            clip.get("source_video"),
            block.get("source_video"),
            rb.get("source_video"),
            getattr(self.pipeline, "video_path", ""),
        ):
            value = str(candidate or "").strip()
            if value and os.path.exists(value):
                source = value
                break
        try:
            start = float(
                clip.get("start")
                if clip.get("start") is not None
                else block.get("original_start")
                if block.get("original_start") is not None
                else block.get("source_start")
                or rb.get("original_start")
                or rb.get("source_start")
                or 0.0
            )
            end = float(
                clip.get("end")
                if clip.get("end") is not None
                else block.get("original_end")
                if block.get("original_end") is not None
                else block.get("source_end")
                or rb.get("original_end")
                or rb.get("source_end")
                or start + self._duration_for(block, idx)
            )
        except Exception:
            start, end = 0.0, 0.0
        return source, max(0.0, start), max(0.0, end)

    def _stop_embedded_video(self, silent: bool = False):
        self._embedded_video_token += 1
        after_id = self._embedded_video_after_id
        self._embedded_video_after_id = None
        if after_id:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        capture = self._embedded_video_capture
        self._embedded_video_capture = None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        if not silent:
            self._safe_status("Đã dừng video block trong khung preview", "#f59e0b")

    def _stop_selected_block_preview(self):
        """Stop both parts of the synchronized block preview."""
        self._stop_embedded_video(silent=True)
        self._stop_voice_preview(silent=True)
        self._safe_status("Đã dừng video block và voice", "#f59e0b")

    def _show_embedded_video_frame(self, frame) -> bool:
        if Image is None:
            return False
        try:
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            image.thumbnail((410, 216))
            canvas = Image.new("RGB", (410, 216), "black")
            x = max(0, (410 - image.width) // 2)
            y = max(0, (216 - image.height) // 2)
            canvas.paste(image, (x, y))
            self._bottom_preview_image = ctk.CTkImage(
                light_image=canvas,
                dark_image=canvas,
                size=(410, 216),
            )
            self._bottom_image_lbl.configure(image=self._bottom_preview_image, text="")
            return True
        except Exception:
            return False

    def _advance_embedded_video(self, token: int, interval_ms: int, end_frame: int, bid: int):
        if token != self._embedded_video_token or not self._ui_alive():
            return
        capture = self._embedded_video_capture
        if capture is None:
            return
        try:
            import cv2
            current_frame = int(capture.get(cv2.CAP_PROP_POS_FRAMES) or 0)
            if end_frame > 0 and current_frame >= end_frame:
                self._stop_embedded_video(silent=True)
                self._safe_status(f"Đã phát xong video block {bid}", "#22c55e")
                return
            ok, frame = capture.read()
            if not ok:
                self._stop_embedded_video(silent=True)
                self._safe_status(f"Video block {bid} đã phát tới cuối nguồn", "#22c55e")
                return
            self._show_embedded_video_frame(frame)
            self._embedded_video_after_id = self.after(
                interval_ms,
                lambda: self._advance_embedded_video(token, interval_ms, end_frame, bid),
            )
        except Exception as exc:
            self._stop_embedded_video(silent=True)
            self._safe_status(f"Không phát được video block {bid}: {str(exc)[:100]}", "#f87171")

    def _play_selected_video_in_panel(self):
        if not self._selected_preview:
            self._safe_status("Chưa chọn block để phát video", "#f59e0b")
            return
        idx, block = self._selected_preview[:2]
        bid = _block_id(block, idx)
        source, start, end = self._preview_video_window(idx, block)
        if not source or not os.path.exists(source):
            self._safe_status(f"Block {bid}: không tìm thấy video nguồn", "#f87171")
            return
        if end <= start + 0.05:
            self._safe_status(f"Block {bid}: timestamp video không hợp lệ", "#f87171")
            return
        self._stop_embedded_video(silent=True)
        try:
            import cv2
            capture = cv2.VideoCapture(source)
            if not capture.isOpened():
                capture.release()
                raise RuntimeError("OpenCV không mở được video nguồn")
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
            if fps <= 1.0 or fps > 120.0:
                fps = 25.0
            capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
            self._embedded_video_capture = capture
            self._embedded_video_token += 1
            token = self._embedded_video_token
            interval_ms = max(15, min(80, int(round(1000.0 / fps))))
            end_frame = int(round(end * fps))
            self._safe_status(
                f"Đang phát video block {bid}: {start:.1f}s -> {end:.1f}s trong khung preview",
                "#60a5fa",
            )
            self._advance_embedded_video(token, interval_ms, end_frame, bid)
        except Exception as exc:
            self._stop_embedded_video(silent=True)
            self._safe_status(f"Không phát được video block {bid}: {str(exc)[:110]}", "#f87171")

    def _select_block_preview(self, idx: int, block: dict, issues=None, box=None):
        self._stop_embedded_video(silent=True)
        self._stop_voice_preview(silent=True)
        bid = _block_id(block, idx)
        issues = list(issues if issues is not None else (block.get("_editor_issues") or []))
        clip = _review_clip_for_block(block)
        start = 0.0
        end = 0.0
        try:
            start = float(clip.get("start") or block.get("original_start") or block.get("source_start") or 0.0)
            end = float(clip.get("end") or block.get("original_end") or block.get("source_end") or start + self._duration_for(block, idx))
        except Exception:
            pass
        dur = max(0.0, end - start)
        self._selected_preview = (idx, block, issues, box)
        img_path = self._preview_image_path_for(block, idx)
        try:
            if Image is not None and img_path:
                with Image.open(img_path) as img:
                    img.thumbnail((410, 230))
                    self._bottom_preview_image = ctk.CTkImage(light_image=img.copy(), dark_image=img.copy(), size=(410, 230))
                self._bottom_image_lbl.configure(image=self._bottom_preview_image, text="")
            else:
                self._bottom_preview_image = None
                self._bottom_image_lbl.configure(image=None, text="Chưa có thumbnail cho block này")
        except Exception:
            self._bottom_preview_image = None
            self._bottom_image_lbl.configure(image=None, text="Không đọc được thumbnail")
        text = _clean_text(box.get("1.0", "end") if box is not None else (block.get("text") or block.get("script_text") or ""))
        info = (
            f"Block {bid} | nguồn {start:.1f}s -> {end:.1f}s | cảnh {dur:.1f}s\n"
            f"{text[:520]}{'...' if len(text) > 520 else ''}"
        )
        self._bottom_info_lbl.configure(text=info)
        if issues:
            self._bottom_issue_lbl.configure(text="CẢNH BÁO: " + ", ".join(str(x) for x in issues), text_color="#f87171")
        else:
            self._bottom_issue_lbl.configure(text="OK: block này chưa có cảnh báo trong editor", text_color="#22c55e")
        self._draw_bottom_timeline(bid, start, end)

    def _scroll_to_selected_preview(self):
        # CTkScrollableFrame không có API scroll-to-widget ổn định ở mọi bản.
        # Nút này giữ vai trò xác nhận block đang chọn trong panel dưới.
        if self._selected_preview:
            idx, block, _issues = self._selected_preview[:3]
            self._safe_status(f"Đang xem Block {_block_id(block, idx)} ở panel dưới", "#60a5fa")

    def _play_selected_review_clip(self):
        if self._voice_preview_running:
            self._safe_status("Đang phát một block khác; hãy bấm Dừng video trước", "#f59e0b")
            return
        if not self._selected_preview:
            self._safe_status("Chưa chọn block để phát cảnh", "#f59e0b")
            return
        idx, block, _issues, box = (list(self._selected_preview) + [None])[:4]
        bid = _block_id(block, idx)
        source, start, end = self._preview_video_window(idx, block)
        duration = max(0.1, end - start)
        if not source or not os.path.exists(source):
            self._safe_status(f"Block {bid}: không tìm thấy video nguồn để phát cảnh", "#f87171")
            return
        text = _clean_text(box.get("1.0", "end") if box is not None else (block.get("text") or block.get("script_text") or ""))
        if not text:
            self._safe_status(f"Block {bid}: chưa có text để tạo voice preview", "#f87171")
            return
        rate = str(block.get("_tts_rate") or _rate_for_duration(text, duration)[0] or "+0%")
        voice_id = self._resolve_voice_id_for_preview()
        out_dir = self._output_dir() / ".preview_scene"
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_path = str(out_dir / f"block_{bid:04d}_voice.mp3")
        cancel_event = threading.Event()
        self._preview_cancel_event = cancel_event
        self._voice_preview_running = True
        self._set_stop_voice_enabled(True)
        self._safe_status(f"Đang tạo voice cho video block {bid}...", "#60a5fa")
        def worker():
            stopped = False
            failed = False
            try:
                import asyncio
                from engine.ai_engine import AIEngine
                ai = AIEngine("")
                asyncio.run(ai.text_to_speech(text, audio_path, voice=voice_id, rate=rate))
                if cancel_event.is_set():
                    stopped = True
                    return
                video_started = threading.Event()

                def start_video_in_panel():
                    self._play_selected_video_in_panel()
                    video_started.set()

                self._safe_after(0, start_video_in_panel)
                video_started.wait(timeout=2.0)
                if cancel_event.is_set():
                    stopped = True
                    return
                self._safe_after(0, lambda: self._safe_status(f"Đang phát video block + voice {bid}...", "#60a5fa"))
                ok, err = self._play_audio_file(audio_path, cancel_event=cancel_event)
                if not ok and not cancel_event.is_set():
                    raise RuntimeError(err or "Không phát được voice của video block")
                stopped = cancel_event.is_set()
            except Exception as exc:
                failed = True
                self._safe_after(0, lambda e=str(exc): self._safe_status(f"Lỗi phát video block + voice {bid}: {e[:120]}", "#f87171"))
            finally:
                def finish():
                    self._voice_preview_running = False
                    if self._preview_cancel_event is cancel_event:
                        self._preview_cancel_event = None
                    self._set_stop_voice_enabled(False)
                    if stopped or cancel_event.is_set():
                        self._safe_status(f"Đã dừng video block + voice {bid}", "#f59e0b")
                    elif not failed and not self._active_preview_process:
                        self._safe_status(f"Đã phát xong voice block {bid}; video tiếp tục đến hết cảnh", "#22c55e")
                self._safe_after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Actions ----------------
    def _apply_search(self):
        self._render_blocks()

    def _reload_from_package(self):
        self.script_blocks = self._load_script_blocks()
        self._render_blocks()
        self._safe_status("\u0110\u00e3 reset theo ai_package hi\u1ec7n t\u1ea1i", "#22c55e")

    def _auto_fill_empty(self):
        fixed = 0
        for idx, (box, block, _rate_lbl, _dur) in enumerate(self._block_entries, 1):
            text = _clean_text(box.get("1.0", "end"))
            if text:
                continue
            fallback = self._evidence_for(block, idx) or "Nh\u1ecbp truy\u1ec7n ti\u1ebfp t\u1ee5c \u0111\u1ea9y nh\u00e2n v\u1eadt v\u00e0o m\u1ed9t l\u1ef1a ch\u1ecdn kh\u00f3 h\u01a1n, m\u1edf \u0111\u01b0\u1eddng cho bi\u1ebfn c\u1ed1 k\u1ebf ti\u1ebfp."
            fallback = _clean_text(fallback)
            if len(fallback.split()) < 12:
                fallback = fallback + " \u0110i\u1ec1u n\u00e0y khi\u1ebfn m\u1ea1ch phim c\u0103ng h\u01a1n v\u00e0 bu\u1ed9c nh\u00e2n v\u1eadt ph\u1ea3i ph\u1ea3n \u1ee9ng ngay trong c\u1ea3nh sau."
            box.delete("1.0", "end")
            box.insert("1.0", fallback)
            fixed += 1
        self._save_blocks(silent=True)
        self._render_blocks()
        self._safe_status(f"Auto-fill tr\u1ed1ng: \u0111\u00e3 \u0111i\u1ec1n {fixed} block", "#22c55e" if fixed else "#94a3b8")

    def _auto_time_balance(self, silent=False):
        changed = 0
        for box, block, _rate_lbl, dur in self._block_entries:
            text = _clean_text(box.get("1.0", "end"))
            rate, final_est, msg = _rate_for_duration(text, dur)
            block["_tts_rate"] = rate
            block["auto_time_balanced"] = True
            block["estimated_tts_duration"] = _estimate_tts_duration(text)
            block["estimated_final_voice_duration"] = final_est
            block["auto_time_message"] = msg
            changed += 1
        self._save_blocks(silent=True)
        self._render_blocks()
        if not silent:
            self._safe_status(f"Tự chỉnh tốc độ xong: {changed} block. Kịch bản không bị cắt hoặc chèn thêm.", "#22c55e")

    def _blocks_needing_ai(self) -> list[tuple[int, object, dict, float]]:
        payload = []
        seen_details = []
        for idx, (box, block, _rate_lbl, dur) in enumerate(self._block_entries, 1):
            bid = _block_id(block, idx)
            text = _clean_text(box.get("1.0", "end"))
            reasons = []
            if not text:
                reasons.append("empty")
            duplicate = _duplicate_match(text, seen_details)
            normalized = _normalized_script_text(text)
            tokens = set(normalized.split())
            sentences = _script_sentences(text)
            if duplicate:
                reasons.append("duplicate")
                block["_duplicate_text"] = True
                block["_duplicate_of_block"] = duplicate[0]
                block["_duplicate_reason"] = duplicate[1]
            if normalized:
                seen_details.append((bid, normalized, tokens, sentences))
            if not self._uses_recap2_budget_flow():
                est = _estimate_tts_duration(text)
                if dur > 0 and text and est < dur * 0.55:
                    reasons.append("short_voice")
                if dur > 0 and text and est / max(0.1, dur) > _MAX_TTS_SPEED:
                    reasons.append("too_long")
            for issue in block.get("_editor_issues") or []:
                folded = str(issue).lower()
                if any(token in folded for token in ("voice", "empty", "duplicate", "trung", "trùng", "canh bao", "cảnh báo")):
                    reasons.append("editor_warning")
                    break
            if reasons:
                block["ai_repair_reasons"] = sorted(set(reasons))
                payload.append((idx, box, block, dur))
        return payload

    def _ai_fix_timing(self):
        if self._ai_fix_running:
            return
        targets = self._blocks_needing_ai()
        if not targets:
            self._safe_status("AI s\u1eeda review: kh\u00f4ng c\u00f3 block l\u1ed7i r\u00f5 r\u00e0ng", "#22c55e")
            return
        self._ai_fix_running = True
        self._safe_confirm_button("disabled", "\u0110ANG AI S\u1eeca...")
        self._safe_status(f"AI s\u1eeda review: \u0111ang g\u1ecdi Gemini Web s\u1eeda {len(targets)} block l\u1ed7i theo nh\u00f3m 10", "#60a5fa")
        threading.Thread(target=self._ai_fix_worker, args=(targets,), daemon=True).start()

    def _review_single_block(self, idx: int, box, block: dict, dur: float):
        """Send exactly one block to the same Gemini repair path used by AI sửa review."""
        if self._ai_fix_running:
            self._safe_status("AI đang sửa block khác, chờ xong rồi bấm lại", "#f59e0b")
            return
        bid = _block_id(block, idx)
        current = _clean_text(box.get("1.0", "end"))
        reasons = list(block.get("_editor_issues") or [])
        if not current:
            reasons.append("block trống")
        if not reasons:
            reasons.append("review thủ công block này")
        block["ai_repair_reasons"] = sorted(set(str(r) for r in reasons if str(r).strip()))
        self._ai_fix_running = True
        self._safe_confirm_button("disabled", "ĐANG AI SỬA...")
        self._safe_status(f"Review block {bid}: đang gọi Gemini Web", "#60a5fa")
        threading.Thread(target=self._ai_fix_worker, args=([(idx, box, block, dur)],), daemon=True).start()

    def _ai_fix_worker(self, targets):
        fixed = 0
        error = None
        try:
            from engine.ai_engine import AIEngine
            ai = AIEngine(getattr(self.pipeline, "gemini_api_key", "") or os.environ.get("GEMINI_API_KEY", ""))
            movie_title = getattr(self.pipeline, "movie_title", "") or self.ai_pkg.get("movie_title") or "phim"
            current_text_by_id = {
                _block_id(block, idx): _clean_text(box.get("1.0", "end"))
                for idx, (box, block, _rate_lbl, _dur) in enumerate(self._block_entries, 1)
            }
            for start in range(0, len(targets), 10):
                batch = targets[start:start+10]
                payload = []
                for idx, _box, block, dur in batch:
                    bid = _block_id(block, idx)
                    current = _clean_text(_box.get("1.0", "end"))
                    if self._uses_recap2_budget_flow():
                        preferred_words = max(12, int(block.get("target_words") or len(current.split()) or 24))
                        min_words = max(8, int(preferred_words * 0.65))
                        max_words = max(min_words + 8, int(preferred_words * 1.35))
                        timing_instruction = (
                            f"Giữ gần {preferred_words} từ theo ngân sách chapter. "
                            "Không cố lấp đầy toàn bộ source scene; ưu tiên mạch truyện và evidence."
                        )
                    else:
                        min_words, preferred_words, max_words = _word_bounds_for_duration(dur)
                        timing_instruction = (
                            f"Viết khoảng {preferred_words} từ, tối thiểu {min_words}, tối đa {max_words}. "
                            "Nếu short_voice thì viết dài hơn; nếu too_long thì rút gọn."
                        )
                    role_meta = narrative_role_for_position(idx, len(self._block_entries))
                    duplicate_of = block.get("_duplicate_of_block")
                    payload.append({
                        "block_id": bid,
                        "current_text": current,
                        "scene_duration_seconds": dur,
                        "estimated_voice_seconds": _estimate_tts_duration(current),
                        "min_words_to_clear_red": min_words,
                        "preferred_words": preferred_words,
                        "max_words_before_too_long": max_words,
                        "timing_fix_instruction": timing_instruction,
                        "repair_reasons": block.get("ai_repair_reasons") or block.get("_editor_issues") or [],
                        "duplicate_of_block": duplicate_of,
                        "must_not_repeat_text": current_text_by_id.get(duplicate_of, "") if duplicate_of else "",
                        "scene_role": block.get("scene_role") or block.get("narrative_role") or role_meta.get("scene_role"),
                        "scene_role_label": block.get("scene_role_label") or role_meta.get("scene_role_label"),
                        "srt_reference": self._evidence_for(block, idx),
                        "scene_ids": block.get("scene_ids") or block.get("source_block_ids") or [],
                        "source_block_ids": block.get("source_block_ids") or [],
                        "visual_hint": block.get("visual_hint") or block.get("visual_anchor") or "",
                        "keyframe_refs": block.get("keyframe_refs") or block.get("keyframes") or [],
                        "mapping_reason": block.get("mapping_reason") or block.get("semantic_reason") or "",
                        "character_focus": block.get("character_focus") or block.get("main_characters") or "",
                    })
                # Import shared rules từ recap_engine để đồng bộ với AI_FULL
                try:
                    from core.recap_engine import _recap2_script_prompt_rules as _get_rules
                    _shared = _get_rules()
                except Exception:
                    _shared = ""
                try:
                    from core.review_styles import review_style_instruction, story_writer_instruction
                    _style = review_style_instruction()
                    _story = story_writer_instruction()
                except Exception:
                    _style = _story = ""
                _prompt_pack = recap_prompt_pack_text()
                _block_rules = recap_block_prompt_rules()
                _role_rules = narrative_role_rules_text()
                prompt = (
                    "Ban la bien tap vien review phim tieng Viet.\n"
                    f"PHIM: {movie_title}\n\n"
                    "REVIEW STYLE:\n" + _style + "\n\n"
                    "STORY WRITER:\n" + _story + "\n\n"
                    "RECAP2 QUALITY GATE:\n" + _shared + "\n\n"
                    "RECAP PROMPT PACK:\n" + _prompt_pack + "\n\n"
                    "BLOCK PROMPT RULES:\n" + _block_rules + "\n\n"
                    "NARRATIVE ROLE MAP:\n" + _role_rules + "\n\n"
                    "HARD RULES:\n"
                    "- Tra ve ONLY JSON hop le, khong markdown.\n"
                    '- JSON: {"script_blocks":[{"block_id":1,"text":"..."}]}\n'
                    "- Tieng Viet CO DAU, van review chuyen nghiep, tu nhien.\n"
                    "- Bam srt_reference, scene_ids/source_block_ids, visual_hint va keyframe_refs neu co. Khong bia canh.\n"
                    "- Narration phai mo ta dung visual actions cua block, khong tron su kien khac.\n"
                    "- Khong lap cau giua cac block.\n"
                    "- Neu repair_reasons co duplicate: PHAI viet lai toan bo loi ke; TUYET DOI khong giu nguyen cau/cum cau trong must_not_repeat_text.\n"
                    "- Khong copy thoai; chuyen thanh loi thuyet minh review.\n"
                    "- Dung timing_fix_instruction va ngan sach chapter; khong viet dem de lap day toan bo source scene.\n"
                    "- Khong cat mat y chinh, khong them cau chung chung chi de tang so tu.\n"
                    "- KHONG viet cho logo, quang cao, ca do, credits.\n"
                    "- CHARACTER_FOCUS: neu co, bam nhan vat do.\n"
                    "- SCENE_ROLE: hook_intro chi dung cho block dau; ending_aftertaste chi dung cho block cuoi; block giua khong mo dau lai/to ket lai.\n"
                    "- Neu la block cuoi / ending_aftertaste: ket bang dung 1 cau CTA ngan keu goi theo doi kenh va don xem tap moi nhat hoac phan tiep theo. Block giua KHONG duoc them CTA.\n"
                    "INPUT_BLOCKS:\n"
                    f"{json.dumps(payload, ensure_ascii=False)}\n"
                )
                try:
                    raw = ai._try_generate_gemini_web(prompt)
                except Exception:
                    raw = ai._try_generate(prompt)
                data = ai._extract_json_payload(raw) or {}
                items = data.get("script_blocks") if isinstance(data, dict) else []
                by_id = {}
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            try:
                                by_id[int(item.get("block_id") or 0)] = _clean_text(item.get("text") or "")
                            except Exception:
                                pass
                for idx, box, block, _dur in batch:
                    bid = _block_id(block, idx)
                    text = by_id.get(bid)
                    if text and len(text.split()) >= 5:
                        text = _clean_text(text)
                        block.pop("_duplicate_text", None)
                        block.pop("_duplicate_of_block", None)
                        block.pop("_duplicate_reason", None)
                        self._safe_after(0, lambda b=box, t=text: (b.delete("1.0", "end"), b.insert("1.0", t)))
                        fixed += 1
        except Exception as exc:
            error = str(exc)
            # Deterministic fallback: at least clean empty/duplicate artifacts.
            for idx, box, block, _dur in targets:
                text = _clean_text(box.get("1.0", "end")) or self._evidence_for(block, idx)
                if text and len(text.split()) < 12:
                    text += " Nhịp này làm áp lực tăng lên và kéo câu chuyện sang bước ngoặt tiếp theo."
                if text:
                    text = _clean_text(text)
                    self._safe_after(0, lambda b=box, t=text: (b.delete("1.0", "end"), b.insert("1.0", t)))
                    fixed += 1
        finally:
            try:
                from engine.ai_engine import AIEngine
                AIEngine.close_web_driver()
            except Exception:
                pass

            def finish():
                self._ai_fix_running = False
                self._safe_confirm_button("normal", "XÁC NHẬN - TẠO VOICE")
                deduped = self._dedupe_current_entries()
                self._save_blocks(silent=True)
                self._render_blocks()
                if error:
                    self._safe_status(f"AI lỗi, đã dùng sửa nội bộ: {fixed} block, chống trùng {deduped} block ({error[:70]})", "#f59e0b")
                else:
                    self._safe_status(f"AI sửa review đã sửa {fixed}/{len(targets)} block, chống trùng {deduped} block, đã đóng Gemini Web", "#22c55e" if fixed else "#f59e0b")
            self._safe_after(0, finish)

    def _set_stop_voice_enabled(self, enabled: bool):
        def update():
            try:
                self._stop_voice_btn.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        self._safe_after(0, update)

    def _run_preview_process(self, command, timeout: int, startupinfo, cancel_event):
        """Run ffmpeg/ffplay while retaining a process handle for Stop."""
        import subprocess
        if cancel_event is not None and cancel_event.is_set():
            return -1, "Đã dừng theo yêu cầu"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
        )
        self._active_preview_process = process
        try:
            _stdout, stderr = process.communicate(timeout=timeout)
            if cancel_event is not None and cancel_event.is_set():
                return -1, "Đã dừng theo yêu cầu"
            return int(process.returncode or 0), str(stderr or "")
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            process.communicate()
            return -1, "Preview quá thời gian cho phép"
        finally:
            if self._active_preview_process is process:
                self._active_preview_process = None

    def _stop_voice_preview(self, silent: bool = False):
        """Stop voice-only or scene+voice playback started by the editor."""
        event = self._preview_cancel_event
        if event is not None:
            event.set()
        process = self._active_preview_process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if not silent:
            if self._voice_preview_running:
                self._safe_status("Đã yêu cầu dừng voice preview", "#f59e0b")
            else:
                self._safe_status("Hiện không có voice preview đang phát", "#94a3b8")

    def _resolve_voice_id_for_preview(self) -> str:
        parent = getattr(self, "parent", None)
        try:
            if parent and hasattr(parent, "voice_choice") and hasattr(parent, "_resolve_voice_id"):
                label = parent.voice_choice.get()
                lang = parent.tts_language.get() if hasattr(parent, "tts_language") else "Tiếng Việt"
                voice_id = parent._resolve_voice_id(label, lang)
                if voice_id:
                    return voice_id
        except Exception:
            pass
        return str(getattr(self.pipeline, "voice", "") or "vi-VN-HoaiMyNeural")

    def _play_audio_file(self, audio_path: str, cancel_event=None) -> tuple[bool, str]:
        if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 0:
            return False, "File voice preview rỗng hoặc không tồn tại"
        try:
            ffplay = None
            parent = getattr(self, "parent", None)
            if parent and hasattr(parent, "_get_ffplay"):
                try:
                    ffplay = parent._get_ffplay()
                except Exception:
                    ffplay = None
            if not ffplay:
                ffplay = shutil.which("ffplay") or shutil.which("ffplay.exe")
            if not ffplay:
                return False, "Không tìm thấy ffplay để phát voice preview"
            startupinfo = None
            if parent and hasattr(parent, "_subprocess_startupinfo"):
                try:
                    startupinfo = parent._subprocess_startupinfo()
                except Exception:
                    startupinfo = None
            code, stderr = self._run_preview_process(
                [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                timeout=90,
                startupinfo=startupinfo,
                cancel_event=cancel_event,
            )
            if code != 0:
                return False, stderr or "ffplay dừng hoặc phát voice thất bại"
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _preview_block_voice(self, idx: int, box, block: dict, dur: float, button=None):
        if self._voice_preview_running:
            self._safe_status("Đang nghe voice block khác, chờ xong rồi bấm lại", "#f59e0b")
            return
        text = _clean_text(box.get("1.0", "end"))
        if not text:
            self._safe_status("Block này đang trống, chưa có text để nghe voice", "#f87171")
            return
        bid = _block_id(block, idx)
        rate = str(block.get("_tts_rate") or _rate_for_duration(text, dur)[0] or "+0%")
        voice_id = self._resolve_voice_id_for_preview()
        out_dir = self._output_dir() / ".preview_voice"
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_path = str(out_dir / f"block_{bid:04d}_preview.mp3")
        cancel_event = threading.Event()
        self._preview_cancel_event = cancel_event
        self._voice_preview_running = True
        self._set_stop_voice_enabled(True)
        try:
            if button is not None:
                button.configure(state="disabled", text="Đang tạo...")
        except Exception:
            pass
        self._safe_status(f"Đang tạo voice preview block {bid} ({voice_id}, tốc độ {rate})", "#60a5fa")

        def worker():
            ok = False
            err = ""
            try:
                import asyncio
                from engine.ai_engine import AIEngine
                ai = AIEngine("")
                asyncio.run(ai.text_to_speech(text, audio_path, voice=voice_id, rate=rate))
                if cancel_event.is_set():
                    err = "Đã dừng theo yêu cầu"
                else:
                    self._safe_after(
                        0,
                        lambda: button is not None and button.configure(text="Đang phát..."),
                    )
                    ok, err = self._play_audio_file(audio_path, cancel_event=cancel_event)
            except Exception as exc:
                err = str(exc)

            def finish():
                self._voice_preview_running = False
                if self._preview_cancel_event is cancel_event:
                    self._preview_cancel_event = None
                self._set_stop_voice_enabled(False)
                try:
                    if button is not None:
                        button.configure(state="normal", text="Nghe voice")
                except Exception:
                    pass
                if cancel_event.is_set():
                    self._safe_status(f"Đã dừng voice preview block {bid}", "#f59e0b")
                elif ok:
                    self._safe_status(f"Đã nghe xong voice block {bid}", "#22c55e")
                else:
                    self._safe_status(f"Không nghe được voice block {bid}: {err[:120]}", "#f87171")
            self._safe_after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _dedupe_current_entries(self):
        seen_details = []
        changed = 0
        for idx, (box, block, _rate_lbl, dur) in enumerate(self._block_entries, 1):
            text = _clean_text(box.get("1.0", "end"))
            normalized = _normalized_script_text(text)
            if not normalized:
                continue
            duplicate = _duplicate_match(text, seen_details)
            if not duplicate:
                seen_details.append(
                    (_block_id(block, idx), normalized, set(normalized.split()), _script_sentences(text))
                )
                continue
            duplicate_of, duplicate_reason = duplicate
            evidence = self._evidence_for(block, idx)
            repeated_sentences = set()
            for previous_id, _previous_text, _tokens, previous_sentences in seen_details:
                if previous_id == duplicate_of:
                    repeated_sentences = previous_sentences
                    break
            unique_parts = [
                sentence
                for sentence in re.split(r"(?<=[.!?])\s+", text)
                if _normalized_script_text(sentence) not in repeated_sentences
            ]
            anchor_parts = [
                _clean_text(part)
                for part in re.split(r"[|\n]+", evidence or "")
                if len(_clean_text(part).split()) >= 4
            ]
            base = _clean_text(" ".join(unique_parts))
            if len(base.split()) < 8 and anchor_parts:
                base = _clean_text(
                    f"Trong diễn biến này, {anchor_parts[0]}. "
                    + (
                        f"Chi tiết {anchor_parts[1]} đẩy mạch truyện sang một hướng khác."
                        if len(anchor_parts) > 1
                        else "Phản ứng của nhân vật làm mâu thuẫn trong cảnh trở nên rõ hơn."
                    )
                )
            if len(base.split()) < 8:
                block["_duplicate_text"] = True
                block["_duplicate_of_block"] = duplicate_of
                block["_duplicate_reason"] = duplicate_reason
                seen_details.append(
                    (_block_id(block, idx), normalized, set(normalized.split()), _script_sentences(text))
                )
                continue
            new_text = (
                _fit_text_to_duration(base, evidence, dur)
                if not self._uses_recap2_budget_flow()
                else base
            )
            remaining_duplicate = _duplicate_match(new_text, seen_details)
            if remaining_duplicate:
                block["_duplicate_text"] = True
                block["_duplicate_of_block"] = remaining_duplicate[0]
                block["_duplicate_reason"] = remaining_duplicate[1]
                seen_details.append(
                    (_block_id(block, idx), normalized, set(normalized.split()), _script_sentences(text))
                )
                continue
            box.delete("1.0", "end")
            box.insert("1.0", new_text)
            block.pop("_duplicate_text", None)
            block.pop("_duplicate_of_block", None)
            block.pop("_duplicate_reason", None)
            new_normalized = _normalized_script_text(new_text)
            seen_details.append(
                (_block_id(block, idx), new_normalized, set(new_normalized.split()), _script_sentences(new_text))
            )
            changed += 1
        return changed

    def _save_blocks(self, silent=False):
        changed_blocks = []
        output_dir = self._output_dir()
        for idx, (box, block, _rate_lbl, dur) in enumerate(self._block_entries, 1):
            text = _clean_text(box.get("1.0", "end"))
            block["text"] = text
            block["script_text"] = text
            block["editor_text"] = text
            block["voice_text"] = text
            block["voice_source"] = "script_editor"
            block["script_editor_synced"] = True
            block["script_editor_locked"] = True
            block["duration_hint_seconds"] = dur or block.get("duration_hint_seconds")
            _ensure_review_clip(block, output_dir)
            changed_blocks.append(block)
        self.script_blocks = changed_blocks
        sync_basis = json.dumps([[_block_id(b, i+1), b.get("text", "")] for i, b in enumerate(changed_blocks)], ensure_ascii=False)
        sync_id = hashlib.sha1(sync_basis.encode("utf-8", errors="ignore")).hexdigest()[:16]
        self.ai_pkg["script_blocks"] = changed_blocks
        self.ai_pkg["script"] = "\n\n".join(b.get("text", "") for b in changed_blocks if b.get("text"))
        self.ai_pkg["subtitle_chunks"] = [b.get("text", "") for b in changed_blocks if b.get("text")]
        self.ai_pkg["script_editor_synced"] = True
        self.ai_pkg["script_editor_locked"] = True
        self.ai_pkg["voice_source"] = "script_editor"
        self.ai_pkg["script_editor_sync_version"] = 2
        self.ai_pkg["script_editor_sync_id"] = sync_id
        self.ai_pkg["script_editor_saved_at"] = time.time()
        self.pipeline.ai_package = self.ai_pkg
        self._sync_render_blocks(sync_id)
        self._write_outputs()
        self._invalidate_voice_outputs()
        if not silent:
            self._safe_status(f"\u0110\u00e3 l\u01b0u {len(changed_blocks)} block v\u00e0 \u0111\u1ed3ng b\u1ed9 xu\u1ed1ng pipeline", "#22c55e")
        return changed_blocks

    def _sync_render_blocks(self, sync_id: str):
        if not self.render_blocks:
            return
        by_id = {_block_id(b, i+1): b for i, b in enumerate(self.script_blocks)}
        strategy = str(self.ai_pkg.get("script_block_strategy") or "").lower()
        if strategy == "chapter_budget_story_segment":
            return
        for idx, rb in enumerate(self.render_blocks, 1):
            if not isinstance(rb, dict):
                continue
            bid = _block_id(rb, idx)
            block = by_id.get(bid)
            if not block:
                continue
            text = block.get("text") or ""
            rb["script_text"] = text
            rb["editor_text"] = text
            rb["voice_text"] = text
            rb["voice_source"] = "script_editor"
            rb["script_editor_synced"] = True
            rb["script_editor_locked"] = True
            rb["script_editor_sync_id"] = sync_id
            rb["script_editor_sync_version"] = 2
            if block.get("_tts_rate"):
                rb["_tts_rate"] = block.get("_tts_rate")
        self.pipeline.render_blocks = self.render_blocks

    def _write_outputs(self):
        output_dir = Path(getattr(self.pipeline, "output_dir", "") or "")
        if not output_dir:
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            (output_dir / "ai_package.json").write_text(json.dumps(self.ai_pkg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        try:
            if self.render_blocks:
                (output_dir / "render_blocks.json").write_text(json.dumps(self.render_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _invalidate_voice_outputs(self):
        output_dir = Path(getattr(self.pipeline, "output_dir", "") or "")
        if not output_dir:
            return
        targets = [
            "voice_segments", "voice_segments.json", "voice_track.mp3", "voice_track.wav",
            "voice_subtitles.srt", "voice_srt.json", "final_video.mp4",
        ]
        for name in targets:
            path = output_dir / name
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            except Exception:
                pass

    def _release_video_preview(self):
        self._stop_embedded_video(silent=True)
        self._stop_voice_preview(silent=True)
        return None

    def _hide_editor(self):
        """Minimize without confirming, cancelling or losing current edits."""
        self._release_video_preview()
        try:
            self.iconify()
        except Exception:
            try:
                self.withdraw()
            except Exception:
                pass

    def restore_editor(self):
        """Restore the same editing session from the main-window button."""
        if not self._ui_alive():
            return False
        try:
            self.deiconify()
            self.state("zoomed")
        except Exception:
            try:
                self.deiconify()
            except Exception:
                return False
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass
        return True

    def _clear_parent_editor_reference(self):
        try:
            if getattr(self.parent, "_active_script_editor", None) is self:
                self.parent._active_script_editor = None
        except Exception:
            pass

    def _on_confirm(self):
        if self._ai_fix_running:
            self._safe_status("AI \u0111ang s\u1eeda, ch\u1edd xong r\u1ed3i m\u1edbi t\u1ea1o voice", "#f59e0b")
            return
        self._save_blocks(silent=True)
        empties = [i for i, b in enumerate(self.script_blocks, 1) if not _clean_text(b.get("text"))]
        if empties:
            self._safe_status(f"C\u00f2n {len(empties)} block tr\u1ed1ng: {', '.join(map(str, empties[:20]))}", "#f87171")
            return
        self._confirmed = True
        self._release_video_preview()
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self._clear_parent_editor_reference()
            self.destroy()
        finally:
            if self.on_confirm:
                self.on_confirm()

    def _on_cancel(self):
        self._confirmed = False
        self._release_video_preview()
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self._clear_parent_editor_reference()
            self.destroy()
        finally:
            if self.on_cancel:
                self.on_cancel()

    def _on_close_requested(self):
        # Keep the editing session alive; the main button can restore it.
        self._hide_editor()




