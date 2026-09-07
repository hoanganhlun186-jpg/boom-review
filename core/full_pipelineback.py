"""
AutoRecapPro V2 - Full 11-Step Pipeline
========================================
Thứ tự pipeline hoàn chỉnh:

  1.  METADATA       - Phân tích thông tin định dạng video
  2.  TRANSCRIPT     - Trích xuất lời thoại gốc (Subtitle/SRT)
  3.  SCENE_DETECT   - Nhận diện các phân cảnh phim (non-fatal, có thể skip)
  4.  SUBTITLE_MAP   - Khớp lời thoại gốc vào từng phân cảnh
  5.  KEYFRAMES      - Trích xuất ảnh tiêu biểu (non-fatal, có thể skip)
  6.  AI_FULL        - Xây story chapter và viết narration toàn phim
  7.  CLIP_FIND      - Chọn/cắt cảnh theo story và visual anchor
  8.  VOICE_SEGMENTS - Tạo giọng thuyết minh AI từng đoạn (resume-safe)
  9.  VOICE_CONCAT   - Ghép nối giọng đọc thành track tổng hợp + loudnorm
  10. VOICE_SRT      - Đồng bộ phụ đề giọng đọc thuyết minh
  11. RENDER_FINAL   - Ghép video băm + voice track thành video cuối (tắt tiếng gốc)

KEY DESIGN:
  - AI_FULL viết story chapter trước; CLIP_FIND chỉ cắt cảnh sau khi có narration
  - SRT gốc được map theo original_start/original_end (timestamp video GỐC)
  - Voice placement dùng start_in_final_video (timestamp video BĂM)
  - Resume-safe: mỗi bước kiểm tra output file đã có chưa trước khi chạy lại
"""

import os
import json
import hashlib
import shutil
import subprocess
import tempfile
import asyncio
import re
import unicodedata
import math
import time
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from utils.helpers import FFmpegUtils

try:
    from core.review_styles import normalize_review_style
except Exception:
    def normalize_review_style(style=None):
        return "professional_youtube_movie_recap"

# ─────────────────────────────────────────────────────────────────
# Progress helpers
# ─────────────────────────────────────────────────────────────────

STEP_NAMES = [
    "METADATA",
    "TRANSCRIPT",
    "SCENE_DETECT",
    "SUBTITLE_MAP",
    "KEYFRAMES",
    "AI_FULL",
    "CLIP_FIND",
    "VOICE_SEGMENTS",
    "VOICE_CONCAT",
    "VOICE_SRT",
    "RENDER_FINAL",
]

STATUS_ICONS = {"pending": "⏳", "running": "▶️ ", "done": "✅", "failed": "❌", "skipped": "⏭️ "}


class PipelineStep:
    def __init__(self, name: str):
        self.name = name
        self.status = "pending"
        self.result: Any = None
        self.error: str = ""
        self.duration_s: float = 0.0

    def start(self):
        import time
        self._start = time.time()
        self.status = "running"

    def complete(self, result=None):
        import time
        self.duration_s = time.time() - getattr(self, "_start", 0)
        self.status = "done"
        self.result = result

    def fail(self, error: str):
        import time
        self.duration_s = time.time() - getattr(self, "_start", 0)
        self.status = "failed"
        self.error = error

    def skip(self, reason: str = ""):
        self.status = "skipped"
        self.error = reason

    def __str__(self):
        icon = STATUS_ICONS.get(self.status, "?")
        dur = f" ({self.duration_s:.1f}s)" if self.duration_s else ""
        extra = f" - {self.error}" if self.status in ("failed", "skipped") and self.error else ""
        return f"{icon} {self.name}{dur}{extra}"


# ─────────────────────────────────────────────────────────────────
# Main pipeline class
# ─────────────────────────────────────────────────────────────────

class FullPipeline:
    """
    Chạy toàn bộ 10 bước pipeline tự động.

    Usage:
        pipeline = FullPipeline(
            video_path="input.mp4",
            output_dir="output/",
            movie_title="Tên phim",
            voice="vi-VN-HoaiMyNeural",
            progress_callback=lambda msg: print(msg),
        )
        success = pipeline.run()
        print(pipeline.summary())
    """

    def __init__(
        self,
        video_path: str,
        output_dir: str,
        movie_title: str = "",
        movie_description: str = "",
        voice: str = "vi-VN-HoaiMyNeural",
        keep_seconds: int = 4,
        skip_seconds: int = 8,
        max_video_minutes: Optional[float] = 20.0,
        smart_cut: bool = True,
        gemini_api_key: str = "",
        source_srt_path: str = "",
        bg_music_path: str = "",
        header_text: str = "",
        footer_text: str = "",
        header_color: tuple = (255, 255, 0),
        footer_color: tuple = (255, 255, 255),
        header_bar_color: tuple = (255, 0, 0),
        footer_bar_color: tuple = (0, 174, 255),
        header_font_size: int = 80,
        footer_font_size: int = 60,
        header_pos=None,
        footer_pos=None,
        review_style: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
        step_callback: Optional[Callable[[str, str], None]] = None,
        script_review_callback: Optional[Callable[["FullPipeline"], bool]] = None,
        preview_design=None,
    ):
        self.preview_design = dict(preview_design or {})
        self.video_path = video_path
        self.output_dir = output_dir
        self.movie_title = movie_title
        self.movie_description = movie_description
        self.voice = voice
        self.keep_seconds = keep_seconds
        self.skip_seconds = skip_seconds
        try:
            _max_minutes = float(max_video_minutes or 20.0)
        except Exception:
            _max_minutes = 20.0
        if not math.isfinite(_max_minutes) or _max_minutes <= 0:
            _max_minutes = 20.0
        # Preset và số phút người dùng tự nhập đều dùng chung ngân sách chapter.
        # Chỉ chặn giá trị bất thường; không ép ngược về các mốc 5/10/15/20 cũ.
        _max_minutes = min(600.0, max(1.0, _max_minutes))
        self.max_video_minutes = _max_minutes
        self.smart_cut = smart_cut
        self.gemini_api_key = gemini_api_key
        self.source_srt_path = source_srt_path
        self.bg_music_path = bg_music_path
        self.header_text = header_text
        self.footer_text = footer_text
        self.header_color = header_color
        self.footer_color = footer_color
        self.header_bar_color = header_bar_color
        self.footer_bar_color = footer_bar_color
        self.header_font_size = header_font_size
        self.footer_font_size = footer_font_size
        self.header_pos = header_pos
        self.footer_pos = footer_pos
        self.review_style = normalize_review_style(review_style or os.environ.get("AUTORECAP_REVIEW_STYLE"))
        os.environ["AUTORECAP_REVIEW_STYLE"] = self.review_style
        # Callback để hiện Script Editor sau AI_FULL.
        # Nhận pipeline làm tham số, trả về True = tiếp tục, False = huỷ.
        self.script_review_callback = script_review_callback
        self.progress_callback = progress_callback or (lambda msg: print(msg))
        self.step_callback = step_callback  # called as step_callback(step_name, status)
        self._verbose_logs = str(
            os.environ.get("AUTORECAP_VERBOSE_LOGS", "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._debug_log_path = os.path.join(output_dir, "pipeline_debug.log")

        # Intermediate outputs
        self.metadata: Dict = {}
        self.transcript_srt: str = ""
        self.scenes: List[Dict] = []
        self.subtitle_map: List[Dict] = []
        self.keyframes: List[str] = []
        self.scene_cards: List[Dict] = []
        self.story_outline: Dict[str, Any] = {}
        self.ai_package: Dict = {}
        self.cut_video_path: str = ""
        self.cut_duration: float = 0.0
        self.render_blocks: List[Dict] = []
        self.voice_segments: List = []
        self.concat_audio_path: str = ""
        self.voice_srt_path: str = ""
        self.final_video_path: str = ""

        # Optional: callback (deprecated — không còn dùng CapCut cho video băm)
        # Giữ lại để tránh lỗi nếu code khác vẫn set thuộc tính này
        self._capcut_srt_callback: Optional[Callable] = None

        # Step tracking
        self.steps: Dict[str, PipelineStep] = {
            name: PipelineStep(name) for name in STEP_NAMES
        }

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        try:
            from core.pipeline_state import PipelineStateManager
            self.pipeline_state = PipelineStateManager(output_dir)
        except Exception:
            self.pipeline_state = None

    def _market_strict(self) -> bool:
        value = str(os.environ.get("AUTORECAP_MARKET_STRICT", "1") or "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _write_market_readiness_report(self, report: Dict[str, Any]) -> None:
        try:
            with open(os.path.join(self.output_dir, "market_readiness_report.json"), "w", encoding="utf-8") as f:
                json.dump(report or {}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _write_srt_alignment_report(self, report: Dict[str, Any]) -> None:
        try:
            with open(os.path.join(self.output_dir, "srt_alignment_report.json"), "w", encoding="utf-8") as f:
                json.dump(report or {}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _write_srt_triangulation_report(self, report: Dict[str, Any]) -> None:
        try:
            with open(os.path.join(self.output_dir, "srt_triangulation_report.json"), "w", encoding="utf-8") as f:
                json.dump(report or {}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _annotate_srt_alignment(
        self,
        package: Dict[str, Any],
        render_blocks: List[Dict[str, Any]],
        voice_srt_path: str = "",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        from core.srt_alignment import SrtAlignmentValidator

        voice_subtitles = []
        if voice_srt_path and os.path.exists(voice_srt_path):
            try:
                from core.srt_processor import SRTParser
                voice_subtitles = SRTParser.parse_srt(voice_srt_path)
            except Exception:
                voice_subtitles = []
        package, report = SrtAlignmentValidator.annotate_package(
            package,
            render_blocks,
            voice_subtitles=voice_subtitles,
        )
        self._write_srt_alignment_report(report)
        self._log(
            "   [SRT_ALIGNMENT] "
            f"{'PASS' if report.get('ready') else 'FAIL'} | "
            f"errors {report.get('error_count', 0)} | "
            f"weak {report.get('weak_block_count', 0)}/{report.get('block_count', 0)}"
        )
        return package, report

    def _validate_voice_srt_alignment(self, voice_srt_path: str) -> bool:
        try:
            from core.preview_design import read_cues
            from core.voice_caption_validation import validate_captions
            default_mode = '0' if self.ai_package.get('voice_concat_continuous') else '1'
            render_timeline = str(os.environ.get('AUTORECAP_VOICE_SRT_RENDER_TIMELINE',default_mode)).lower() not in {'0','false','no','off'}
            errors = validate_captions(read_cues(voice_srt_path),self.voice_segments,render_timeline)
            if errors:
                self._log('   [VOICE_CAPTIONS] FAIL: '+'; '.join(errors))
                return False
            self._log('   [VOICE_CAPTIONS] PASS: nội dung và thời gian khớp voice đã tạo')
            self.ai_package, _ = self._annotate_srt_alignment(
                self.ai_package,
                self.render_blocks,
                voice_srt_path=voice_srt_path,
            )
            pkg_file = os.path.join(self.output_dir, "ai_package.json")
            with open(pkg_file, "w", encoding="utf-8") as f:
                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            self.ai_package, market_report = self._annotate_market_readiness(
                self.ai_package,
                self.render_blocks,
                self.ai_package.get("context_coverage_report"),
                self.ai_package.get("visual_scene_evidence_report"),
            )
            with open(pkg_file, "w", encoding="utf-8") as f:
                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            if not market_report.get('ready'):
                self._log('   ⚠️ Chất lượng lời kể/cảnh còn cảnh báo trong market_readiness_report.json; phụ đề đã khớp audio. Sửa lời kể cần tạo lại voice, không sửa riêng SRT.')
            return True
        except Exception as e:
            self._log(f"   [SRT_ALIGNMENT] Không kiểm định được voice SRT: {e}")
            return False

    def _ensure_cut_keyframes(self) -> None:
        """Create thumbnails for Script Editor.

        RECAP2 mode keeps render_blocks on the ORIGINAL movie timeline while
        cut_video.mp4 is only a compact preview. In that mode thumbnails must
        come from the source video, otherwise the editor shows the wrong frame.
        """
        try:
            if not self.cut_video_path or not os.path.exists(self.cut_video_path) or not self.render_blocks:
                return
            recap2_mode = self._recap2_beat_mode_enabled()
            frame_source = self.video_path if recap2_mode and self.video_path and os.path.exists(self.video_path) else self.cut_video_path
            thumb_blocks = (
                self.ai_package.get("script_blocks")
                if recap2_mode and isinstance(getattr(self, "ai_package", None), dict) and self.ai_package.get("script_blocks")
                else self.render_blocks
            )
            frame_source_label = "source_video_script_block" if recap2_mode and thumb_blocks is not self.render_blocks else ("source_video" if frame_source == self.video_path else "cut_video")
            out_dir = os.path.join(self.output_dir, "cut_keyframes")
            legacy_dir = os.path.join(self.output_dir, "cut_frames")
            os.makedirs(out_dir, exist_ok=True)
            os.makedirs(legacy_dir, exist_ok=True)
            manifest_path = os.path.join(out_dir, "keyframes_manifest.json")
            legacy_manifest_path = os.path.join(legacy_dir, "keyframes_manifest.json")
            existing = [
                name for name in os.listdir(out_dir)
                if name.lower().endswith((".jpg", ".jpeg", ".png"))
            ]
            if existing and os.path.exists(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as _mf:
                        _manifest = json.load(_mf)
                    if recap2_mode and (
                        len(_manifest or []) != len(thumb_blocks or [])
                        or any(
                            isinstance(item, dict) and item.get("source") != "source_video_script_block"
                            for item in (_manifest or [])
                        )
                    ):
                        existing = []
                except Exception:
                    if recap2_mode:
                        existing = []
            if existing and os.path.exists(manifest_path):
                if not any(name.lower().endswith((".jpg", ".jpeg", ".png")) for name in os.listdir(legacy_dir)):
                    try:
                        import shutil as _sh
                        for name in existing:
                            src = os.path.join(out_dir, name)
                            dst = os.path.join(legacy_dir, name)
                            if os.path.exists(src) and not os.path.exists(dst):
                                _sh.copy2(src, dst)
                        if os.path.exists(manifest_path) and not os.path.exists(legacy_manifest_path):
                            _sh.copy2(manifest_path, legacy_manifest_path)
                    except Exception:
                        pass
                return

            import cv2
            try:
                from utils.helpers import FFmpegUtils
                ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
            except Exception:
                import shutil as _sh
                ffmpeg_bin = _sh.which("ffmpeg") or "ffmpeg"

            def _write_with_ffmpeg(ts: float, path: str) -> bool:
                try:
                    r = subprocess.run(
                        [
                            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{max(0.0, float(ts or 0.0)):.3f}",
                            "-i", frame_source,
                            "-frames:v", "1", "-q:v", "2", path,
                        ],
                        **FFmpegUtils.subprocess_kwargs(
                            capture_output=True,
                            text=True,
                            timeout=25,
                        ),
                    )
                    return r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0
                except Exception:
                    return False

            cap = cv2.VideoCapture(frame_source)
            cv2_ok = bool(cap.isOpened())
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            manifest = []
            made = 0
            for idx, block in enumerate(thumb_blocks or [], 1):
                try:
                    bid = int(block.get("block_id") or block.get("book_id") or idx)
                except Exception:
                    bid = idx
                if recap2_mode:
                    start_t = float(
                        block.get("original_start")
                        if block.get("original_start") is not None
                        else block.get("source_start")
                        if block.get("source_start") is not None
                        else block.get("start_s")
                        if block.get("start_s") is not None
                        else block.get("start_in_final_video")
                        or block.get("start")
                        or 0.0
                    )
                    end_t = float(
                        block.get("original_end")
                        if block.get("original_end") is not None
                        else block.get("source_end")
                        if block.get("source_end") is not None
                        else block.get("end_s")
                        if block.get("end_s") is not None
                        else block.get("end_in_final_video")
                        or (start_t + float(block.get("duration") or 0.0))
                    )
                else:
                    start_t = float(block.get("start_in_final_video") or block.get("start") or 0.0)
                    end_t = float(block.get("end_in_final_video") or (start_t + float(block.get("duration") or 0.0)))
                ts = max(0.0, start_t + max(0.0, end_t - start_t) * 0.15)
                path = os.path.join(out_dir, f"block_{bid:04d}.jpg")
                alias = os.path.join(out_dir, f"scene_{bid:04d}.jpg")
                wrote = False
                if cv2_ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(ts * fps))
                    ret, frame = cap.read()
                    if ret:
                        wrote = bool(cv2.imwrite(path, frame))
                if not wrote:
                    wrote = _write_with_ffmpeg(ts, path)
                if not wrote:
                    continue
                try:
                    import shutil as _sh
                    _sh.copy2(path, alias)
                except Exception:
                    pass
                legacy_path = os.path.join(legacy_dir, f"block_{bid:04d}.jpg")
                legacy_alias = os.path.join(legacy_dir, f"scene_{bid:04d}.jpg")
                try:
                    import shutil as _sh
                    if not os.path.exists(legacy_path):
                        _sh.copy2(path, legacy_path)
                    if os.path.exists(alias) and not os.path.exists(legacy_alias):
                        _sh.copy2(alias, legacy_alias)
                except Exception:
                    pass
                made += 1
                manifest.append({
                    "block_id": bid,
                    "scene_id": bid,
                    "path": path,
                    "scene_alias": alias,
                    "legacy_path": legacy_path,
                    "legacy_scene_alias": legacy_alias,
                    "time_s": round(ts, 3),
                    "start_s": round(start_t, 3),
                    "end_s": round(end_t, 3),
                    "source": frame_source_label,
                })
            if cv2_ok:
                cap.release()
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            with open(legacy_manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            if made:
                self._log(f"   🖼️ Cut keyframes: {made} ảnh → {out_dir}")
            else:
                self._log("   ⚠️ Cut keyframes: không trích được ảnh từ cut_video.mp4")
        except Exception as exc:
            self._log(f"   ⚠️ Cut keyframes lỗi: {exc}")

    @staticmethod
    def _market_fail_message(report: Dict[str, Any]) -> str:
        issues = [
            item for item in (report.get("issues") or [])
            if isinstance(item, dict) and item.get("level") == "ERROR"
        ]
        if not issues:
            issues = [item for item in (report.get("issues") or []) if isinstance(item, dict)]
        labels = [str(item.get("type") or item.get("message") or "unknown") for item in issues[:4]]
        suffix = ", ".join(labels) if labels else str(report.get("status") or "failed")
        return f"Market readiness FAIL: {suffix}"

    def _annotate_market_readiness(
        self,
        package: Dict[str, Any],
        render_blocks: List[Dict[str, Any]],
        context_coverage_report: Optional[Dict[str, Any]] = None,
        visual_evidence_report: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        from core.market_readiness import MarketReadinessValidator

        package, report = MarketReadinessValidator.annotate_package(
            package,
            render_blocks,
            context_coverage_report=context_coverage_report,
            visual_evidence_report=visual_evidence_report,
            strict=self._market_strict(),
        )
        self._write_market_readiness_report(report)
        status = str(report.get("status") or "unknown").upper()
        metrics = report.get("metrics") or {}
        self._log(
            "   🧪 Market readiness: "
            f"{status} | evidence {float(metrics.get('evidence_coverage_ratio', 0.0) or 0.0):.2f} | "
            f"grounding weak {metrics.get('grounding_weak_block_count', 0)} | "
            f"mapping issues {metrics.get('mapping_issue_count', 0)}"
        )
        for issue in (report.get("issues") or [])[:6]:
            if isinstance(issue, dict):
                self._log(f"      - {issue.get('level', 'WARN')}: {issue.get('type')} - {issue.get('message')}")
        return package, report

    def _write_evidence_gate_report(self, report: Dict[str, Any]) -> None:
        try:
            with open(os.path.join(self.output_dir, "evidence_gate_report.json"), "w", encoding="utf-8") as f:
                json.dump(report or {}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _evidence_gate_enabled(self) -> bool:
        value = str(os.environ.get("AUTORECAP_EVIDENCE_GATE", "1") or "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    @staticmethod
    def _evidence_gate_issue(level: str, issue_type: str, message: str, **metrics: Any) -> Dict[str, Any]:
        item: Dict[str, Any] = {"level": level, "type": issue_type, "message": message}
        if metrics:
            item["metrics"] = metrics
        return item

    def _build_evidence_gate_report(
        self,
        context_coverage_report: Dict[str, Any],
        visual_evidence_report: Optional[Dict[str, Any]],
        source_subtitles: List[Dict[str, Any]],
        render_blocks: List[Dict[str, Any]],
        target_duration: float,
    ) -> Dict[str, Any]:
        context_coverage_report = context_coverage_report or {}
        visual_evidence_report = visual_evidence_report or {}
        book_count = int(context_coverage_report.get("book_count", len(render_blocks or [])) or 0)
        subtitle_count = len(source_subtitles or [])
        duration_minutes = max(0.1, float(target_duration or self.metadata.get("duration_s") or 0.0) / 60.0)
        source_duration_minutes = max(0.1, float(self.metadata.get("duration_s") or target_duration or 0.0) / 60.0)
        srt_lines_per_minute = round(subtitle_count / source_duration_minutes, 2)
        evidence_ratio = float(context_coverage_report.get("evidence_coverage_ratio", 0.0) or 0.0)
        srt_ratio = float(context_coverage_report.get("srt_coverage_ratio", 0.0) or 0.0)
        visual_ratio = float(context_coverage_report.get("visual_coverage_ratio", 0.0) or 0.0)
        weak_count = int(context_coverage_report.get("weak_context_count", 0) or 0)
        visual_targets = int(visual_evidence_report.get("target_count", 0) or 0)
        visual_items = int(visual_evidence_report.get("item_count", 0) or 0)
        visual_success_ratio = round(visual_items / max(1, visual_targets), 3) if visual_targets else 0.0
        min_evidence = float(os.environ.get("AUTORECAP_MIN_EVIDENCE_COVERAGE", "0.35") or "0.35")
        warn_evidence = float(os.environ.get("AUTORECAP_WARN_EVIDENCE_COVERAGE", "0.55") or "0.55")
        min_srt_lines = max(80, int(source_duration_minutes * 2.0))
        issues: List[Dict[str, Any]] = []

        if subtitle_count < min_srt_lines and visual_ratio < 0.35:
            issues.append(
                self._evidence_gate_issue(
                    "ERROR",
                    "source_srt_too_sparse",
                    "SRT nguồn quá ít dòng so với thời lượng phim; AI không đủ thoại thật để bám nội dung.",
                    subtitle_count=subtitle_count,
                    expected_min_subtitles=min_srt_lines,
                    source_duration_minutes=round(source_duration_minutes, 1),
                    srt_lines_per_minute=srt_lines_per_minute,
                )
            )
        if book_count and evidence_ratio < min_evidence:
            issues.append(
                self._evidence_gate_issue(
                    "ERROR",
                    "ai_input_evidence_too_low",
                    "Quá ít book có SRT/visual evidence thật; không gọi AI để tránh sinh kịch bản đoán mò.",
                    evidence_coverage_ratio=round(evidence_ratio, 3),
                    weak_context_count=weak_count,
                    book_count=book_count,
                )
            )
        elif book_count and evidence_ratio < warn_evidence:
            issues.append(
                self._evidence_gate_issue(
                    "WARNING",
                    "ai_input_evidence_low",
                    "Evidence hơi yếu; kịch bản có thể cần kiểm tra thủ công các cảnh quan trọng.",
                    evidence_coverage_ratio=round(evidence_ratio, 3),
                    weak_context_count=weak_count,
                    book_count=book_count,
                )
            )
        if visual_targets and visual_success_ratio < 0.35 and evidence_ratio < warn_evidence:
            issues.append(
                self._evidence_gate_issue(
                    "ERROR",
                    "visual_rescue_insufficient",
                    "Gemini Vision không cứu đủ block thiếu ngữ cảnh.",
                    visual_items=visual_items,
                    visual_targets=visual_targets,
                    visual_success_ratio=visual_success_ratio,
                )
            )

        errors = [item for item in issues if item.get("level") == "ERROR"]
        ready = not errors
        report = {
            "version": "evidence_gate_v1",
            "ready": ready,
            "status": "pass" if ready and not issues else ("warn" if ready else "fail"),
            "error_count": len(errors),
            "warning_count": len([item for item in issues if item.get("level") == "WARNING"]),
            "issues": issues,
            "metrics": {
                "book_count": book_count,
                "subtitle_count": subtitle_count,
                "duration_minutes": round(duration_minutes, 1),
                "source_duration_minutes": round(source_duration_minutes, 1),
                "srt_lines_per_minute": srt_lines_per_minute,
                "srt_coverage_ratio": round(srt_ratio, 3),
                "visual_coverage_ratio": round(visual_ratio, 3),
                "evidence_coverage_ratio": round(evidence_ratio, 3),
                "weak_context_count": weak_count,
                "visual_items": visual_items,
                "visual_targets": visual_targets,
                "visual_success_ratio": visual_success_ratio,
            },
            "recommendations": [
                "Tạo lại SRT nguồn từ video gốc đầy đủ, không dùng voice_subtitles hoặc SRT chỉ có vài dòng.",
                "Nếu phim không có SRT đủ, bật/cấp quota Gemini Vision hoặc OpenRouter Vision để mô tả keyframe cho nhiều book hơn.",
                "Chạy lại từ TRANSCRIPT/SUBTITLE_MAP/AI_FULL/CLIP_FIND sau khi thay SRT nguồn.",
            ],
        }
        return report

    def _enforce_evidence_gate(
        self,
        context_coverage_report: Dict[str, Any],
        visual_evidence_report: Optional[Dict[str, Any]],
        source_subtitles: List[Dict[str, Any]],
        render_blocks: List[Dict[str, Any]],
        target_duration: float,
    ) -> Dict[str, Any]:
        report = self._build_evidence_gate_report(
            context_coverage_report,
            visual_evidence_report,
            source_subtitles,
            render_blocks,
            target_duration,
        )
        self._write_evidence_gate_report(report)
        metrics = report.get("metrics") or {}
        self._log(
            "   🧱 Evidence gate: "
            f"{str(report.get('status') or 'unknown').upper()} | "
            f"SRT {metrics.get('subtitle_count', 0)} dòng ({metrics.get('srt_lines_per_minute', 0)}/phút) | "
            f"evidence {float(metrics.get('evidence_coverage_ratio', 0.0) or 0.0):.2f} | "
            f"weak {metrics.get('weak_context_count', 0)}/{metrics.get('book_count', 0)} | "
            f"vision {metrics.get('visual_items', 0)}/{metrics.get('visual_targets', 0)}"
        )
        for issue in (report.get("issues") or [])[:6]:
            if isinstance(issue, dict):
                self._log(f"      - {issue.get('level', 'WARN')}: {issue.get('type')} - {issue.get('message')}")
        if self._market_strict() and self._evidence_gate_enabled() and not report.get("ready"):
            labels = [
                str(item.get("type") or item.get("message") or "unknown")
                for item in (report.get("issues") or [])
                if isinstance(item, dict) and item.get("level") == "ERROR"
            ][:4]
            raise RuntimeError(f"Evidence gate FAIL: {', '.join(labels)}")
        return report

    def _max_ai_blocks(self, target_duration: float = 0.0) -> int:
        target_duration = float(target_duration or self.cut_duration or 0.0)
        if target_duration <= 0:
            return 80
        # Giữ beat dài hơn để recap liền mạch, tránh chia quá nhỏ làm hụt voice.
        return max(30, min(80, int(target_duration / 12.0) + 2))

    def _recap2_beat_mode_enabled(self) -> bool:
        """Use RECAP2-style beat blocks instead of tiny scene-by-scene script blocks."""
        return str(os.environ.get("AUTORECAP_RECAP2_BEAT_MODE", "1") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def _target_recap2_beat_seconds(self, target_duration: float = 0.0) -> float:
        """Target spoken beat duration.
        
        ClipAssembler mode: block nhỏ hơn (~12-15s) vì mỗi block có clip riêng
        → cover chi tiết hơn, nhiều block = nhiều cảnh đắt giá.
        Scene-pinned mode: block lớn hơn (~22s) để tránh atempo quá nhiều.
        """
        try:
            configured = float(os.environ.get("AUTORECAP_RECAP2_BEAT_SECONDS", "0") or "0")
        except Exception:
            configured = 0.0

        # Nếu user chưa set → dùng default theo mode
        if configured <= 0:
            # ClipAssembler: dùng video gốc, không atempo → block nhỏ hơn OK
            configured = 12.0

        configured = max(8.0, min(30.0, configured))
        target_duration = float(target_duration or self.cut_duration or 0.0)
        if target_duration <= 0:
            return configured

        # Tính số blocks phù hợp: ClipAssembler cho phép nhiều blocks hơn
        if self._recap2_beat_mode_enabled():
            # ClipAssembler: không cap cứng — để tự nhiên theo duration/beat
            # RECAP2.0 không set max cứng, để AI tự chia theo budget
            max_blocks = max(30, int(target_duration / configured) + 2)
            max_blocks = min(120, max_blocks)  # chỉ giới hạn ở 120 để tránh quá tải API
        else:
            max_blocks = max(18, min(80, int(target_duration / configured) + 1))

        by_duration = target_duration / max_blocks
        return max(8.0, min(30.0, max(configured * 0.75, by_duration)))

    def _render_blocks_timeline_end(self, render_blocks: list) -> float:
        end = 0.0
        for block in render_blocks or []:
            if not isinstance(block, dict):
                continue
            start = float(block.get("start_in_final_video") or 0.0)
            duration = float(block.get("duration") or 0.0)
            value = float(block.get("end_in_final_video") or (start + duration) or 0.0)
            end = max(end, value)
        return end

    def _render_blocks_source_end(self, render_blocks: list) -> float:
        """Return the farthest original/source timestamp covered by render blocks."""
        end = 0.0
        for block in render_blocks or []:
            if not isinstance(block, dict):
                continue
            try:
                source_start = float(
                    block.get("original_start")
                    if block.get("original_start") is not None
                    else block.get("source_start")
                    if block.get("source_start") is not None
                    else block.get("start_s")
                    if block.get("start_s") is not None
                    else block.get("start_in_final_video")
                    or 0.0
                )
                source_end = float(
                    block.get("original_end")
                    if block.get("original_end") is not None
                    else block.get("source_end")
                    if block.get("source_end") is not None
                    else block.get("end_s")
                    if block.get("end_s") is not None
                    else 0.0
                )
                if source_end <= source_start:
                    source_end = source_start + float(block.get("duration") or 0.0)
                end = max(end, source_end)
            except Exception:
                continue
        return end

    def _source_range_for_block(self, block: dict, fallback: dict | None = None) -> tuple[float, float]:
        """Return original/source timestamp range for a story block.

        In RECAP2 flow the script block is authoritative. The fallback render
        block is only used when the script block does not yet carry timestamps.
        """
        fallback = fallback or {}

        def _pick(*values):
            for value in values:
                if value is not None and value != "":
                    return value
            return None

        start_raw = _pick(
            block.get("original_start"),
            block.get("source_start"),
            block.get("start_s"),
            block.get("review_clip", {}).get("start") if isinstance(block.get("review_clip"), dict) else None,
            fallback.get("original_start"),
            fallback.get("source_start"),
            fallback.get("start_s"),
            fallback.get("start_in_final_video"),
            block.get("start_in_final_video"),
            block.get("start"),
        )
        end_raw = _pick(
            block.get("original_end"),
            block.get("source_end"),
            block.get("end_s"),
            block.get("review_clip", {}).get("end") if isinstance(block.get("review_clip"), dict) else None,
            fallback.get("original_end"),
            fallback.get("source_end"),
            fallback.get("end_s"),
            fallback.get("end_in_final_video"),
            block.get("end_in_final_video"),
            block.get("end"),
        )
        try:
            start = float(start_raw or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(end_raw or 0.0)
        except Exception:
            end = 0.0
        if end <= start:
            try:
                dur = float(
                    block.get("duration_hint_seconds")
                    or block.get("duration_seconds")
                    or block.get("duration")
                    or fallback.get("duration")
                    or 0.0
                )
            except Exception:
                dur = 0.0
            end = start + max(0.0, dur)
        return max(0.0, start), max(0.0, end)

    def _review_clip_for_block(
        self,
        block: dict,
        fallback: dict | None = None,
        idx: int = 0,
        scene_cards: list | None = None,
    ) -> dict:
        """Build the RECAP2-style clip metadata stored inside each script block."""
        fallback = fallback or {}
        start, end = self._source_range_for_block(block, fallback)
        source_duration = float((self.metadata or {}).get("duration_s") or 0.0)
        if source_duration > 0:
            content_end = max(0.5, source_duration - 8.0)
            requested_duration = max(0.5, end - start)
            end = min(end, content_end)
            if start >= end:
                start = max(0.0, end - min(requested_duration, 20.0))
        scene_ids = (
            block.get("source_block_ids")
            or block.get("scene_ids")
            or fallback.get("source_block_ids")
            or fallback.get("scene_ids")
            or []
        )
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
        lookahead_reason = "semantic_story"
        action_lookahead = False
        if scene_cards:
            try:
                from core.semantic_clip_finder import SemanticClipFinder

                extended = SemanticClipFinder.extend_story_clip_with_action_lookahead(
                    scene_cards,
                    start,
                    end,
                    clean_scene_ids,
                )
                end = float(extended.get("end") or end)
                clean_scene_ids = list(extended.get("scene_ids") or clean_scene_ids)
                lookahead_reason = str(extended.get("reason") or lookahead_reason)
                action_lookahead = bool(extended.get("extended"))
            except Exception:
                pass
        if source_duration > 0:
            end = min(end, max(0.5, source_duration - 0.1))
            start = max(0.0, min(start, end - 0.1))
        bid = 0
        try:
            bid = int(block.get("block_id") or block.get("book_id") or fallback.get("block_id") or idx or 0)
        except Exception:
            bid = int(idx or 0)
        thumbnail = ""
        try:
            if self.output_dir and bid:
                thumbnail = os.path.join(self.output_dir, "cut_keyframes", f"block_{bid:04d}.jpg")
        except Exception:
            thumbnail = ""
        return {
            "source_video": self.video_path,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "scene_ids": clean_scene_ids,
            "source_block_ids": clean_scene_ids,
            "thumbnail": thumbnail,
            "mode": "source_video_timestamp",
            "mapping_reason": lookahead_reason,
            "action_lookahead": action_lookahead,
        }

    def _attach_review_clips_to_package(self, package: dict, render_blocks: list) -> dict:
        """Embed review_clip into every script block so editor/render share one source."""
        package = dict(package or {})
        blocks = [dict(b) for b in (package.get("script_blocks") or []) if isinstance(b, dict)]
        if not blocks:
            return package
        rb_map = {}
        for idx, rb in enumerate(render_blocks or [], 1):
            if not isinstance(rb, dict):
                continue
            for key in (rb.get("block_id"), rb.get("book_id"), idx):
                try:
                    rb_map[int(key)] = rb
                except Exception:
                    pass
        try:
            scene_cards = self._ensure_scene_cards()
        except Exception:
            scene_cards = []
        enriched = []
        for idx, block in enumerate(blocks, 1):
            if block.get("review_clip_tail_extended") and block.get("review_clip_tail_previous_end") is not None:
                try:
                    previous_end = float(block.get("review_clip_tail_previous_end"))
                    old_clip = dict(block.get("review_clip") or {})
                    old_start = float(old_clip.get("start") or block.get("original_start") or 0.0)
                    old_clip["end"] = round(previous_end, 3)
                    old_clip["duration"] = round(max(0.0, previous_end - old_start), 3)
                    block["review_clip"] = old_clip
                    block["original_end"] = round(previous_end, 3)
                    block["source_end"] = round(previous_end, 3)
                    block["duration_hint_seconds"] = old_clip["duration"]
                    block.pop("review_clip_tail_extended", None)
                    block["legacy_tail_extension_removed"] = True
                except Exception:
                    pass
            try:
                bid = int(block.get("block_id") or block.get("book_id") or idx)
            except Exception:
                bid = idx
            fallback = rb_map.get(bid) or {}
            clip = self._review_clip_for_block(
                block,
                fallback,
                idx=bid,
                scene_cards=scene_cards,
            )
            block["review_clip"] = clip
            block["review_clip_source"] = "script_block"
            block["source_video"] = clip.get("source_video")
            block["original_start"] = clip.get("start")
            block["original_end"] = clip.get("end")
            block["source_start"] = clip.get("start")
            block["source_end"] = clip.get("end")
            if clip.get("scene_ids") and not block.get("scene_ids"):
                block["scene_ids"] = clip.get("scene_ids")
            if clip.get("source_block_ids") and not block.get("source_block_ids"):
                block["source_block_ids"] = clip.get("source_block_ids")
            if not block.get("duration_hint_seconds"):
                block["duration_hint_seconds"] = clip.get("duration")
            enriched.append(block)

        # Guard: script/editor blocks must cover the ending if render_blocks do.
        # This keeps Script Editor and ClipAssembler aware of the final scene.
        try:
            script_end = max(
                float((b.get("review_clip") or {}).get("end") or b.get("original_end") or 0.0)
                for b in enriched
                if isinstance(b, dict)
            )
        except Exception:
            script_end = 0.0
        render_end = self._render_blocks_source_end(render_blocks)
        # Never stretch the final narration block to the physical end of the
        # source. That used to pull studio logos/credits into the last block and
        # FFmpeg then appeared to hold one still image for minutes. Story
        # coverage must come from chapter/source ids, not a synthetic tail.
        allow_tail_extension = str(os.environ.get("AUTORECAP_EXTEND_FINAL_CLIP_TO_SOURCE", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if allow_tail_extension and enriched and render_end > script_end + 2.0:
            last = enriched[-1]
            clip = dict(last.get("review_clip") or {})
            old_start = float(clip.get("start") or last.get("original_start") or 0.0)
            old_end = float(clip.get("end") or last.get("original_end") or old_start)
            tail_scene_ids = []
            for rb in render_blocks or []:
                if not isinstance(rb, dict):
                    continue
                rs, re_ = self._source_range_for_block(rb)
                if re_ >= old_end - 0.25:
                    for sid in (rb.get("scene_ids") or rb.get("source_block_ids") or [rb.get("scene_id")]):
                        try:
                            sid_int = int(sid)
                            if sid_int not in tail_scene_ids:
                                tail_scene_ids.append(sid_int)
                        except Exception:
                            pass
            merged_ids = []
            for sid in (clip.get("scene_ids") or last.get("scene_ids") or []) + tail_scene_ids:
                try:
                    sid_int = int(sid)
                    if sid_int not in merged_ids:
                        merged_ids.append(sid_int)
                except Exception:
                    pass
            clip["end"] = round(render_end, 3)
            clip["duration"] = round(max(0.0, render_end - old_start), 3)
            clip["scene_ids"] = merged_ids
            clip["source_block_ids"] = merged_ids
            last["review_clip"] = clip
            last["original_end"] = clip["end"]
            last["source_end"] = clip["end"]
            last["duration_hint_seconds"] = clip["duration"]
            if merged_ids:
                last["scene_ids"] = merged_ids
                last["source_block_ids"] = merged_ids
            last["review_clip_tail_extended"] = True
            last["review_clip_tail_previous_end"] = round(old_end, 3)
        elif enriched and render_end > script_end + 2.0:
            package["source_tail_not_forced_into_last_block"] = True
            package["source_tail_seconds"] = round(render_end - script_end, 3)
        package["script_blocks"] = enriched
        package["review_clip_schema_version"] = 1
        package["script"] = "\n\n".join(
            str(block.get("text", "")).strip()
            for block in enriched
            if str(block.get("text", "")).strip()
        ).strip()
        package["subtitle_chunks"] = [block["text"] for block in enriched if block.get("text")]
        return package

    def _review_clip_preview_segments(self, max_duration_seconds: float | None = None) -> list:
        """Build cut_video preview segments directly from script_blocks.review_clip.

        cut_video is only an editor/preview artifact in RECAP2 mode. It should
        show the same source areas as the script blocks without expanding back
        into the full episode.
        """
        blocks = [
            block for block in (self.ai_package.get("script_blocks") or [])
            if isinstance(block, dict) and isinstance(block.get("review_clip"), dict)
        ]
        if not blocks:
            return []

        def _clip_range(block: dict) -> tuple[float, float]:
            clip = block.get("review_clip") or {}
            try:
                s = float(clip.get("start") if clip.get("start") is not None else block.get("original_start") or 0.0)
                e = float(clip.get("end") if clip.get("end") is not None else block.get("original_end") or 0.0)
            except Exception:
                return 0.0, 0.0
            return max(0.0, s), max(0.0, e)

        # Estimate natural preview duration from narration, then scale to target.
        raw = []
        for index, block in enumerate(blocks, 1):
            s, e = _clip_range(block)
            window = max(0.0, e - s)
            if window < 0.5:
                continue
            words = len(str(block.get("text") or "").split())
            estimated_voice = max(3.0, words / 2.75) if words else 6.0
            desired = min(window, max(3.0, min(16.0, estimated_voice)))
            raw.append((index, block, s, e, desired))

        if not raw:
            return []

        total_desired = sum(item[4] for item in raw)
        target = float(max_duration_seconds or 0.0)
        if target <= 0:
            target = total_desired
        scale = 1.0
        if total_desired > target > 0:
            scale = max(0.35, target / max(1.0, total_desired))

        segments = []
        prev_end = -999.0
        total = len(raw)
        for pos, (index, block, s, e, desired) in enumerate(raw, 1):
            window = max(0.0, e - s)
            dur = min(window, max(2.0, desired * scale))
            if target > 0:
                used = sum(max(0.0, seg["end"] - seg["start"]) for seg in segments)
                remaining = target - used
                remaining_blocks = max(1, total - pos + 1)
                if remaining <= 0:
                    break
                dur = min(dur, max(1.5, remaining / remaining_blocks * 1.35))
                dur = min(dur, remaining)

            if pos == 1:
                start = s
            elif pos == total:
                start = max(s, e - dur)
            else:
                start = s + max(0.0, window - dur) * 0.35
            # Keep preview segments distinct so the cutter does not merge the
            # whole chronological episode back together.
            if start <= prev_end + 0.25 and e - dur > prev_end + 0.25:
                start = min(e - dur, prev_end + 0.35)
            end = min(e, start + dur)
            if end - start < 0.5:
                continue
            try:
                bid = int(block.get("block_id") or block.get("book_id") or index)
            except Exception:
                bid = index
            clip = block.get("review_clip") or {}
            segments.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "score": float(block.get("smart_score") or block.get("cut_score") or 80),
                "reason": "review_clip_preview",
                "block_id": bid,
                "scene_ids": clip.get("scene_ids") or block.get("scene_ids") or [],
            })
            prev_end = end
        return segments

    def _build_render_blocks_from_full_scenes(self) -> list:
        """Build render blocks from the full source scene timeline, not recap length."""
        if not self.scenes:
            return []
        try:
            from core.calculator import VideoCalculator
            return VideoCalculator.get_render_blocks(
                [
                    {
                        "start": float(scene.get("start_s") or scene.get("start") or 0.0),
                        "end": float(scene.get("end_s") or scene.get("end") or 0.0),
                        "reason": "scene_cut",
                        "score": scene.get("cut_score", 50),
                        "scene_id": scene.get("scene_id"),
                        "scene_ids": [scene.get("scene_id")] if scene.get("scene_id") is not None else [],
                        "dialogue_text": scene.get("dialogue_text", ""),
                        "visual_anchor": scene.get("visual_anchor", ""),
                    }
                    for scene in self.scenes
                    if float(scene.get("end_s") or scene.get("end") or 0.0)
                    > float(scene.get("start_s") or scene.get("start") or 0.0)
                ]
            )
        except Exception as e:
            self._log(f"   Không rebuild được render_blocks toàn phim: {e}")
            return []

    def _ensure_render_blocks_cover_source(self, render_blocks: list, source_duration: float, label: str = "") -> list:
        """Ensure render blocks reach the end of the original episode timeline.

        RECAP2-style review can be shorter than the episode, but the story plan
        must still see the whole episode. This guard prevents old cached/pattern
        blocks from silently covering only the first part of the movie.
        """
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        try:
            source_duration = float(source_duration or 0.0)
        except Exception:
            source_duration = 0.0
        if source_duration <= 0 or not blocks:
            return blocks

        source_end = self._render_blocks_source_end(blocks)
        if source_end >= max(0.0, source_duration - 8.0):
            return blocks

        rebuilt = self._build_render_blocks_from_full_scenes()
        rebuilt_end = self._render_blocks_source_end(rebuilt)
        if rebuilt and rebuilt_end > source_end + 8.0:
            self._log(
                f"   🔁 Render blocks chưa phủ hết tập{(' (' + label + ')') if label else ''}: "
                f"{source_end:.0f}s/{source_duration:.0f}s -> rebuild toàn phim "
                f"({len(rebuilt)} blocks, tới {rebuilt_end:.0f}s)"
            )
            return rebuilt

        # Last-resort tail block: keep AI/assembler aware that the ending exists.
        tail = dict(blocks[-1])
        final_start = self._render_blocks_timeline_end(blocks)
        tail["block_id"] = len(blocks) + 1
        tail["book_id"] = len(blocks) + 1
        tail["start_in_final_video"] = round(final_start, 3)
        tail["duration"] = round(max(8.0, min(30.0, source_duration - source_end)), 3)
        tail["end_in_final_video"] = round(final_start + float(tail["duration"]), 3)
        tail["original_start"] = round(max(0.0, source_end), 3)
        tail["original_end"] = round(source_duration, 3)
        tail["cut_reason"] = "source_tail_repair"
        tail["visual_anchor"] = "Đoạn cuối tập phim cần được khép lại trong phần review."
        tail["srt_anchor"] = ""
        tail["scene_ids"] = []
        blocks.append(tail)
        self._log(
            f"   🔁 Thêm tail block để phủ hết tập: {source_end:.0f}s -> {source_duration:.0f}s"
        )
        return blocks

    def _needs_render_block_compaction(self, render_blocks: list, target_duration: float = 0.0) -> bool:
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return False
        target_duration = float(target_duration or self.cut_duration or 0.0)
        max_blocks = self._max_ai_blocks(target_duration)
        timeline_end = self._render_blocks_timeline_end(blocks)
        avg_duration = sum(float(block.get("duration") or 0.0) for block in blocks) / max(1, len(blocks))
        if self._recap2_beat_mode_enabled():
            target_beat = self._target_recap2_beat_seconds(target_duration)
            micro_count = sum(1 for block in blocks if float(block.get("duration") or 0.0) < target_beat * 0.60)
            return (
                len(blocks) > 1
                and (
                    micro_count > 0
                    or avg_duration < target_beat * 0.85
                    or len(blocks) > max(18, int((target_duration or timeline_end or 1.0) / target_beat) + 8)
                )
            )
        return (
            len(blocks) > max_blocks
            or avg_duration < 8.0
            or (target_duration > 0 and timeline_end > target_duration * 1.25 and len(blocks) > max_blocks)
        )

    def _compact_render_blocks_for_ai(self, render_blocks: list, target_duration: float = 0.0) -> list:
        """Group cut scenes into RECAP2-style AI/voice beats."""
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks or not self._needs_render_block_compaction(blocks, target_duration):
            return blocks
        try:
            from core.premium_pipeline import PremiumReviewPipeline
            subtitles = []
            if self.transcript_srt and os.path.exists(self.transcript_srt):
                try:
                    # Dùng PremiumReviewPipeline.parse_srt_content thay vì SRTParser
                    # (core/srt_processor.py không tồn tại → ImportError → subtitles rỗng → weak 371/372)
                    with open(self.transcript_srt, encoding="utf-8", errors="replace") as _srt_f:
                        subtitles = PremiumReviewPipeline.parse_srt_content(_srt_f.read())
                except Exception:
                    subtitles = []
            target_duration = float(target_duration or self.cut_duration or 0.0)
            if self._recap2_beat_mode_enabled():
                target_book_seconds = self._target_recap2_beat_seconds(target_duration)
            else:
                max_books = self._max_ai_blocks(target_duration)
                target_book_seconds = 18.0
                if target_duration > 0:
                    target_book_seconds = max(12.0, min(26.0, target_duration / max_books))
            books = PremiumReviewPipeline.build_book_map(
                blocks,
                subtitles,
                target_book_seconds=target_book_seconds,
            )
            if books and len(books) < len(blocks):
                for book in books:
                    book["recap2_beat_mode"] = bool(self._recap2_beat_mode_enabled())
                    book["script_block_strategy"] = "recap2_beat_first"
                    book["raw_render_block_count"] = len(blocks)
                self._log(
                    f"   RECAP2 beat script: gom cảnh nhỏ {len(blocks)} -> {len(books)} "
                    f"beat (~{target_book_seconds:.1f}s/beat)"
                )
                return books
        except Exception as e:
            self._log(f"   Không gom được render_blocks: {e}")
        return blocks

    def _split_long_render_blocks(self, render_blocks: list, max_seconds: float = 0.0) -> list:
        """Split very long narration blocks so TTS can stay close to scene timing."""
        blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks:
            return blocks
        try:
            max_seconds = float(max_seconds or os.environ.get("AUTORECAP_MAX_VOICE_BLOCK_SECONDS", "32") or "32")
        except Exception:
            max_seconds = 32.0
        max_seconds = max(12.0, min(60.0, max_seconds))
        result = []
        split_count = 0
        for block in blocks:
            try:
                start = float(block.get("start_in_final_video") or block.get("start") or 0.0)
                duration = float(block.get("duration") or 0.0)
                end = float(block.get("end_in_final_video") or (start + duration))
                if duration <= 0 and end > start:
                    duration = end - start
            except Exception:
                result.append(dict(block))
                continue
            if duration <= max_seconds * 1.15:
                result.append(dict(block))
                continue
            parts = max(2, int((duration + max_seconds - 0.001) // max_seconds))
            chunk = duration / parts
            orig_start = float(block.get("original_start") or start)
            orig_end = float(block.get("original_end") or (orig_start + duration))
            orig_dur = max(0.0, orig_end - orig_start)
            scene_ids = block.get("scene_ids") or ([block.get("scene_id")] if block.get("scene_id") is not None else [])
            for part in range(parts):
                child = dict(block)
                child_start = start + chunk * part
                child_end = start + chunk * (part + 1)
                if part == parts - 1:
                    child_end = start + duration
                child["start_in_final_video"] = round(child_start, 3)
                child["end_in_final_video"] = round(child_end, 3)
                child["duration"] = round(max(0.1, child_end - child_start), 3)
                child["duration_hint_seconds"] = child["duration"]
                if orig_dur > 0:
                    child["original_start"] = round(orig_start + orig_dur * (part / parts), 3)
                    child["original_end"] = round(orig_start + orig_dur * ((part + 1) / parts), 3)
                child["split_parent_block_id"] = block.get("block_id") or block.get("book_id")
                child["split_part"] = part + 1
                child["split_parts"] = parts
                if scene_ids:
                    child["scene_ids"] = scene_ids
                result.append(child)
            split_count += 1
        if split_count:
            for idx, block in enumerate(result, 1):
                block["block_id"] = idx
                block["book_id"] = idx
            self._log(
                f"   ✂️  Chia block voice quá dài: {len(blocks)} -> {len(result)} blocks "
                f"(max ~{max_seconds:.0f}s/block)"
            )
        return result

    def _ensure_keyframe_aliases(self, kf_dir: str) -> None:
        """Create scene_ aliases and a small manifest for existing kf_ images."""
        if not kf_dir or not os.path.isdir(kf_dir):
            return
        manifest = []
        try:
            import shutil as _sh
            for name in sorted(os.listdir(kf_dir)):
                if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                match = re.match(r"(?:kf|scene)_(\d+)\.(?:jpg|jpeg|png)$", name, flags=re.IGNORECASE)
                if not match:
                    continue
                sid = int(match.group(1))
                path = os.path.join(kf_dir, name)
                scene_alias = os.path.join(kf_dir, f"scene_{sid:04d}.jpg")
                if name.lower().startswith("kf_") and not os.path.exists(scene_alias):
                    try:
                        _sh.copy2(path, scene_alias)
                    except Exception:
                        scene_alias = ""
                manifest.append({
                    "scene_id": sid,
                    "path": path,
                    "scene_alias": scene_alias if os.path.exists(scene_alias) else "",
                    "time_s": 0.0,
                })
            if manifest:
                with open(os.path.join(kf_dir, "keyframes_manifest.json"), "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _rebuild_pattern_render_blocks(self, target_duration: float = 0.0) -> list:
        """Rebuild render blocks from keep/skip pattern when cached metadata is missing/bad."""
        try:
            from core.video_cutter import VideoCutter
            from core.calculator import VideoCalculator
            source_duration = (
                float(self.metadata.get("duration_s") or 0.0)
                or VideoCutter.get_video_duration(self.video_path)
                or 0.0
            )
            if source_duration <= 0:
                return []
            raw_segments = VideoCutter.get_keep_segments(
                source_duration,
                self.keep_seconds,
                self.skip_seconds,
            )
            if target_duration and target_duration > 0:
                raw_segments = VideoCutter._limit_segments_to_duration(raw_segments, target_duration)
            return VideoCalculator.get_render_blocks(raw_segments)
        except Exception as e:
            self._log(f"   Không rebuild được render_blocks pattern: {e}")
            return []

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    def _write_debug_log(self, msg: str) -> None:
        """Keep complete diagnostics without flooding the customer-facing log."""
        try:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            with open(self._debug_log_path, "a", encoding="utf-8") as handle:
                handle.write(str(msg).rstrip("\n") + "\n")
        except Exception:
            pass

    def _reset_debug_log(self) -> None:
        try:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            with open(self._debug_log_path, "w", encoding="utf-8") as handle:
                handle.write("AutoRecapPro V2 - pipeline diagnostics\n")
        except Exception:
            pass

    def _user_log_message(self, msg: str) -> Optional[str]:
        """Return a concise customer-facing message, or None for debug-only lines."""
        text = str(msg or "")
        stripped = text.strip()
        if not stripped or self._verbose_logs:
            return text

        tts_progress = re.match(r"^\[TTS_PROGRESS\]\s+(\d+)/(\d+)$", stripped, flags=re.IGNORECASE)
        if tts_progress:
            current, total = map(int, tts_progress.groups())
            if current == 1 or current == total or current % 5 == 0:
                return f"   🔊 TTS đang tạo/kiểm tra giọng đọc: {current}/{total} block"
            return None

        # Step lifecycle and fatal errors are always useful to the user.
        if re.match(r"^(?:▶️|✅|❌|⏭️|♻️)\s*\[[A-Z0-9_]+\]", stripped):
            if "Evidence gate FAIL" in stripped:
                return "❌ [AI_FULL] Thất bại: SRT hoặc hình ảnh đầu vào chưa đủ để viết kịch bản bám phim."
            return text

        # These reports are valuable for support, but their PASS/FAIL wording is
        # misleading before Script Editor has completed the final review.
        technical_markers = (
            "[SRT_ALIGNMENT]",
            "[SRT_TRIANGULATION]",
            "Market readiness:",
            "SceneMappingValidator",
            "Evidence gate:",
            "Context coverage:",
            "Block context:",
            "AI package cache",
            "render_blocks cache",
            "VOICE_SRT cache",
            "source range của beat",
            "neo " ,
            "cảnh chuyển động",
            "ClipAssembler QA:",
        )
        if any(marker in stripped for marker in technical_markers):
            return None
        if re.match(r"^-\s+(?:WARNING|WARN|ERROR):", stripped, flags=re.IGNORECASE):
            return None

        # Replace hundreds of per-block timing lines with lightweight progress.
        tts_match = re.match(r"^block\s+(\d+)/(\d+):\s*TTS\b", stripped, flags=re.IGNORECASE)
        if tts_match:
            current, total = map(int, tts_match.groups())
            if current == 1 or current == total or current % 10 == 0:
                return f"   🔊 Đang tạo giọng đọc: {current}/{total} block"
            return None

        render_match = re.match(r"^✅\s*Block\s+(\d+)/(\d+):", stripped, flags=re.IGNORECASE)
        if render_match:
            current, total = map(int, render_match.groups())
            if current == 1 or current == total or current % 10 == 0:
                return f"   🎬 Đang dựng cảnh: {current}/{total} block"
            return None

        if re.match(r"^block\s+\d+:", stripped, flags=re.IGNORECASE):
            return None
        if stripped.startswith("✅ Block "):
            return None

        hidden_fragments = (
            "resume:",
            "Map-reduce direct:",
            "Dùng chapter-budget story segments trực tiếp",
            "Dùng Gemini batched",
            "tổng voice thật",
            "Voice thật ",
            "Continuous voice:",
            "Đồng bộ Script Editor -> render_blocks",
            "Đã đóng Gemini Web browser",
        )
        if any(fragment in stripped for fragment in hidden_fragments):
            return None

        return text

    def _log(self, msg: str):
        import sys
        self._write_debug_log(msg)
        msg = self._user_log_message(msg)
        if msg is None:
            return
        try:
            # Try to send the original message to the progress callback.
            self.progress_callback(msg)
        except UnicodeEncodeError:
            # Fallback: replace non-encodable characters to avoid stdout errors (e.g., emoji on cp1252)
            enc = sys.stdout.encoding or "utf-8"
            safe_msg = msg.encode(enc, errors="replace").decode(enc, errors="replace")
            try:
                self.progress_callback(safe_msg)
            except Exception:
                # Last resort: silently ignore to avoid crashing the pipeline
                pass
        except Exception:
            # For any other exception from the callback, try a safe, encoded message once and ignore failures.
            try:
                enc = sys.stdout.encoding or "utf-8"
                safe_msg = msg.encode(enc, errors="replace").decode(enc, errors="replace")
                self.progress_callback(safe_msg)
            except Exception:
                pass

    def _safe_step_callback(self, name: str, status: str):
        """Call step_callback safely, avoiding UnicodeEncodeError when the callback prints to a narrow-codepage stdout."""
        if not self.step_callback:
            return
        try:
            self.step_callback(name, status)
        except UnicodeEncodeError:
            import sys
            enc = sys.stdout.encoding or "utf-8"
            safe_name = str(name).encode(enc, errors="replace").decode(enc, errors="replace")
            safe_status = str(status).encode(enc, errors="replace").decode(enc, errors="replace")
            try:
                self.step_callback(safe_name, safe_status)
            except Exception:
                pass
        except Exception:
            # On any other exception, try one encoded attempt then ignore.
            try:
                import sys
                enc = sys.stdout.encoding or "utf-8"
                safe_name = str(name).encode(enc, errors="replace").decode(enc, errors="replace")
                safe_status = str(status).encode(enc, errors="replace").decode(enc, errors="replace")
                self.step_callback(safe_name, safe_status)
            except Exception:
                pass

    def _step_start(self, name: str):
        step = self.steps[name]
        step.start()
        if getattr(self, "pipeline_state", None):
            self.pipeline_state.mark_running(name)
        self._log(f"▶️  [{name}] Bắt đầu...")
        if self.step_callback:
            self._safe_step_callback(name, "running")

    def _step_done(self, name: str, info: str = ""):
        step = self.steps[name]
        step.complete(info)
        if getattr(self, "pipeline_state", None):
            self.pipeline_state.mark_done(name, info)
        self._log(f"✅ [{name}] Hoàn tất{(' - ' + info) if info else ''} ({step.duration_s:.1f}s)")
        if self.step_callback:
            self._safe_step_callback(name, "done")

    def _step_fail(self, name: str, error: str):
        step = self.steps[name]
        step.fail(error)
        if getattr(self, "pipeline_state", None):
            self.pipeline_state.mark_failed(name, error)
        self._log(f"❌ [{name}] Thất bại: {error}")
        if self.step_callback:
            self._safe_step_callback(name, "failed")

    def _step_skip(self, name: str, reason: str = ""):
        step = self.steps[name]
        step.skip(reason)
        self._log(f"⏭️  [{name}] Bỏ qua{(' - ' + reason) if reason else ''}")
        if self.step_callback:
            self._safe_step_callback(name, "skipped")

    def _with_retry(self, step_fn, step_name: str = "", max_retries: int = 3) -> bool:
        """Chạy step_fn, tự retry tối đa max_retries lần nếu trả về False.
        Dùng cho các bước có thể fail do mạng/AI timeout (không phải lỗi logic).
        """
        import time
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                wait = 3 * attempt
                self._log(f"   ♻️  [{step_name}] Thử lại lần {attempt}/{max_retries} (chờ {wait}s)...")
                time.sleep(wait)
                # Reset step status về pending để step_fn có thể chạy lại
                step = self.steps.get(step_name)
                if step:
                    step.status = "pending"
                    step.error = ""
                    step.duration_s = 0.0
            result = step_fn()
            if result:
                return True
            if attempt < max_retries:
                self._log(f"   ⚠️  [{step_name}] Lần {attempt} thất bại, sẽ tự thử lại...")
        self._log(f"   ❌ [{step_name}] Thất bại sau {max_retries} lần thử.")
        return False

    def _ffmpeg(self):
        try:
            from utils.helpers import FFmpegUtils
            return FFmpegUtils.ffmpeg_executable()
        except Exception:
            import shutil
            return shutil.which("ffmpeg") or "ffmpeg"

    def _ffprobe(self):
        try:
            from utils.helpers import FFmpegUtils
            return FFmpegUtils.ffprobe_executable()
        except Exception:
            import shutil
            return shutil.which("ffprobe") or "ffprobe"

    def _media_duration(self, path: str) -> float:
        if not path or not os.path.exists(path):
            return 0.0
        try:
            result = subprocess.run(
                [
                    self._ffprobe(), "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True,
                    text=True,
                    timeout=15,
                ),
            )
            return float((result.stdout or "").strip() or 0.0)
        except Exception:
            return 0.0

    def _cached(self, *file_paths) -> bool:
        """True nếu TẤT CẢ các file đã tồn tại và có dung lượng > 0.
        Dùng để kiểm tra resume: bỏ qua bước nếu output đã có sẵn."""
        return all(p and os.path.exists(p) and os.path.getsize(p) > 0 for p in file_paths)

    @staticmethod
    def _normalized_media_stem(path: str) -> str:
        stem = Path(path or "").stem.lower()
        for suffix in ("_capcut_translated", "_translated", "_review_vi", "_capcut", "_vi"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        stem = unicodedata.normalize("NFKD", stem)
        return "".join(ch for ch in stem if ch.isalnum())

    def _srt_matches_current_video(self, path: str) -> bool:
        """Reject an arbitrary SRT left by another movie in a reused output folder."""
        if not path or not os.path.isfile(path):
            return False
        if Path(path).name.lower() == "transcript.srt":
            return True
        video_stem = self._normalized_media_stem(self.video_path)
        srt_stem = self._normalized_media_stem(path)
        return bool(
            video_stem
            and srt_stem
            and (video_stem == srt_stem or video_stem in srt_stem or srt_stem in video_stem)
        )

    @staticmethod
    def _file_signature(path: str, include_hash: bool = False) -> Dict[str, Any]:
        if not path or not os.path.isfile(path):
            return {}
        resolved = str(Path(path).resolve())
        stat = os.stat(resolved)
        signature: Dict[str, Any] = {
            "path": os.path.normcase(resolved),
            "size": int(stat.st_size),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        }
        if include_hash:
            digest = hashlib.sha256()
            with open(resolved, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            signature["sha256"] = digest.hexdigest()
        return signature

    def _current_input_signature(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "video": self._file_signature(self.video_path, include_hash=False),
            "srt": self._file_signature(self.transcript_srt, include_hash=True),
        }

    @staticmethod
    def _same_input_part(previous: Any, current: Any) -> bool:
        if not previous and not current:
            return True
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return False
        keys = ("path", "size", "mtime_ns", "sha256")
        return all(previous.get(key) == current.get(key) for key in keys if key in previous or key in current)

    def _invalidate_cached_outputs(self, from_step: str, reason: str) -> None:
        """Discard only derived outputs affected by changed source video/SRT."""
        try:
            start = STEP_NAMES.index(from_step)
        except ValueError:
            start = 0
        if from_step == "SUBTITLE_MAP":
            # Keyframes depend only on the video/scenes, not on subtitle text.
            invalid_steps = ["SUBTITLE_MAP"] + STEP_NAMES[STEP_NAMES.index("AI_FULL"):]
        else:
            invalid_steps = STEP_NAMES[start:]

        files_by_step = {
            "METADATA": ["metadata.json"],
            "SCENE_DETECT": ["scenes.json"],
            "SUBTITLE_MAP": ["subtitle_map.json", "subtitle_map.meta.json"],
            "KEYFRAMES": ["keyframes_manifest.json"],
            "AI_FULL": [
                "scene_cards.json", "story_outline.json", "visual_scene_evidence.json",
                "evidence_gate_report.json", "srt_triangulation_report.json",
                "srt_alignment_report.json", "market_readiness_report.json",
                "ai_package.json", "raw_render_blocks.json", "render_blocks.json",
            ],
            "CLIP_FIND": ["cut_video.mp4", "cut_video.json"],
            "VOICE_SEGMENTS": ["voice_segments.json"],
            "VOICE_CONCAT": ["voice_track.mp3", "voice_track.wav"],
            "VOICE_SRT": ["voice_subtitles.srt", "voice_sync.json"],
        }
        directories_by_step = {
            "KEYFRAMES": ["keyframes"],
            "CLIP_FIND": ["cut_frames", "review_clips", "preview_segments"],
            "VOICE_SEGMENTS": ["voice_segments", ".preview_voice", ".preview_scene"],
        }
        output = Path(self.output_dir).resolve()
        for step in invalid_steps:
            for name in files_by_step.get(step, []):
                try:
                    (output / name).unlink(missing_ok=True)
                except Exception:
                    pass
            for name in directories_by_step.get(step, []):
                try:
                    target = (output / name).resolve()
                    if target.parent == output and target.exists():
                        shutil.rmtree(target)
                except Exception:
                    pass
        if "RENDER_FINAL" in invalid_steps:
            for target in output.glob("*_final.mp4"):
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass

        if from_step in {"METADATA", "SCENE_DETECT"}:
            self.metadata = {}
            self.scenes = []
            self.keyframes = []
        if start <= STEP_NAMES.index("SUBTITLE_MAP"):
            self.subtitle_map = []
            self.scene_cards = []
            self.story_outline = {}
        if start <= STEP_NAMES.index("AI_FULL"):
            self.ai_package = {}
            self.render_blocks = []
        if start <= STEP_NAMES.index("CLIP_FIND"):
            self.cut_video_path = ""
            self.cut_duration = 0.0
        if start <= STEP_NAMES.index("VOICE_SEGMENTS"):
            self.voice_segments = []
            self.concat_audio_path = ""
            self.voice_srt_path = ""
            self.final_video_path = ""
        if getattr(self, "pipeline_state", None):
            self.pipeline_state.invalidate_steps(invalid_steps)
        self._log(f"   ♻️  {reason} -> làm mới cache từ [{from_step}]")

    def _validate_pipeline_inputs(self) -> None:
        """Ensure cached script/voice artifacts were created from the current movie and SRT."""
        signature_path = Path(self.output_dir) / ".pipeline_inputs.json"
        current = self._current_input_signature()
        previous: Dict[str, Any] = {}
        try:
            if signature_path.exists():
                previous = json.loads(signature_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

        if previous:
            if not self._same_input_part(previous.get("video"), current.get("video")):
                self._invalidate_cached_outputs("METADATA", "Video đầu vào đã thay đổi")
                # METADATA was checked before the input signature. Rebuild it now
                # so later scene/AI steps never run with metadata from another movie.
                self.step_metadata()
            elif not self._same_input_part(previous.get("srt"), current.get("srt")):
                self._invalidate_cached_outputs("SUBTITLE_MAP", "SRT nguồn/dịch đã thay đổi")
        else:
            # Upgrade old projects that did not have a signature sidecar yet.
            metadata_path = Path(self.output_dir) / "metadata.json"
            video_sig = current.get("video") or {}
            legacy_video_changed = False
            if metadata_path.exists() and video_sig:
                try:
                    cached_metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                    cached_name = self._normalized_media_stem(str(cached_metadata.get("filename") or ""))
                    current_name = self._normalized_media_stem(self.video_path)
                    legacy_video_changed = bool(cached_name and current_name and cached_name != current_name)
                    legacy_video_changed = legacy_video_changed or (
                        metadata_path.stat().st_mtime_ns < int(video_sig.get("mtime_ns") or 0)
                    )
                except Exception:
                    legacy_video_changed = False
            if legacy_video_changed:
                self._invalidate_cached_outputs("METADATA", "Cache cũ không thuộc video hiện tại")
                self.step_metadata()

            map_path = Path(self.output_dir) / "subtitle_map.json"
            srt_sig = current.get("srt") or {}
            if (not legacy_video_changed) and map_path.exists() and srt_sig:
                try:
                    if map_path.stat().st_mtime_ns < int(srt_sig.get("mtime_ns") or 0):
                        self._invalidate_cached_outputs(
                            "SUBTITLE_MAP",
                            "SRT mới hơn subtitle map cũ",
                        )
                except Exception:
                    pass

        try:
            signature_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(f"   ⚠️  Không lưu được chữ ký video/SRT: {exc}")

    def _resume_skip(self, name: str, info: str = ""):
        """Log và đánh dấu bước là done khi resume từ cache."""
        step = self.steps[name]
        step.complete(info)
        if getattr(self, "pipeline_state", None):
            self.pipeline_state.mark_done(name, {"resume": True, "info": info})
        self._log(f"♻️  [{name}] Bỏ qua - đã có từ lần chạy trước{(' (' + info + ')') if info else ''}")
        if self.step_callback:
            self._safe_step_callback(name, "done")

    def _ensure_scene_cards(self) -> List[Dict]:
        """Build/load scene cards without adding a GUI-visible pipeline step."""
        cards_file = os.path.join(self.output_dir, "scene_cards.json")
        if self.scene_cards:
            return self.scene_cards
        if self._cached(cards_file):
            try:
                with open(cards_file, encoding="utf-8") as f:
                    self.scene_cards = json.load(f)
                try:
                    source_duration = float(self.metadata.get("duration_s") or 0.0)
                    cards_end = max(
                        float(card.get("end_s") or card.get("end") or 0.0)
                        for card in (self.scene_cards or [])
                        if isinstance(card, dict)
                    )
                except Exception:
                    source_duration = 0.0
                    cards_end = 0.0
                if source_duration > 0 and cards_end > 0 and cards_end < source_duration - 8.0:
                    self._log(
                        f"   SceneCards cache chỉ tới {cards_end:.0f}s/{source_duration:.0f}s -> tạo lại"
                    )
                    self.scene_cards = []
                else:
                    return self.scene_cards
            except Exception:
                self.scene_cards = []
        if not self.scenes:
            return []
        try:
            from core.scene_cards import SceneCardBuilder
            self.scene_cards = SceneCardBuilder.build(
                self.scenes,
                self.subtitle_map,
                self.keyframes,
            )
            SceneCardBuilder.write(self.scene_cards, cards_file)
            self._log(f"   SceneCards: {len(self.scene_cards)} cảnh -> {cards_file}")
        except Exception as e:
            self._log(f"   Không tạo được SceneCards: {e}")
            self.scene_cards = []
        return self.scene_cards

    def _ensure_story_outline(self) -> Dict[str, Any]:
        """Build/load a transcript-first story outline for prompt grounding."""
        outline_file = os.path.join(self.output_dir, "story_outline.json")
        if self.story_outline:
            return self.story_outline
        if self._cached(outline_file):
            try:
                with open(outline_file, encoding="utf-8") as f:
                    self.story_outline = json.load(f)
                return self.story_outline
            except Exception:
                self.story_outline = {}
        cards = self._ensure_scene_cards()
        if not cards:
            return {}
        try:
            from core.transcript_story import TranscriptFirstStoryPlanner
            # The outline must use the selected narration budget. A cached
            # cut_video is only a preview pool and must never shrink the next
            # AI story plan.
            target_duration = (
                (self.max_video_minutes * 60)
                if self.max_video_minutes
                else self.metadata.get("duration_s", 0)
            )
            self.story_outline = TranscriptFirstStoryPlanner.build(cards, target_duration)
            TranscriptFirstStoryPlanner.write(self.story_outline, outline_file)
            self._log(f"   Story outline: {len(self.story_outline.get('acts', []))} act -> {outline_file}")
        except Exception as e:
            self._log(f"   Không tạo được story outline: {e}")
            self.story_outline = {}
        return self.story_outline

    # ──────────────────────────────────────────────────────────────
    # STEP 1: METADATA
    # ──────────────────────────────────────────────────────────────

    def _detect_and_store_intro_outro(self):
        """Phát hiện intro/outro boundaries bằng AI, lưu vào metadata.
        Non-fatal — bỏ qua nếu không có API key hoặc SRT.
        Kết quả dùng để cắt bỏ phần đầu/cuối không liên quan khi băm video.
        """
        # Optional AI trimming must not silently open/wait on Gemini Web after
        # TRANSCRIPT has already been reported complete. Preserve all footage
        # unless the operator explicitly enables this extra analysis.
        intro_outro_enabled = str(os.environ.get("AUTORECAP_INTRO_OUTRO", "0") or "0").strip().lower() not in {"0", "false", "no", "off"}
        if not intro_outro_enabled:
            self._log('   ℹ️ Giữ nguyên đầu/cuối video; chuyển tiếp sang phân cảnh, không chờ AI dò intro/outro.')
            return
        if not self.gemini_api_key or not self.transcript_srt:
            return
        if self.metadata.get("content_start_s") is not None:
            return  # đã có từ cache
        try:
            self._log('   ⏳ Đang dò intro/outro bằng AI (đã bật AUTORECAP_INTRO_OUTRO).')
            from core.recap_engine import detect_intro_outro_boundaries
            from core.ai_engine import AIEngine
            ai = AIEngine(api_key=self.gemini_api_key)
            video_duration = float(self.metadata.get("duration_s") or 0)
            if video_duration <= 0:
                return
            content_start, content_end = detect_intro_outro_boundaries(
                srt_path=self.transcript_srt,
                video_duration=video_duration,
                ai_call=lambda prompt: ai._try_generate(prompt),
                log=self._log,
            )
            self.metadata["content_start_s"] = content_start
            self.metadata["content_end_s"] = content_end
            # Persist vào metadata.json
            meta_file = os.path.join(self.output_dir, "metadata.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "w", encoding="utf-8") as f:
                        json.dump(self.metadata, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
        except Exception as e:
            self._log(f"   ⚠️ Intro/outro detection skip: {e}")

    def step_metadata(self) -> bool:
        """Phân tích thông tin kỹ thuật của video."""
        meta_file = os.path.join(self.output_dir, "metadata.json")

        # Resume: load từ cache nếu đã có
        if self._cached(meta_file):
            try:
                with open(meta_file, encoding="utf-8") as f:
                    self.metadata = json.load(f)
                v = self.metadata.get("video", {})
                self._resume_skip("METADATA",
                    f"{v.get('width',0)}x{v.get('height',0)} | {self.metadata.get('duration_s',0):.0f}s")
                return True
            except Exception:
                pass  # file corrupt → re-run

        self._step_start("METADATA")
        try:
            ffprobe = self._ffprobe()
            cmd = [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", self.video_path,
            ]
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True,
                    text=True,
                    check=True,
                ),
            )
            raw = json.loads(result.stdout)

            fmt = raw.get("format", {})
            streams = raw.get("streams", [])
            video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

            self.metadata = {
                "filename": os.path.basename(self.video_path),
                "duration_s": float(fmt.get("duration", 0)),
                "size_mb": round(int(fmt.get("size", 0)) / 1e6, 2),
                "bitrate_kbps": round(int(fmt.get("bit_rate", 0)) / 1000),
                "format": fmt.get("format_name", ""),
                "video": {
                    "codec": video_stream.get("codec_name", ""),
                    "width": video_stream.get("width", 0),
                    "height": video_stream.get("height", 0),
                    "fps": eval(video_stream.get("r_frame_rate", "0/1")),
                    "profile": video_stream.get("profile", ""),
                },
                "audio": {
                    "codec": audio_stream.get("codec_name", ""),
                    "sample_rate": audio_stream.get("sample_rate", ""),
                    "channels": audio_stream.get("channels", 0),
                },
            }

            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)

            self._log(
                f"   📹 {self.metadata['video']['width']}x{self.metadata['video']['height']} | "
                f"{self.metadata['duration_s']:.1f}s | {self.metadata['size_mb']} MB | "
                f"{self.metadata['video']['fps']:.2f} fps"
            )
            self._step_done("METADATA", meta_file)
            return True

        except subprocess.CalledProcessError as e:
            # ffprobe failed (e.g., missing binary or unsupported file)
            self._step_skip("METADATA", "ffprobe failed – using fallback metadata")
            return True
        except json.JSONDecodeError:
            # Invalid ffprobe output – use fallback
            self._step_skip("METADATA", "ffprobe output invalid – using fallback")
            return True
        except Exception as e:
            # Any other error – try to use cached metadata, otherwise skip step
            if self._cached(meta_file):
                try:
                    with open(meta_file, encoding="utf-8") as f:
                        self.metadata = json.load(f)
                    self._log("   ♻️  Loaded cached metadata after error")
                except Exception:
                    pass
                self._resume_skip("METADATA", "using cached metadata")
                return True
            else:
                # No cache available – skip step (non‑fatal)
                self._step_skip("METADATA", "using fallback (no metadata)")
                return True

    # ──────────────────────────────────────────────────────────────
    # STEP 2: TRANSCRIPT
    # ──────────────────────────────────────────────────────────────

    def step_transcript(self) -> bool:
        """Trích xuất lời thoại gốc từ video CHƯA băm.

        SRT này dùng timestamp gốc (original_start/original_end) để AI map
        đúng thoại vào từng block khi generate kịch bản.

        Ưu tiên:
        1. source_srt_path đã cung cấp → dùng luôn
        2. Tìm file .srt cạnh video gốc (CapCut SRT của video gốc)
        3. Tìm trong output_dir
        4. CapCut ASR nền (không mở giao diện CapCut)

        Whisper chỉ chạy khi người dùng bật rõ AUTORECAP_ALLOW_WHISPER_FALLBACK=1.
        """
        self._step_start("TRANSCRIPT")

        # Priority 1: SRT đã cung cấp trực tiếp
        if self.source_srt_path and os.path.exists(self.source_srt_path):
            self.transcript_srt = self.source_srt_path
            self._step_done("TRANSCRIPT", f"SRT gốc: {os.path.basename(self.source_srt_path)}")
            return True

        # Priority 2: Tìm .srt cạnh video gốc — ưu tiên SRT đã dịch tiếng Việt
        # vì keyword matching sẽ tốt hơn khi cả voice review và SRT cùng tiếng Việt
        video_base = os.path.splitext(self.video_path)[0]
        for suffix in [
            "_capcut_translated.srt",   # SRT đã dịch → keyword match tốt nhất
            "_review_vi.srt",           # SRT review tiếng Việt
            "_vi.srt",
            "_capcut.srt",              # SRT gốc (có thể là tiếng Trung)
            ".srt",
        ]:
            candidate = video_base + suffix
            if os.path.exists(candidate):
                self.transcript_srt = candidate
                self._step_done("TRANSCRIPT", f"SRT gốc cạnh video: {os.path.basename(candidate)}")
                self._detect_and_store_intro_outro()
                return True

        # Priority 3: Tìm trong output_dir (tránh lấy voice_subtitles.srt của pipeline)
        for fname in sorted(os.listdir(self.output_dir)):
            if (fname.endswith(".srt")
                    and "voice_subtitles" not in fname
                    and "voice_sync" not in fname):
                candidate = os.path.join(self.output_dir, fname)
                if not self._srt_matches_current_video(candidate):
                    self._log(f"   ⚠️  Bỏ qua SRT không thuộc video hiện tại: {fname}")
                    continue
                self.transcript_srt = candidate
                self._step_done("TRANSCRIPT", f"SRT trong output: {fname}")
                self._detect_and_store_intro_outro()
                return True

        # Priority 4: CapCut subtitle service. This is much faster than local
        # Whisper for a full movie and does not require opening CapCut Desktop.
        capcut_mode = os.getenv("AUTORECAP_CAPCUT_SRT_MODE", "auto").strip().lower()
        if capcut_mode == "manual":
            self._step_fail(
                "TRANSCRIPT",
                "Chưa có SRT nguồn. Hãy dùng nút CapCut thủ công, tạo Auto Caption, "
                "đóng CapCut rồi chạy lại Full Pipeline.",
            )
            return False

        try:
            from engine.capcut_asr import CapCutDirectASR
            out_srt = os.path.join(self.output_dir, "transcript.srt")
            self._log("   🎙️ Không có SRT nguồn → nhận dạng bằng CapCut ASR nền")
            recognizer = CapCutDirectASR(
                progress_callback=lambda msg, pct: self._log(f"   🎙️ {pct}%: {msg}")
            )
            count = recognizer.transcribe(self.video_path, out_srt)
            self.transcript_srt = out_srt
            self._step_done("TRANSCRIPT", f"{count} dòng (CapCut ASR nền)")
            self._detect_and_store_intro_outro()
            return True
        except Exception as capcut_error:
            capcut_error_text = str(capcut_error)
            self._log(f"   ⚠️ CapCut ASR nền lỗi: {capcut_error_text}")
            if "HTTP 400" in capcut_error_text or "HTTP 401" in capcut_error_text or "HTTP 403" in capcut_error_text:
                self._log(
                    "   ℹ️ CapCut nền bị endpoint nội bộ từ chối. "
                    "Hãy dùng chế độ Thủ công: mở CapCut → tạo Auto Caption → đóng CapCut."
                )

        # Local Whisper is deliberately opt-in. Never make users wait through
        # a long CPU transcription just because an online request failed.
        if os.getenv("AUTORECAP_ALLOW_WHISPER_FALLBACK", "0").strip() == "1":
            self._log("   🎙️ Đã bật fallback thủ công → thử Whisper")
            try:
                from core.srt_generator import SRTGenerator
                out_srt = os.path.join(self.output_dir, "transcript.srt")
                success, error = SRTGenerator.generate_srt_from_video(
                    self.video_path,
                    out_srt,
                    use_whisper=True,
                    whisper_model="base",
                    progress_callback=lambda msg, pct: self._log(
                        f"   🎙️ Whisper {pct}%: {msg}"
                    ),
                )
                if success and os.path.exists(out_srt):
                    self.transcript_srt = out_srt
                    self._step_done("TRANSCRIPT", "SRT từ Whisper (fallback thủ công)")
                    return True
                self._log(f"   ⚠️ Whisper lỗi: {error}")
            except Exception as whisper_error:
                self._log(f"   ⚠️ Whisper lỗi: {whisper_error}")

        self._step_fail(
            "TRANSCRIPT",
            "Chưa có SRT nguồn hợp lệ. Hãy dùng nút MỞ CAPCUT TẠO SRT, "
            "tạo Auto Caption rồi đóng CapCut; app sẽ lấy SRT từ project CapCut. "
            "CapCut nền chỉ là chế độ thử nghiệm và có thể bị HTTP 400 trên máy khách.",
        )
        return False

    # ──────────────────────────────────────────────────────────────
    # STEP 3: SCENE_DETECT
    # ──────────────────────────────────────────────────────────────

    # STEP 2b: AUTO_TRANSLATE
    # ──────────────────────────────────────────────────────────────

    def step_auto_translate(self) -> None:
        """Tự động dịch SRT tiếng Trung → tiếng Việt nếu cần.

        - Không phải bước fatal: nếu dịch lỗi, giữ nguyên SRT gốc và tiếp tục.
        - Nếu SRT đã là tiếng Việt → bỏ qua luôn (không gọi API).
        - Kết quả lưu ra _capcut_translated.srt hoặc _translated.srt cạnh SRT gốc.
        - Cập nhật self.transcript_srt trỏ sang file đã dịch.
        """
        if not self.transcript_srt or not os.path.exists(self.transcript_srt):
            return

        # Kiểm tra xem có cần dịch không
        try:
            from engine.srt_processor import SRTParser
            from engine.srt_translator import SRTTranslator
        except Exception as e:
            self._log(f"   🌐 AUTO_TRANSLATE: không load được module ({e}) → bỏ qua")
            return

        try:
            subtitles = SRTParser.parse_srt(self.transcript_srt)
            if not subtitles:
                return
            if not SRTTranslator._needs_translation(subtitles):
                self._log("   🌐 AUTO_TRANSLATE: SRT đã là tiếng Việt → bỏ qua")
                return
        except Exception as e:
            self._log(f"   🌐 AUTO_TRANSLATE: không đọc được SRT ({e}) → bỏ qua")
            return

        # Kiểm tra cache: nếu file _translated đã tồn tại và hợp lệ → dùng lại
        output_path = SRTTranslator.default_output_path(self.transcript_srt)
        if SRTTranslator._valid_cached_output(self.transcript_srt, output_path):
            self._log(f"   🌐 AUTO_TRANSLATE: dùng lại SRT đã dịch: {os.path.basename(output_path)}")
            self.transcript_srt = output_path
            return

        # Cần Gemini key để dịch
        if not self.gemini_api_key:
            self._log("   🌐 AUTO_TRANSLATE: không có Gemini key → bỏ qua dịch SRT")
            return

        self._log(f"   🌐 AUTO_TRANSLATE: phát hiện SRT tiếng Trung → dịch sang tiếng Việt...")
        try:
            translated_path = SRTTranslator.translate_file_to_vietnamese(
                srt_path=self.transcript_srt,
                ai_keys=self.gemini_api_key,
                output_path=output_path,
                force=False,
                progress_callback=lambda msg: self._log(f"   🌐 {msg}"),
            )
            if translated_path and os.path.exists(translated_path):
                self.transcript_srt = translated_path
                self._log(f"   ✅ AUTO_TRANSLATE: đã dịch xong → {os.path.basename(translated_path)}")
            else:
                self._log("   ⚠️ AUTO_TRANSLATE: dịch không ra file → giữ SRT gốc")
        except Exception as e:
            self._log(f"   ⚠️ AUTO_TRANSLATE: lỗi dịch SRT ({e}) → giữ nguyên SRT gốc, tiếp tục pipeline")

    def step_scene_detect(self) -> bool:
        """Nhận diện các phân cảnh trong phim bằng phân tích frame."""
        scenes_file = os.path.join(self.output_dir, "scenes.json")

        # Resume
        if self._cached(scenes_file):
            try:
                with open(scenes_file, encoding="utf-8") as f:
                    self.scenes = json.load(f)
                try:
                    source_duration = float(self.metadata.get("duration_s") or 0.0)
                    scene_end = max(
                        float(scene.get("end_s") or scene.get("end") or 0.0)
                        for scene in (self.scenes or [])
                        if isinstance(scene, dict)
                    )
                except Exception:
                    source_duration = 0.0
                    scene_end = 0.0
                if source_duration > 0 and scene_end > 0 and scene_end < source_duration - 8.0:
                    self._log(
                        f"   SCENE_DETECT cache chỉ tới {scene_end:.0f}s/{source_duration:.0f}s -> chạy lại để phủ hết tập"
                    )
                    self.scenes = []
                else:
                    self._resume_skip("SCENE_DETECT", f"{len(self.scenes)} phân cảnh")
                    return True
            except Exception:
                pass

        self._step_start("SCENE_DETECT")
        try:
            try:
                import cv2
                import numpy as np
                CV2_AVAILABLE = True
            except ImportError:
                CV2_AVAILABLE = False

            scenes = []

            if CV2_AVAILABLE:
                scenes = self._detect_scenes_cv2()
            else:
                # Fallback: use ffmpeg scene detection
                scenes = self._detect_scenes_ffmpeg()

            if not scenes:
                self._step_skip("SCENE_DETECT", "Không phát hiện phân cảnh")
                self.scenes = []
                return True  # Non-fatal

            self.scenes = scenes
            scenes_file = os.path.join(self.output_dir, "scenes.json")
            with open(scenes_file, "w", encoding="utf-8") as f:
                json.dump(scenes, f, indent=2, ensure_ascii=False)

            self._step_done("SCENE_DETECT", f"{len(scenes)} phân cảnh → {scenes_file}")
            return True

        except Exception as e:
            self._step_fail("SCENE_DETECT", str(e))
            return False

    def _detect_scenes_cv2(self) -> List[Dict]:
        """Detect scene cuts using OpenCV frame differencing."""
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        THRESHOLD = 30.0
        SAMPLE_EVERY = max(1, int(fps / 2))  # sample 2 frames/sec

        scenes = []
        scene_start = 0.0
        prev_gray = None
        scene_id = 0

        frame_idx = 0
        last_progress = 0.0
        self._log(f'[SCENE_PROGRESS] {0 if total_frames else -1}|Đang quét video — 0/{total_frames or "chưa rõ"} khung hình')
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            now = time.monotonic()
            if now-last_progress >= 1.0:
                percent = min(99,int(frame_idx*100/total_frames)) if total_frames else -1
                current_s = frame_idx/fps
                total_s = total_frames/fps
                self._log(f'[SCENE_PROGRESS] {percent}|Đã đọc {frame_idx}/{total_frames or "chưa rõ"} khung hình — {current_s:.1f}/{total_s:.1f} giây — tìm thấy {len(scenes)} cảnh')
                last_progress = now
            if frame_idx % SAMPLE_EVERY != 0:
                frame_idx += 1
                continue

            t = frame_idx / fps
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                diff = cv2.absdiff(prev_gray, gray)
                score = float(np.mean(diff))

                if score > THRESHOLD:
                    scenes.append({
                        "scene_id": scene_id,
                        "start_s": round(scene_start, 3),
                        "end_s": round(t, 3),
                        "duration_s": round(t - scene_start, 3),
                        "cut_score": round(score, 2),
                    })
                    scene_id += 1
                    scene_start = t

            prev_gray = gray
            frame_idx += 1

        if total_frames and frame_idx < total_frames-2:
            cap.release()
            raise RuntimeError(f'Đọc video dừng sớm tại khung {frame_idx}/{total_frames}; chưa quét hết video')
        # Last scene. OpenCV frame count can be shorter than ffprobe on some
        # streams, so prefer metadata duration when it is larger.
        total_duration = total_frames / fps if fps else 0
        try:
            meta_duration = float(self.metadata.get("duration_s") or 0.0)
            if meta_duration > total_duration:
                total_duration = meta_duration
        except Exception:
            pass
        if scene_start < total_duration:
            scenes.append({
                "scene_id": scene_id,
                "start_s": round(scene_start, 3),
                "end_s": round(total_duration, 3),
                "duration_s": round(total_duration - scene_start, 3),
                "cut_score": 0.0,
            })

        cap.release()
        self._log(f'[SCENE_PROGRESS] 100|Quét xong — tìm thấy {len(scenes)} cảnh')
        return scenes

    def _detect_scenes_ffmpeg(self) -> List[Dict]:
        """Detect scenes using ffmpeg scdet filter."""
        ffmpeg = self._ffmpeg()
        cmd = [
            ffmpeg, "-i", self.video_path,
            "-vf", "scdet=threshold=10",
            "-an", "-f", "null", "-",
        ]
        result = subprocess.run(
            cmd,
            **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True),
        )
        # Parse stderr for scene detection timestamps
        import re
        scenes = []
        scene_id = 0
        timestamps = re.findall(r"pts_time:([\d.]+).*?score:([\d.]+)", result.stderr)
        prev_t = 0.0
        for t_str, score_str in timestamps:
            t = float(t_str)
            scenes.append({
                "scene_id": scene_id,
                "start_s": round(prev_t, 3),
                "end_s": round(t, 3),
                "duration_s": round(t - prev_t, 3),
                "cut_score": float(score_str),
            })
            scene_id += 1
            prev_t = t
        try:
            total_duration = float(self.metadata.get("duration_s") or 0.0)
        except Exception:
            total_duration = 0.0
        if total_duration > prev_t + 0.25:
            scenes.append({
                "scene_id": scene_id,
                "start_s": round(prev_t, 3),
                "end_s": round(total_duration, 3),
                "duration_s": round(total_duration - prev_t, 3),
                "cut_score": 0.0,
            })
        return scenes

    # ──────────────────────────────────────────────────────────────
    # STEP 4: SUBTITLE_MAP
    # ──────────────────────────────────────────────────────────────

    def step_subtitle_map(self) -> bool:
        """Khớp lời thoại gốc vào từng phân cảnh."""
        map_file = os.path.join(self.output_dir, "subtitle_map.json")
        map_meta_file = os.path.join(self.output_dir, "subtitle_map.meta.json")

        # Resume (chỉ skip nếu có transcript SRT - nếu không có thì cũng skip luôn)
        if self._cached(map_file):
            try:
                expected_signature = self._current_input_signature()
                cached_signature = {}
                if self._cached(map_meta_file):
                    with open(map_meta_file, encoding="utf-8") as meta_handle:
                        cached_signature = json.load(meta_handle)
                if cached_signature and (
                    not self._same_input_part(cached_signature.get("video"), expected_signature.get("video"))
                    or not self._same_input_part(cached_signature.get("srt"), expected_signature.get("srt"))
                ):
                    self._log("   SUBTITLE_MAP không cùng video/SRT hiện tại -> map lại")
                    self.subtitle_map = []
                    raise ValueError("stale subtitle map")
                with open(map_file, encoding="utf-8") as f:
                    self.subtitle_map = json.load(f)
                map_end = 0.0
                scene_end = 0.0
                try:
                    map_end = max(
                        float(item.get("scene_end_s") or item.get("end_s") or item.get("end") or 0.0)
                        for item in (self.subtitle_map or [])
                        if isinstance(item, dict)
                    )
                    scene_end = max(
                        float(item.get("end_s") or item.get("end") or 0.0)
                        for item in (self.scenes or [])
                        if isinstance(item, dict)
                    )
                except Exception:
                    map_end = 0.0
                    scene_end = 0.0
                if self.scenes and len(self.subtitle_map or []) < max(1, int(len(self.scenes) * 0.80)):
                    self._log(
                        f"   SUBTITLE_MAP cache lệch scenes "
                        f"({len(self.subtitle_map or [])}/{len(self.scenes)}) -> map lại"
                    )
                    self.subtitle_map = []
                elif self.scenes and scene_end > 0 and map_end > 0 and map_end < scene_end - 8.0:
                    self._log(
                        f"   SUBTITLE_MAP cache chỉ tới {map_end:.0f}s/{scene_end:.0f}s -> map lại"
                    )
                    self.subtitle_map = []
                else:
                    total_mapped = sum(s.get("subtitle_count", 0) for s in self.subtitle_map)
                    self._resume_skip("SUBTITLE_MAP", f"{total_mapped} dòng / {len(self.subtitle_map)} cảnh")
                    return True
            except Exception:
                pass

        self._step_start("SUBTITLE_MAP")

        if not self.transcript_srt or not os.path.exists(self.transcript_srt):
            self._step_skip("SUBTITLE_MAP", "Không có transcript SRT")
            return True  # Non-fatal

        try:
            from core.srt_processor import SRTParser
            subs = SRTParser.parse_srt(self.transcript_srt)

            scenes_to_use = self.scenes
            if not scenes_to_use:
                # Build synthetic scenes from video duration
                total = self.metadata.get("duration_s", 0) or float(
                    self.metadata.get("duration_s", 0)
                )
                if total > 0:
                    scene_len = 30.0
                    scenes_to_use = [
                        {
                            "scene_id": i,
                            "start_s": i * scene_len,
                            "end_s": min((i + 1) * scene_len, total),
                            "duration_s": scene_len,
                        }
                        for i in range(int(total / scene_len) + 1)
                        if i * scene_len < total
                    ]

            from core.scene_cards import SubtitleSceneMapper
            subtitle_map = SubtitleSceneMapper.map_subtitles_to_scenes(scenes_to_use, subs)

            self.subtitle_map = subtitle_map
            map_file = os.path.join(self.output_dir, "subtitle_map.json")
            with open(map_file, "w", encoding="utf-8") as f:
                json.dump(subtitle_map, f, indent=2, ensure_ascii=False)
            with open(map_meta_file, "w", encoding="utf-8") as f:
                json.dump(self._current_input_signature(), f, indent=2, ensure_ascii=False)

            total_mapped = sum(s["subtitle_count"] for s in subtitle_map)
            self._step_done("SUBTITLE_MAP", f"{total_mapped} dòng thoại → {len(subtitle_map)} phân cảnh")
            return True

        except Exception as e:
            self._step_fail("SUBTITLE_MAP", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 5: KEYFRAMES
    # ──────────────────────────────────────────────────────────────

    def step_keyframes(self) -> bool:
        """Trích xuất ảnh tiêu biểu từ mỗi phân cảnh."""
        kf_dir = os.path.join(self.output_dir, "keyframes")

        # Resume: nếu thư mục keyframes đã có ít nhất 1 ảnh jpg
        if os.path.isdir(kf_dir):
            existing = [os.path.join(kf_dir, f) for f in os.listdir(kf_dir) if f.endswith(".jpg")]
            if existing:
                manifest_end = 0.0
                scene_end = 0.0
                try:
                    manifest_file = os.path.join(kf_dir, "keyframes_manifest.json")
                    if os.path.exists(manifest_file):
                        with open(manifest_file, encoding="utf-8") as mf:
                            manifest_items = json.load(mf)
                        manifest_end = max(
                            float(item.get("end_s") or item.get("time_s") or 0.0)
                            for item in (manifest_items or [])
                            if isinstance(item, dict)
                        )
                    scene_end = max(
                        float(item.get("end_s") or item.get("end") or 0.0)
                        for item in (self.scenes or [])
                        if isinstance(item, dict)
                    )
                except Exception:
                    manifest_end = 0.0
                    scene_end = 0.0
                if self.scenes and len(existing) < max(1, int(len(self.scenes) * 0.60)):
                    self._log(
                        f"   KEYFRAMES cache thiếu ({len(existing)}/{len(self.scenes)} scenes) -> trích lại"
                    )
                elif self.scenes and scene_end > 0 and manifest_end > 0 and manifest_end < scene_end - 8.0:
                    self._log(
                        f"   KEYFRAMES cache chỉ tới {manifest_end:.0f}s/{scene_end:.0f}s -> trích lại"
                    )
                else:
                    self._ensure_keyframe_aliases(kf_dir)
                    self.keyframes = sorted(existing)
                    self._resume_skip("KEYFRAMES", f"{len(self.keyframes)} ảnh")
                    return True

        self._step_start("KEYFRAMES")
        Path(kf_dir).mkdir(parents=True, exist_ok=True)

        try:
            import cv2
            CV2_OK = True
        except ImportError:
            CV2_OK = False

        try:
            scenes_to_use = self.scenes
            if not scenes_to_use:
                # Extract 1 keyframe every 30s if no scenes
                total = self.metadata.get("duration_s", 0)
                scenes_to_use = [
                    {"scene_id": i, "start_s": i * 30, "end_s": min((i + 1) * 30, total)}
                    for i in range(max(1, int(total / 30)))
                ]
            scenes_to_use = list(scenes_to_use or [])
            max_source_keyframes = int(os.environ.get("AUTORECAP_MAX_SOURCE_KEYFRAMES", "0") or "0")
            if max_source_keyframes > 0 and len(scenes_to_use) > max_source_keyframes:
                step = max(1, len(scenes_to_use) / float(max_source_keyframes))
                sampled = []
                used = set()
                for i in range(max_source_keyframes):
                    idx = min(len(scenes_to_use) - 1, int(round(i * step)))
                    if idx in used:
                        continue
                    used.add(idx)
                    sampled.append(scenes_to_use[idx])
                scenes_to_use = sampled or scenes_to_use[:max_source_keyframes]
                self._log(
                    f"   🖼️ KEYFRAMES: lấy {len(scenes_to_use)} ảnh đại diện "
                    f"từ {len(self.scenes or [])} phân cảnh để tránh quá nặng"
                )

            keyframes = []
            keyframe_manifest = []

            def _record_keyframe(scene, path, mid_t):
                if not path or not os.path.exists(path):
                    return
                keyframes.append(path)
                try:
                    sid = int(scene.get("scene_id", len(keyframes)))
                    scene_alias = os.path.join(kf_dir, f"scene_{sid:04d}.jpg")
                    if os.path.abspath(scene_alias) != os.path.abspath(path) and not os.path.exists(scene_alias):
                        import shutil as _sh
                        _sh.copy2(path, scene_alias)
                    keyframe_manifest.append({
                        "scene_id": sid,
                        "path": path,
                        "scene_alias": scene_alias if os.path.exists(scene_alias) else "",
                        "time_s": round(float(mid_t or 0.0), 3),
                        "start_s": round(float(scene.get("start_s") or 0.0), 3),
                        "end_s": round(float(scene.get("end_s") or 0.0), 3),
                    })
                except Exception:
                    pass

            from core.parallel_jobs import ordered_parallel, worker_count
            workers = worker_count('AUTORECAP_KEYFRAME_WORKERS',3)
            self._log(f'   🧵 Trích ảnh: {workers} luồng, {len(scenes_to_use)} cảnh')

            def extract_scene(scene):
                mid_t = (scene['start_s']+scene['end_s'])/2
                path = os.path.join(kf_dir,f"kf_{int(scene['scene_id']):04d}.jpg")
                if CV2_OK:
                    cap = cv2.VideoCapture(self.video_path)
                    try:
                        if cap.isOpened():
                            cap.set(cv2.CAP_PROP_POS_MSEC,max(0,mid_t)*1000)
                            ok,frame = cap.read()
                            if ok and cv2.imwrite(path,frame):
                                return scene,path,mid_t
                    finally:
                        cap.release()
                command = [self._ffmpeg(),'-y','-ss',str(max(0,mid_t)),
                           '-threads','1','-i',self.video_path,'-frames:v','1','-q:v','2',path]
                result = subprocess.run(command,**FFmpegUtils.subprocess_kwargs(
                    capture_output=True,timeout=25))
                if result.returncode or not os.path.exists(path) or not os.path.getsize(path):
                    raise RuntimeError(f"Không trích được ảnh cảnh {scene['scene_id']}")
                return scene,path,mid_t

            def image_progress(done,total):
                self._log(f'[JOB_PROGRESS] {done*100//max(1,total)}|Trích ảnh: đã xong {done}/{total} cảnh ({workers} luồng)')

            # Only the coordinator updates manifests, in original scene order.
            for scene,path,mid_t in ordered_parallel(extract_scene,scenes_to_use,workers,image_progress):
                _record_keyframe(scene,path,mid_t)

            self.keyframes = keyframes
            try:
                with open(os.path.join(kf_dir, "keyframes_manifest.json"), "w", encoding="utf-8") as f:
                    json.dump(keyframe_manifest, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            if keyframes:
                self._step_done("KEYFRAMES", f"{len(keyframes)} ảnh → {kf_dir}")
                return True
            self._step_fail("KEYFRAMES", f"Không trích được ảnh từ video: {self.video_path}")
            return False

        except Exception as e:
            self._step_fail("KEYFRAMES", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 6: AI_FULL
    # ──────────────────────────────────────────────────────────────

    def step_ai_full(self) -> bool:
        """AI phân tích toàn phim, xây dựng timeline và viết kịch bản thuyết minh.
        
        Gọi trước CLIP_FIND để viết story chapter và narration từ scene/SRT/keyframe context.
        Script sẽ được generate với target_words theo đúng duration từng block.
        """
        pkg_file = os.path.join(self.output_dir, "ai_package.json")

        # Resume: load ai_package từ cache
        # Lưu ý: KHÔNG skip nếu render_blocks chưa có → cần rebuild
        if self._cached(pkg_file) and self.render_blocks:
            try:
                with open(pkg_file, encoding="utf-8") as f:
                    self.ai_package = json.load(f)
                try:
                    from core.script_grounding import ScriptGroundingValidator
                    script_block_count = len(self.ai_package.get("script_blocks") or [])
                    vi_report = self.ai_package.get("vietnamese_diacritics_report") or {}
                    planned_cache_duration = ((self.max_video_minutes * 60) if self.max_video_minutes else 0) or self.metadata.get("duration_s", 0)
                    cached_planned_duration = float(self.ai_package.get("planned_duration_seconds") or self.ai_package.get("target_duration_seconds") or 0)
                    # Ngân sách do người dùng chọn/tự nhập là contract của kịch bản.
                    # Không tái sử dụng package cũ khi chỉ đổi vài phút hoặc số lẻ.
                    duration_changed = bool(
                        planned_cache_duration
                        and cached_planned_duration
                        and abs(float(planned_cache_duration) - cached_planned_duration) > 1.0
                    )
                    too_many_blocks = script_block_count > self._max_ai_blocks(planned_cache_duration or self.cut_duration) * 2
                    missing_diacritics = bool(vi_report.get("needs_repair"))
                    source_duration = float(self.metadata.get("duration_s") or 0.0)
                    source_coverage_end = self._render_blocks_source_end(self.render_blocks)
                    source_coverage_missing = bool(
                        source_duration > 0
                        and source_coverage_end > 0
                        and source_coverage_end < source_duration - 8.0
                    )
                    if self.ai_package.get("ai_quota_fallback_used"):
                        self._log("   AI package la quota fallback -> sinh lai khi API san sang")
                        self.ai_package = {}
                    elif (
                        self._recap2_beat_mode_enabled()
                        and self.ai_package.get("script_block_strategy")
                        not in {"recap2_beat_first", "chapter_budget_story_segment"}
                    ):
                        self._log("   AI package cache chưa theo RECAP2 beat mode -> sinh lại")
                        self.ai_package = {}
                    elif duration_changed:
                        self._log("   AI package cache lệch độ dài recap -> sinh lại")
                        self.ai_package = {}
                    elif int(self.ai_package.get("review_duration_budget_version") or 0) < 4:
                        # Budget v4 follows RECAP2's chars/minute contract. Old
                        # packages cannot be upgraded by metadata only because
                        # their narration text was written with a conflicting
                        # words/second budget.
                        self._log("   AI package cache dùng ngân sách độ dài cũ -> sinh lại theo RECAP2")
                        self.ai_package = {}
                    elif too_many_blocks:
                        self._log(f"   AI package qua nhieu block ({script_block_count}) -> sinh lai")
                        self.ai_package = {}
                    elif missing_diacritics:
                        self._log("   AI package thieu dau tieng Viet -> sinh lai")
                        self.ai_package = {}
                    elif source_coverage_missing:
                        self._log(
                            f"   AI package cache thiếu cuối tập "
                            f"({source_coverage_end:.0f}s/{source_duration:.0f}s) -> sinh lại"
                        )
                        self.ai_package = {}
                    elif self.ai_package.get("review_script_version") != ScriptGroundingValidator.VERSION:
                        self._log(f"   AI package cache cu -> sinh lai theo {ScriptGroundingValidator.VERSION}")
                        self.ai_package = {}
                    elif not self.ai_package.get("script_grounding_report"):
                        self.ai_package, _ = ScriptGroundingValidator.annotate_package(
                            self.ai_package,
                            self.render_blocks,
                        )
                        with open(pkg_file, "w", encoding="utf-8") as wf:
                            json.dump(self.ai_package, wf, indent=2, ensure_ascii=False)
                    if self.ai_package and not self.ai_package.get("srt_alignment_report"):
                        self.ai_package, _ = self._annotate_srt_alignment(
                            self.ai_package,
                            self.render_blocks,
                        )
                        with open(pkg_file, "w", encoding="utf-8") as wf:
                            json.dump(self.ai_package, wf, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                if not self.ai_package:
                    raise ValueError("stale_ai_package")
                if not self.ai_package.get("scene_mapping_report"):
                    try:
                        from core.scene_mapping_validator import SceneMappingValidator
                        self.ai_package, _ = SceneMappingValidator.annotate_package(
                            self.ai_package,
                            self._ensure_scene_cards(),
                            self.render_blocks,
                        )
                        with open(pkg_file, "w", encoding="utf-8") as wf:
                            json.dump(self.ai_package, wf, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                if self.ai_package:
                    try:
                        self.ai_package, market_report = self._annotate_market_readiness(
                            self.ai_package,
                            self.render_blocks,
                            self.ai_package.get("context_coverage_report"),
                            self.ai_package.get("visual_scene_evidence_report"),
                        )
                        with open(pkg_file, "w", encoding="utf-8") as wf:
                            json.dump(self.ai_package, wf, indent=2, ensure_ascii=False)
                        if self._market_strict() and not market_report.get("ready"):
                            self._log("   AI package cache không đạt market readiness -> sinh lại")
                            self.ai_package = {}
                    except Exception as e:
                        self._log(f"   ⚠️  Không kiểm định được AI package cache: {e}")
                if not self.ai_package:
                    raise ValueError("stale_ai_package")
                n_blocks = len(self.ai_package.get("script_blocks", []))
                self._resume_skip("AI_FULL", f"{n_blocks} script blocks")
                return True
            except Exception:
                pass

        self._step_start("AI_FULL")
        try:
            from core.ai_engine import AIEngine
            from core.premium_pipeline import PremiumReviewPipeline

            # ── RECAP2.0 DESIGN: AI_FULL dùng TOÀN BỘ scenes/SRT gốc ────────
            # AI cần hiểu toàn phim trước, CLIP_FIND mới chọn cảnh đắt nhất sau.
            # max_video_minutes chỉ là target duration review — KHÔNG cắt nội dung
            # trước khi AI hiểu phim. Nếu AI chỉ nhận một phần phim thì
            # không thể gọi là review phim hoàn chỉnh.
            #
            # planned_duration = duration mục tiêu review (bao nhiêu phút output)
            # full_source_duration = toàn bộ phim gốc (AI dùng để hiểu toàn cảnh)
            full_source_duration = float(self.metadata.get("duration_s") or 0)
            planned_duration = (self.max_video_minutes * 60) if self.max_video_minutes else full_source_duration

            # Xây render_blocks từ TOÀN BỘ scenes gốc (không giới hạn max_video_minutes)
            # CLIP_FIND sẽ filter/chọn cảnh fit target_duration sau
            render_blocks = self.render_blocks
            if not render_blocks and self.scenes:
                # Dùng toàn bộ scenes gốc — không cắt theo max_video_minutes ở đây
                render_blocks = self._build_render_blocks_from_full_scenes()
                self._log(f"   📹 AI_FULL dùng toàn phim: {len(render_blocks)} scenes ({full_source_duration:.0f}s)")
            elif render_blocks and full_source_duration > 0:
                # Kiểm tra render_blocks có cover đủ phim không
                render_blocks = self._ensure_render_blocks_cover_source(
                    render_blocks,
                    full_source_duration,
                    label="AI_FULL input",
                )

            # target_duration cho AI = toàn phim (để viết kịch bản đủ)
            # nhưng target_duration cho TTS/voice = planned_duration (review ngắn hơn)
            ai_target_duration = full_source_duration or planned_duration
            target_duration = planned_duration  # dùng cho tính target_words voice
            render_blocks = self._ensure_render_blocks_cover_source(
                render_blocks,
                full_source_duration,
                label="trước khi gom beat",
            )
            # User-selected review length controls beat density/block count.
            # Full source duration still controls coverage, so the recap keeps
            # beginning-middle-ending of the whole film instead of only a slice.
            render_blocks = self._compact_render_blocks_for_ai(render_blocks, target_duration)
            render_blocks = self._ensure_render_blocks_cover_source(
                render_blocks,
                full_source_duration,
                label="sau khi gom beat",
            )
            render_blocks = self._split_long_render_blocks(render_blocks, self._target_recap2_beat_seconds(target_duration) if self._recap2_beat_mode_enabled() else 0)
            render_blocks = self._ensure_render_blocks_cover_source(
                render_blocks,
                full_source_duration,
                label="sau khi chia block",
            )
            render_timeline = self._render_blocks_timeline_end(render_blocks)
            if render_timeline > 0:
                ai_target_duration = min(float(ai_target_duration or render_timeline), float(render_timeline))

            # ── SCENE MERGE (RECAP2.0 upgrade) ──────────────────────────────
            # Gom cảnh ngắn liền kề trước khi đưa vào AI để giảm số block,
            # mỗi block có đủ thoại làm grounding context.
            # Disabled: render_blocks already captured stable scene IDs above.
            # Merging self.scenes here invalidates later clip references.
            scene_merge_enabled = False
            if scene_merge_enabled and self.scenes and len(self.scenes) > 30:
                try:
                    from core.recap_engine import merge_scenes_to_duration_cap
                    merged_scenes = merge_scenes_to_duration_cap(self.scenes, max_sec=25.0)
                    if len(merged_scenes) < len(self.scenes):
                        self._log(f"   🔗 Scene merge: {len(self.scenes)} → {len(merged_scenes)} scenes")
                        self.scenes = merged_scenes
                except Exception as sm_e:
                    self._log(f"   ⚠️ Scene merge skip: {sm_e}")
            # ── END SCENE MERGE ──────────────────────────────────────────────

            # Allocate the selected review duration across the whole source.
            # RECAP2 uses 640 non-space Vietnamese characters/minute. With an
            # average four characters per whitespace token this is about 2.67
            # words/second. Keep target_words as a prompt hint only; the
            # authoritative map-reduce budget remains CHARS_PER_MINUTE.
            default_wps = "2.67"
            WORDS_PER_SEC = float(os.environ.get("AUTORECAP_REVIEW_WORDS_PER_SEC", default_wps) or default_wps)
            MIN_WORDS_PER_BLOCK = 10
            MAX_WORDS_PER_BLOCK = int(os.environ.get("AUTORECAP_MAX_WORDS_PER_BLOCK", "260") or "260")

            source_budget_seconds = sum(
                max(0.1, float(rb.get("duration") or 0.0))
                for rb in render_blocks
                if isinstance(rb, dict)
            ) or max(0.1, float(ai_target_duration or target_duration or 1.0))

            for rb in render_blocks:
                dur = float(rb.get("duration") or 0.0)
                if dur > 0:
                    voice_target = max(1.0, float(target_duration or 0.0) * dur / source_budget_seconds)
                    target = int(round(voice_target * WORDS_PER_SEC))
                    target = max(MIN_WORDS_PER_BLOCK, min(MAX_WORDS_PER_BLOCK, target))
                    rb["target_words"] = target
                    rb["target_voice_duration_seconds"] = round(voice_target, 3)
                elif not rb.get("target_words"):
                    rb["target_words"] = MIN_WORDS_PER_BLOCK

            self._log(
                f"   📚 {len(render_blocks)} render blocks | "
                f"toàn phim: {ai_target_duration:.1f}s | target review: {target_duration:.1f}s | "
                f"~{int(target_duration * WORDS_PER_SEC)} từ target"
            )

            # Build context
            subtitle_context = ""
            timed_subtitles = ""
            source_subtitles = []
            if self.transcript_srt and os.path.exists(self.transcript_srt):
                try:
                    content = open(self.transcript_srt, encoding="utf-8").read()
                    subtitle_context = content[:3000]
                    timed_subtitles = content[:5000]
                    from core.srt_processor import SRTParser
                    source_subtitles = SRTParser.parse_srt(self.transcript_srt)
                except Exception:
                    pass

            scene_cards_for_context = self._ensure_scene_cards()
            render_blocks = PremiumReviewPipeline.enrich_render_blocks_with_context(
                render_blocks,
                source_subtitles,
                scene_cards_for_context,
            )
            srt_triangulation_report = {}
            srt_lock_context = ""
            try:
                from core.srt_alignment import SrtAlignmentValidator
                render_blocks, srt_triangulation_report = SrtAlignmentValidator.enrich_render_blocks(
                    render_blocks,
                    source_subtitles,
                )
                srt_lock_context = SrtAlignmentValidator.build_prompt_context(
                    render_blocks,
                    srt_triangulation_report,
                )
                self._write_srt_triangulation_report(srt_triangulation_report)
                self._log(
                    "   [SRT_TRIANGULATION] "
                    f"source {srt_triangulation_report.get('source_srt_block_count', 0)}/"
                    f"{srt_triangulation_report.get('block_count', 0)} | "
                    f"cut {srt_triangulation_report.get('cut_srt_block_count', 0)}/"
                    f"{srt_triangulation_report.get('block_count', 0)}"
                )
                # Re-run context enrichment after triangulation so coverage/evidence gate
                # sees source_srt_anchor/cut_visible_srt before deciding to fail.
                render_blocks = PremiumReviewPipeline.enrich_render_blocks_with_context(
                    render_blocks,
                    source_subtitles,
                    scene_cards_for_context,
                )
            except Exception as e:
                self._log(f"   [SRT_TRIANGULATION] Bo qua truoc evidence gate: {e}")
            context_coverage_report = PremiumReviewPipeline.render_context_coverage(render_blocks)

            visual_evidence_report = {}
            weak_context_count = int(context_coverage_report.get("weak_context_count", 0) or 0)
            visual_coverage_ratio = float(context_coverage_report.get("visual_coverage_ratio", 0.0) or 0.0)
            force_vision_on_warn = str(os.environ.get("AUTORECAP_VISION_ON_WARN", "1") or "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            needs_visual_evidence = (
                context_coverage_report.get("status") == "poor"
                or float(context_coverage_report.get("evidence_coverage_ratio", 0.0) or 0.0) < 0.60
                or (force_vision_on_warn and weak_context_count > 0)
                or (force_vision_on_warn and visual_coverage_ratio < 0.95)
            )
            vision_disabled = str(os.environ.get("AUTORECAP_DISABLE_VISION", "") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if needs_visual_evidence and vision_disabled:
                self._log("   👁️  Bỏ qua Vision vì AUTORECAP_DISABLE_VISION đang bật.")
            if needs_visual_evidence and not vision_disabled and not self.gemini_api_key:
                self._log("   👁️  Cần Gemini/OpenRouter key cho Vision; text vẫn dùng Gemini Web.")
            if needs_visual_evidence and not vision_disabled and not scene_cards_for_context:
                self._log("   👁️  Không có keyframe/scene_cards nên chưa chạy được Vision.")
            if needs_visual_evidence and not vision_disabled and scene_cards_for_context and self.gemini_api_key:
                try:
                    from core.visual_scene_evidence import VisualSceneEvidence

                    visual_evidence_path = os.path.join(self.output_dir, "visual_scene_evidence.json")

                    # Reuse kết quả vision từ lần chạy trước (retry không gọi lại Vision)
                    _cached_vision_report = None
                    if os.path.exists(visual_evidence_path):
                        try:
                            with open(visual_evidence_path, encoding="utf-8") as _vf:
                                _cached = json.load(_vf)
                            if _cached.get("ok") and _cached.get("item_count", 0) > 0:
                                _cached_vision_report = _cached
                                self._log(
                                    f"   👁️  Dùng lại vision cache từ lần trước: "
                                    f"{_cached.get('item_count', 0)} block đã phân tích"
                                )
                        except Exception:
                            pass

                    if _cached_vision_report is not None:
                        visual_evidence_report = _cached_vision_report
                    else:
                        try:
                            vision_max_blocks = int(os.environ.get("AUTORECAP_VISION_MAX_BLOCKS", "36") or "36")
                        except Exception:
                            vision_max_blocks = 36
                        try:
                            vision_batch_size = int(os.environ.get("AUTORECAP_VISION_BATCH_SIZE", "6") or "6")
                        except Exception:
                            vision_batch_size = 6
                        try:
                            vision_max_seconds = float(os.environ.get("AUTORECAP_VISION_MAX_SECONDS", "240") or "240")
                        except Exception:
                            vision_max_seconds = 480.0
                        vision_max_blocks = max(8, min(vision_max_blocks, len(render_blocks) or vision_max_blocks))
                        vision_batch_size = max(1, min(vision_batch_size, 10))
                        vision_max_seconds = max(60.0, vision_max_seconds)
                        self._log(
                            "   👁️  Context/visual còn yếu -> gọi Gemini/OpenRouter Vision: "
                            f"weak {weak_context_count} | visual {visual_coverage_ratio:.2f} | "
                            f"tối đa {vision_max_blocks} block | batch {vision_batch_size} | "
                            f"timeout {int(vision_max_seconds)}s"
                        )
                        visual_evidence_report = VisualSceneEvidence.analyze_render_blocks(
                        render_blocks,
                        scene_cards_for_context,
                        self.gemini_api_key,
                        visual_evidence_path,
                        progress_callback=lambda msg: self._log(f"   👁️  {msg}"),
                        max_blocks=vision_max_blocks,
                        batch_size=vision_batch_size,
                        max_seconds=vision_max_seconds,
                    )
                    if visual_evidence_report.get("ok"):
                        render_blocks = VisualSceneEvidence.merge_into_render_blocks(
                            render_blocks,
                            visual_evidence_report,
                        )
                        render_blocks = PremiumReviewPipeline.enrich_render_blocks_with_context(
                            render_blocks,
                            source_subtitles,
                            scene_cards_for_context,
                        )
                        context_coverage_report = PremiumReviewPipeline.render_context_coverage(render_blocks)
                        self._log(
                            "   👁️  Visual evidence: "
                            f"{visual_evidence_report.get('item_count', 0)}/"
                            f"{visual_evidence_report.get('target_count', 0)} block | "
                            f"{visual_evidence_report.get('duration_seconds', 0)}s"
                        )
                    else:
                        self._log(
                            "   ⚠️  Visual evidence không tạo được: "
                            f"{visual_evidence_report.get('error') or visual_evidence_report.get('errors')}"
                        )
                except Exception as e:
                    self._log(f"   ⚠️  Bỏ qua visual evidence: {e}")

            self.render_blocks = render_blocks
            try:
                with open(os.path.join(self.output_dir, "render_blocks.json"), "w", encoding="utf-8") as f:
                    json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            self._log(
                "   🧭 Context coverage: "
                f"SRT {context_coverage_report.get('with_srt_anchor', 0)}/{context_coverage_report.get('book_count', 0)} | "
                f"visual {context_coverage_report.get('with_visual_anchor', 0)}/{context_coverage_report.get('book_count', 0)} | "
                f"weak {context_coverage_report.get('weak_context_count', 0)} | "
                f"{context_coverage_report.get('status')}"
            )
            if context_coverage_report.get("status") == "poor":
                self._log(
                    "   ⚠️  SRT/SceneCard coverage yeu: kịch bản có nguy cơ không sát video băm. "
                    "Nên dùng SRT nguồn đầy đủ/đã dịch hoặc chạy lại Subtitle Map/SceneCards."
                )
            evidence_gate_report = self._enforce_evidence_gate(
                context_coverage_report,
                visual_evidence_report,
                source_subtitles,
                render_blocks,
                target_duration,
            )

            # Build block_context: map SRT gốc vào từng block theo original_start/original_end
            # QUAN TRỌNG: dùng original_start/original_end để tìm thoại trong SRT gốc
            # Voice placement dùng start_in_final_video (timeline video băm)
            block_context = ""
            if render_blocks and self.transcript_srt and os.path.exists(self.transcript_srt):
                try:
                    subs = source_subtitles
                    block_lines = [
                        "CONTEXT_COVERAGE: "
                        f"SRT={context_coverage_report.get('with_srt_anchor', 0)}/{context_coverage_report.get('book_count', 0)}, "
                        f"VISUAL={context_coverage_report.get('with_visual_anchor', 0)}/{context_coverage_report.get('book_count', 0)}, "
                        f"WEAK={context_coverage_report.get('weak_context_count', 0)}, "
                        f"STATUS={context_coverage_report.get('status')}"
                    ]
                    for rb in render_blocks:
                        bid = rb.get("block_id", "?")
                        # Timeline trong video BĂM (để map voice)
                        final_start = float(rb.get("start_in_final_video") or 0)
                        final_end   = float(rb.get("end_in_final_video") or final_start + float(rb.get("duration") or 4))
                        # Timeline trong video GỐC (để map SRT — đây là key fix)
                        orig_start  = float(rb.get("original_start") or final_start)
                        orig_end    = float(rb.get("original_end") or final_end)
                        tw = rb.get("target_words", 25)
                        scene_role = rb.get("scene_role_label") or rb.get("scene_role") or ""
                        semantic_dialogue = str(rb.get("dialogue_text") or rb.get("srt_anchor") or "").replace("\n", " ")
                        visual_anchor = str(rb.get("visual_anchor") or rb.get("visual_hint") or "").replace("\n", " ")
                        visual_notes = str(rb.get("visual_notes") or "").replace("\n", " ")
                        visual_source = str(rb.get("visual_evidence_source") or "").replace("\n", " ")
                        scene_ids = rb.get("scene_ids") or ([rb.get("scene_id")] if rb.get("scene_id") is not None else [])
                        must_mention = PremiumReviewPipeline._anchor_phrase(
                            rb.get("must_mention"),
                            visual_anchor,
                            visual_notes,
                            semantic_dialogue,
                            rb.get("cut_reason"),
                            limit=180,
                        )

                        # Lấy thoại SRT theo timestamp video GỐC
                        matching_subs = [
                            s for s in subs
                            if float(s.get("end_seconds", s.get("start_seconds", 0)) or 0) >= orig_start - 0.5
                            and float(s.get("start_seconds", 0) or 0) <= orig_end + 0.5
                        ]
                        dialogue = " | ".join(
                            s.get("text", "").replace("\n", " ")
                            for s in matching_subs[:3]
                        )[:250]
                        if not dialogue and semantic_dialogue:
                            dialogue = semantic_dialogue[:250]
                        if not dialogue and rb.get("source_srt_anchor"):
                            dialogue = str(rb.get("source_srt_anchor"))[:250]

                        block_lines.append(
                            f"Block {bid} | video_bam: {final_start:.1f}→{final_end:.1f}s"
                            f" ({final_end-final_start:.1f}s, {tw} từ) | cảnh: {scene_role}"
                            f"\n  Scene_ids: {scene_ids or '[unknown]'}"
                            f"\n  Thoại_gốc_SRT ({orig_start:.1f}→{orig_end:.1f}s): {dialogue or '[không có thoại]'}"
                            f"\n  Semantic_dialogue: {semantic_dialogue[:320] or '[không có]'}"
                            f"\n  Visual_anchor: {visual_anchor[:220] or '[không có]'}"
                            f"\n  Visual_notes: {visual_notes[:260] or '[không có]'}"
                            f"\n  Visual_source: {visual_source[:80] or '[không có]'}"
                            f"\n  MUST_MENTION: {must_mention[:180] or '[không có]'}"
                        )
                    block_context = "\n".join(block_lines)
                    self._log(f"   🗺️  Block context: {len(render_blocks)} blocks | SRT mapping theo timestamp gốc")
                except Exception as e:
                    self._log(f"   ⚠️  Không build được block_context: {e}")
                    block_context = ""

            if srt_lock_context:
                block_context = (srt_lock_context + "\n\n" + block_context).strip()

            visual_context = ""
            try:
                outline = self._ensure_story_outline()
                if outline:
                    from core.transcript_story import TranscriptFirstStoryPlanner
                    visual_context = TranscriptFirstStoryPlanner.to_prompt_context(outline)
                if self.scene_cards:
                    top_cards = sorted(
                        self.scene_cards,
                        key=lambda item: float(item.get("importance_score", 0.0) or 0.0),
                        reverse=True,
                    )[:12]
                    card_lines = []
                    for card in sorted(top_cards, key=lambda item: float(item.get("start_s", 0.0) or 0.0)):
                        card_start = float(card.get("start_s", 0.0) or 0.0)
                        card_end = float(card.get("end_s", card_start) or card_start)
                        card_lines.append(
                            f"Scene {card.get('scene_id')} {card_start:.1f}-{card_end:.1f}s "
                            f"score={card.get('importance_score', 0)}: "
                            f"{str(card.get('dialogue_text', ''))[:180]}"
                        )
                    if card_lines:
                        visual_context = (visual_context + "\n\nTOP SCENE CARDS:\n" + "\n".join(card_lines)).strip()
            except Exception as e:
                self._log(f"   ⚠️  Không build được story visual_context: {e}")

            ai = AIEngine(api_key=self.gemini_api_key)

            # ── MAP-REDUCE RECAP ENGINE (RECAP2.0 upgrade) ──────────────────
            # Thử map-reduce trước (outline → budget → per-chapter) để cover TOÀN phim.
            # Nếu thất bại (< 3 blocks), fallback sang generate_review_package cũ.
            map_reduce_script_blocks: list = []
            map_reduce_enabled = str(os.environ.get("AUTORECAP_MAP_REDUCE", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
            map_reduce_duration = self._render_blocks_timeline_end(render_blocks) or target_duration
            target_minutes = max(1.0, float(target_duration or map_reduce_duration or 0) / 60.0)

            if map_reduce_enabled and len(render_blocks) >= 4 and target_minutes >= 2.0:
                try:
                    from core.recap_engine import generate_map_reduce_recap

                    def _ai_call_sync(prompt: str) -> str:
                        return ai._try_generate(prompt)

                    _fallback_key = ""
                    self._log(
                        f"   🗺️  Map-Reduce Recap Engine: {len(render_blocks)} blocks | "
                        f"review {target_minutes:.1f} phút | nguồn {float(map_reduce_duration or 0) / 60.0:.1f} phút"
                    )
                    map_reduce_script_blocks = generate_map_reduce_recap(
                        render_blocks=render_blocks,
                        target_minutes=target_minutes,
                        ai_call=_ai_call_sync,
                        movie_title=self.movie_title or "Phim",
                        reference_text=(self.movie_description or "") + "\n" + subtitle_context[:2000],
                        transcript_srt_path=self.transcript_srt or "",
                        ollama_api_key=_fallback_key,
                        log=self._log,
                    )
                    if len(map_reduce_script_blocks) >= 3:
                        if any(isinstance(b, dict) and b.get("one_shot_gemini") for b in map_reduce_script_blocks):
                            batch_count = max(
                                int(b.get("gemini_batch_count") or 1)
                                for b in map_reduce_script_blocks
                                if isinstance(b, dict)
                            )
                            self._log(f"   ✅ Gemini batched {batch_count} lượt tạo {len(map_reduce_script_blocks)} blocks → dùng trực tiếp")
                        else:
                            self._log(f"   ✅ Map-Reduce tạo {len(map_reduce_script_blocks)} blocks → dùng làm seed cho generate_review_package")
                    else:
                        self._log(f"   ⚠️ Map-Reduce cho {len(map_reduce_script_blocks)} blocks (< 3) → fallback")
                        map_reduce_script_blocks = []
                except Exception as mr_e:
                    self._log(f"   ⚠️ Map-Reduce lỗi: {mr_e} → fallback sang single-shot")
                    map_reduce_script_blocks = []

            # Nếu map-reduce thành công, đưa kết quả vào context cho generate_review_package
            # để nó tinh chỉnh sync/quality thay vì tạo từ đầu
            mr_context_hint = ""
            if map_reduce_script_blocks:
                mr_lines = []
                for blk in map_reduce_script_blocks[:len(render_blocks)]:
                    mr_lines.append(f"[Block {blk.get('block_id', '?')}] {str(blk.get('text', ''))[:300]}")
                mr_context_hint = "MAP_REDUCE_DRAFT (dùng làm nền, tinh chỉnh sync/quality):\n" + "\n".join(mr_lines)

            def _package_from_script_blocks(blocks, direct_reason: str = "map_reduce"):
                _rb_map = {}
                for _idx, _rb in enumerate(render_blocks, 1):
                    if isinstance(_rb, dict):
                        for _k in (_rb.get("block_id"), _rb.get("book_id"), _idx):
                            try:
                                _rb_map[int(_k)] = _rb
                            except Exception:
                                pass
                _enriched = []
                for _blk in blocks:
                    _b = dict(_blk)
                    try:
                        _bid = int(_b.get("block_id") or 0)
                    except Exception:
                        _bid = 0
                    _rb = _rb_map.get(_bid) or {}
                    for _field in ("start_in_final_video", "end_in_final_video", "duration",
                                   "original_start", "original_end", "smart_score",
                                   "target_words", "srt_anchor", "visual_anchor"):
                        if _field in _rb and _field not in _b:
                            _b[_field] = _rb[_field]
                    _strategy = str(_b.get("script_block_strategy") or "").strip()
                    if not _strategy:
                        _strategy = "recap2_beat_first" if self._recap2_beat_mode_enabled() else "scene_block"
                    _b["script_block_strategy"] = _strategy
                    _b["recap2_beat_mode"] = bool(self._recap2_beat_mode_enabled() or _strategy == "chapter_budget_story_segment")
                    _enriched.append(_b)
                _tmp_package = {"script_blocks": _enriched}
                _tmp_package = self._attach_review_clips_to_package(_tmp_package, render_blocks)
                _enriched = _tmp_package.get("script_blocks", _enriched)

                package_strategy = "chapter_budget_story_segment" if any(
                    str(b.get("script_block_strategy") or "") == "chapter_budget_story_segment"
                    for b in _enriched
                ) else ("recap2_beat_first" if self._recap2_beat_mode_enabled() else "scene_block")
                full_script = "\n\n".join(
                    str(b.get("text", "")) for b in _enriched if b.get("text")
                ).strip()
                return {
                    "script": full_script,
                    "script_blocks": _enriched,
                    "subtitle_chunks": [str(b.get("text", "")) for b in _enriched if b.get("text")],
                    "render_blocks": render_blocks,
                    "book_map": render_blocks,
                    "summary": "",
                    "map_reduce_direct": True,
                    "map_reduce_used": True,
                    "map_reduce_block_count": len(_enriched),
                    "one_shot_gemini": direct_reason == "one_shot",
                    "script_block_strategy": package_strategy,
                    "recap2_beat_mode": bool(self._recap2_beat_mode_enabled() or package_strategy == "chapter_budget_story_segment"),
                    "raw_render_block_count": int((render_blocks[0].get("raw_render_block_count") if render_blocks and isinstance(render_blocks[0], dict) else 0) or len(render_blocks)),
                    "beat_target_seconds": self._target_recap2_beat_seconds(target_duration) if self._recap2_beat_mode_enabled() else 0,
                    "planned_duration_seconds": round(float(target_duration or 0), 3),
                    "review_duration_budget_version": 4,
                    "target_review_seconds": round(float(target_duration or 0), 3),
                    "target_review_minutes": round(float(target_duration or 0) / 60.0, 3) if target_duration else None,
                    "target_review_label": (
                        f"{round(float(target_duration) / 60.0, 2)} phút" if target_duration else "20 phút"
                    ),
                    "ai_source_duration_seconds": round(float(ai_target_duration or 0), 3),
                    "source_full_duration_seconds": round(float(full_source_duration or 0), 3),
                    "map_reduce_duration_seconds": round(float(map_reduce_duration or target_duration or 0), 3),
                    "provider_used": getattr(ai, "last_provider_used", "gemini"),
                    "fallback_used": getattr(ai, "last_fallback_used", False),
                    "ai_quota_fallback_used": False,
                    "voice_narration_style": self.review_style,
                    "review_style_profile": self.review_style,
                }
            # ── END MAP-REDUCE ───────────────────────────────────────────────

            # Thử generate_review_package để tinh chỉnh sync/quality.
            # Nếu Gemini vẫn quota và map-reduce đã có đủ blocks → dùng trực tiếp map-reduce.
            _one_shot_direct = bool(
                map_reduce_script_blocks
                and any(isinstance(b, dict) and b.get("one_shot_gemini") for b in map_reduce_script_blocks)
            )
            _chapter_budget_direct = bool(
                map_reduce_script_blocks
                and any(
                    isinstance(b, dict)
                    and str(b.get("script_block_strategy") or "") == "chapter_budget_story_segment"
                    for b in map_reduce_script_blocks
                )
            )
            _use_map_reduce_direct = _one_shot_direct or _chapter_budget_direct
            if _chapter_budget_direct and not _one_shot_direct:
                self._log(f"   ✅ Dùng chapter-budget story segments trực tiếp ({len(map_reduce_script_blocks)} segments) → không gọi Gemini lần 2")
                package = _package_from_script_blocks(map_reduce_script_blocks, direct_reason="chapter_budget")
                map_reduce_script_blocks = package.get("script_blocks", map_reduce_script_blocks)
            elif _one_shot_direct:
                batch_count = max(
                    int(b.get("gemini_batch_count") or 1)
                    for b in map_reduce_script_blocks
                    if isinstance(b, dict)
                )
                self._log(f"   ✅ Dùng Gemini batched {batch_count} lượt trực tiếp ({len(map_reduce_script_blocks)} blocks) → không gọi Gemini lần 2")
                package = _package_from_script_blocks(map_reduce_script_blocks, direct_reason="one_shot")
                map_reduce_script_blocks = package.get("script_blocks", map_reduce_script_blocks)
            else:
                try:
                    package = ai.generate_review_package(
                        movie_name=self.movie_title or "Phim",
                        movie_description=self.movie_description or "",
                        subtitle_context=subtitle_context,
                        timed_subtitles=timed_subtitles,
                        target_duration_seconds=target_duration,
                        render_blocks=render_blocks,
                        block_context=(block_context + "\n\n" + mr_context_hint).strip() if mr_context_hint else block_context,
                        visual_context=visual_context,
                    )
                except Exception as gen_exc:
                    # Nếu map-reduce đã tạo đủ blocks → dùng trực tiếp, không fail pipeline
                    if map_reduce_script_blocks and len(map_reduce_script_blocks) >= 3 and ai._is_quota_error(gen_exc):
                        self._log(f"   ⚠️ generate_review_package quota → dùng map-reduce blocks trực tiếp ({len(map_reduce_script_blocks)} blocks)")
                        _use_map_reduce_direct = True
                        package = _package_from_script_blocks(map_reduce_script_blocks, direct_reason="quota")
                        map_reduce_script_blocks = package.get("script_blocks", map_reduce_script_blocks)
                    else:
                        raise

            if map_reduce_script_blocks and not _use_map_reduce_direct:
                package["map_reduce_used"] = True
                package["map_reduce_block_count"] = len(map_reduce_script_blocks)
            if not package.get("script_block_strategy"):
                package["script_block_strategy"] = "recap2_beat_first" if self._recap2_beat_mode_enabled() else "scene_block"
            if any(
                isinstance(_b, dict) and str(_b.get("script_block_strategy") or "") == "chapter_budget_story_segment"
                for _b in (package.get("script_blocks") or [])
            ):
                package["script_block_strategy"] = "chapter_budget_story_segment"
            package["recap2_beat_mode"] = bool(self._recap2_beat_mode_enabled() or package.get("script_block_strategy") == "chapter_budget_story_segment")
            package["raw_render_block_count"] = int((render_blocks[0].get("raw_render_block_count") if render_blocks and isinstance(render_blocks[0], dict) else 0) or len(render_blocks))
            package["beat_target_seconds"] = self._target_recap2_beat_seconds(target_duration) if self._recap2_beat_mode_enabled() else 0
            package["planned_duration_seconds"] = round(float(target_duration or 0), 3)
            package["review_duration_budget_version"] = 4
            package["target_review_seconds"] = round(float(target_duration or 0), 3)
            package["target_review_minutes"] = round(float(target_duration or 0) / 60.0, 3) if target_duration else None
            package["target_review_label"] = (
                f"{round(float(target_duration) / 60.0, 2)} phút" if target_duration else "20 phút"
            )
            package["ai_source_duration_seconds"] = round(float(ai_target_duration or 0), 3)
            package["source_full_duration_seconds"] = round(float(full_source_duration or 0), 3)
            package["map_reduce_duration_seconds"] = round(float(map_reduce_duration or target_duration or 0), 3)
            for _block in package.get("script_blocks") or []:
                if isinstance(_block, dict):
                    _block["script_block_strategy"] = package["script_block_strategy"]
                    _block["recap2_beat_mode"] = package["recap2_beat_mode"]
            if package.get("script_block_strategy") == "chapter_budget_story_segment":
                from core.recap_engine import restore_story_segment_coverage
                coverage = restore_story_segment_coverage(
                    package.get("script_blocks") or [], render_blocks,
                )
                package["story_source_coverage"] = coverage
                repaired_ids = coverage.get("repaired_source_ids") or []
                if repaired_ids:
                    self._log(
                        "   🧭 Khôi phục coverage mở đầu/giữa/cuối: "
                        + ", ".join(str(value) for value in repaired_ids[:20])
                    )
            package = self._attach_review_clips_to_package(package, render_blocks)
            package["voice_narration_style"] = self.review_style
            package["review_style_profile"] = self.review_style
            package["context_coverage_report"] = context_coverage_report
            package["evidence_gate_report"] = evidence_gate_report
            if srt_triangulation_report:
                package["srt_triangulation_report"] = srt_triangulation_report
            if visual_evidence_report:
                package["visual_scene_evidence_report"] = visual_evidence_report

            try:
                package, srt_alignment_report = self._annotate_srt_alignment(
                    package,
                    render_blocks,
                )
                if srt_alignment_report.get("error_count"):
                    self._log(f"   [SRT_ALIGNMENT] {srt_alignment_report.get('error_count')} lỗi nghiêm trọng")
            except Exception as e:
                self._log(f"   [SRT_ALIGNMENT] Bo qua: {e}")

            try:
                from core.scene_mapping_validator import SceneMappingValidator
                package, mapping_report = SceneMappingValidator.annotate_package(
                    package,
                    self._ensure_scene_cards(),
                    render_blocks,
                )
                if mapping_report.get("issue_count"):
                    self._log(f"   ⚠️  SceneMappingValidator: {mapping_report.get('issue_count')} cảnh báo")
            except Exception as e:
                self._log(f"   ⚠️  Bo qua SceneMappingValidator: {e}")

            package, market_report = self._annotate_market_readiness(
                package,
                render_blocks,
                context_coverage_report,
                visual_evidence_report,
            )
            self.ai_package = package
            self.render_blocks = render_blocks

            with open(pkg_file, "w", encoding="utf-8") as f:
                json.dump(package, f, indent=2, ensure_ascii=False)

            # Khi dùng map-reduce direct (Gemini quota) → bỏ qua strict market check
            # vì package đã có đủ blocks để tạo voice/render
            script_blocks = package.get("script_blocks", [])

            if _use_map_reduce_direct:
                self._log("   ℹ️  Map-reduce direct: bỏ qua strict market check")
            elif self._market_strict() and not market_report.get("ready"):
                if not script_blocks:
                    raise RuntimeError(self._market_fail_message(market_report))
                error_types = [
                    str(item.get("type") or "")
                    for item in (market_report.get("issues") or [])
                    if isinstance(item, dict) and item.get("level") == "ERROR"
                ]
                package["script_editor_required"] = True
                package["script_editor_required_reason"] = self._market_fail_message(market_report)
                package["script_editor_required_issue_types"] = error_types
                package["market_readiness_failed_before_editor"] = True
                self.ai_package = package
                with open(pkg_file, "w", encoding="utf-8") as f:
                    json.dump(package, f, indent=2, ensure_ascii=False)
                self._log("   ⚠️  Market readiness FAIL nhưng đã có kịch bản -> mở Script Editor để sửa bám cảnh/alignment")
            total_words = sum(len(b.get("text","").split()) for b in script_blocks)
            self._step_done(
                "AI_FULL",
                f"{len(script_blocks)} blocks / {total_words} từ → {pkg_file}"
            )
            return True

        except Exception as e:
            self._step_fail("AI_FULL", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 7: CLIP_FIND
    # ──────────────────────────────────────────────────────────────

    def step_clip_find(self) -> bool:
        """Lựa chọn và cắt các cảnh phim đắt giá nhất."""
        cut_output = os.path.join(self.output_dir, "cut_video.mp4")
        render_blocks_file = os.path.join(self.output_dir, "render_blocks.json")
        pkg_file = os.path.join(self.output_dir, "ai_package.json")
        force_recut_after_ai = False
        try:
            force_recut_after_ai = bool(
                self.render_blocks
                and os.path.exists(cut_output)
                and os.path.exists(pkg_file)
                and os.path.getmtime(pkg_file) > os.path.getmtime(cut_output) + 1.0
            )
        except Exception:
            force_recut_after_ai = False

        # Resume: nếu cut_video.mp4 đã tồn tại
        if self._cached(cut_output) and not force_recut_after_ai:
            try:
                from core.video_cutter import VideoCutter
                # Lấy duration từ file đã có
                import subprocess as _sp
                try:
                    from utils.helpers import FFmpegUtils
                    _ffprobe = FFmpegUtils.ffprobe_executable()
                except Exception:
                    import shutil; _ffprobe = shutil.which("ffprobe") or "ffprobe"
                _r = _sp.run(
                    [_ffprobe, "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", cut_output],
                    capture_output=True, text=True
                )
                duration = float(_r.stdout.strip()) if _r.stdout.strip() else 0.0
                if (
                    self._recap2_beat_mode_enabled()
                    and self.ai_package.get("script_blocks")
                    and (self.ai_package.get("cut_video_policy") or {}).get("mode") != "review_clip_preview"
                ):
                    self._log("   cut_video cache chưa theo review_clip preview -> cắt lại")
                    raise RuntimeError("recut review_clip preview")
                if (
                    self._recap2_beat_mode_enabled()
                    and self.max_video_minutes
                ):
                    target_preview = self.max_video_minutes * 60.0
                    if duration > target_preview * 1.08 or duration < target_preview * 0.75:
                        self._log(
                            f"   cut_video cache lệch độ dài review ({duration:.1f}s) so với mục tiêu "
                            f"{self.max_video_minutes} phút -> cắt lại"
                        )
                        raise RuntimeError("recut recap2 preview")
                self.cut_video_path = cut_output
                self.cut_duration = duration
                # Rebuild render_blocks từ cache/pattern nếu chưa có.
                # Tuyệt đối không dùng toàn bộ scene nguồn ở đây: scene timeline
                # có thể dài hơn cut_video và làm AI sinh hàng nghìn block sai.
                if not self.render_blocks and self._cached(render_blocks_file):
                    with open(render_blocks_file, encoding="utf-8") as f:
                        self.render_blocks = json.load(f)
                if (
                    self.render_blocks
                    and duration > 0
                    and self._render_blocks_timeline_end(self.render_blocks) > duration * 1.25
                ):
                    self._log("   render_blocks cache lệch timeline -> rebuild pattern")
                    self.render_blocks = []
                if not self.render_blocks:
                    self.render_blocks = self._rebuild_pattern_render_blocks(duration)
                    if self.render_blocks:
                        self.render_blocks = self._compact_render_blocks_for_ai(self.render_blocks, duration)
                        self.render_blocks = self._split_long_render_blocks(self.render_blocks)
                        try:
                            with open(render_blocks_file, "w", encoding="utf-8") as f:
                                json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                elif self.render_blocks:
                    before_split = len(self.render_blocks)
                    self.render_blocks = self._split_long_render_blocks(self.render_blocks)
                    if len(self.render_blocks) != before_split:
                        try:
                            with open(render_blocks_file, "w", encoding="utf-8") as f:
                                json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                self._ensure_cut_keyframes()
                self._resume_skip("CLIP_FIND", f"{duration:.1f}s")
                return True
            except Exception:
                pass

        if force_recut_after_ai:
            self._log("   CLIP_FIND: AI_FULL moi hon cut_video -> cat lai theo ke hoach story")
        self._step_start("CLIP_FIND")
        try:
            from core.video_cutter import VideoCutter

            max_dur = (self.max_video_minutes * 60) if self.max_video_minutes else None
            success = False
            duration = 0.0
            error = ""
            used_ai_plan = False
            recap2_mode = self._recap2_beat_mode_enabled()

            # AI_FULL chay truoc CLIP_FIND, nen uu tien cat dung theo beat/story da viet.
            # Neu khong co ke hoach AI hop le thi moi fallback sang semantic/pattern cu.
            planned_segments = []
            full_source_duration = float(self.metadata.get("duration_s") or 0.0)
            if recap2_mode and self.ai_package.get("script_blocks"):
                self.ai_package = self._attach_review_clips_to_package(self.ai_package, self.render_blocks)
                try:
                    with open(pkg_file, "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                review_segments = self._review_clip_preview_segments(max_dur)
                if review_segments:
                    try:
                        self._log(
                            f"   RECAP2: tạo cut_video preview từ review_clip "
                            f"({len(review_segments)} segments)"
                        )
                        success, duration, error = VideoCutter.cut_video_with_segments(
                            self.video_path,
                            cut_output,
                            review_segments,
                        )
                        if success:
                            used_ai_plan = True
                            self.ai_package["cut_video_policy"] = {
                                "mode": "review_clip_preview",
                                "segment_count": len(review_segments),
                                "target_duration_seconds": round(float(max_dur or duration or 0), 3),
                                "actual_duration_seconds": round(float(duration or 0), 3),
                            }
                            with open(pkg_file, "w", encoding="utf-8") as f:
                                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        else:
                            self._log(f"   RECAP2 review_clip preview fallback: {error}")
                    except Exception as review_cut_err:
                        self._log(f"   RECAP2 review_clip preview bỏ qua: {review_cut_err}")
            if self.render_blocks and full_source_duration > 0:
                self.render_blocks = self._ensure_render_blocks_cover_source(
                    self.render_blocks,
                    full_source_duration,
                    label="CLIP_FIND input",
                )
            if recap2_mode and self.render_blocks:
                self._log(
                    "   RECAP2: render_blocks là timeline story toàn tập; "
                    "cut_video chỉ cắt preview/scene pool, không cắt nguyên phim"
                )
            if self.render_blocks and not recap2_mode:
                for rb in self.render_blocks:
                    if not isinstance(rb, dict):
                        continue
                    try:
                        s0 = float(
                            rb.get("original_start")
                            if rb.get("original_start") is not None
                            else rb.get("source_start")
                            if rb.get("source_start") is not None
                            else rb.get("start")
                            if rb.get("start") is not None
                            else rb.get("start_in_final_video")
                            or 0.0
                        )
                        e0 = float(
                            rb.get("original_end")
                            if rb.get("original_end") is not None
                            else rb.get("source_end")
                            if rb.get("source_end") is not None
                            else rb.get("end")
                            if rb.get("end") is not None
                            else 0.0
                        )
                        if e0 <= s0:
                            dur0 = float(rb.get("duration") or rb.get("duration_hint_seconds") or 0.0)
                            e0 = s0 + dur0
                    except Exception:
                        continue
                    if e0 > s0 + 0.05:
                        planned_segments.append({
                            "start": round(max(0.0, s0), 3),
                            "end": round(max(0.0, e0), 3),
                            "reason": "ai_story_plan",
                            "score": rb.get("score", 80),
                        })
            if planned_segments and full_source_duration > 0:
                planned_source_end = max(float(seg.get("end") or 0.0) for seg in planned_segments)
                if planned_source_end < full_source_duration - 8.0:
                    self._log(
                        f"   ⚠️ CLIP_FIND plan chưa tới cuối tập: "
                        f"{planned_source_end:.0f}s/{full_source_duration:.0f}s"
                    )
                    repaired_blocks = self._ensure_render_blocks_cover_source(
                        self.render_blocks,
                        full_source_duration,
                        label="CLIP_FIND plan",
                    )
                    if repaired_blocks is not self.render_blocks:
                        self.render_blocks = repaired_blocks
                    planned_segments = []
                    for rb in self.render_blocks:
                        if not isinstance(rb, dict):
                            continue
                        try:
                            s0 = float(
                                rb.get("original_start")
                                if rb.get("original_start") is not None
                                else rb.get("source_start")
                                if rb.get("source_start") is not None
                                else rb.get("start")
                                if rb.get("start") is not None
                                else rb.get("start_in_final_video")
                                or 0.0
                            )
                            e0 = float(
                                rb.get("original_end")
                                if rb.get("original_end") is not None
                                else rb.get("source_end")
                                if rb.get("source_end") is not None
                                else rb.get("end")
                                if rb.get("end") is not None
                                else 0.0
                            )
                            if e0 <= s0:
                                e0 = s0 + float(rb.get("duration") or rb.get("duration_hint_seconds") or 0.0)
                        except Exception:
                            continue
                        if e0 > s0 + 0.05:
                            planned_segments.append({
                                "start": round(max(0.0, s0), 3),
                                "end": round(max(0.0, e0), 3),
                                "reason": "ai_story_plan",
                                "score": rb.get("score", 80),
                            })
            if planned_segments:
                try:
                    self._log(f"   CLIP_FIND dung ke hoach AI_FULL: {len(planned_segments)} canh")
                    success, duration, error = VideoCutter.cut_video_with_segments(
                        self.video_path,
                        cut_output,
                        planned_segments,
                    )
                    if success:
                        used_ai_plan = True
                        # Giu render_blocks da dung de viet kich ban; khong doi timeline sau AI_FULL.
                        self.render_blocks = list(self.render_blocks)
                    else:
                        self._log(f"   CLIP_FIND ke hoach AI fallback: {error}")
                except Exception as plan_err:
                    self._log(f"   CLIP_FIND ke hoach AI bo qua: {plan_err}")
            # Prefer semantic source clips when smart cut is enabled and scene cards
            # are available. Fallback below keeps the old behavior intact.
            if not success and self.smart_cut:
                try:
                    cards = self._ensure_scene_cards()
                    if cards:
                        from core.semantic_clip_finder import SemanticClipFinder
                        from core.calculator import VideoCalculator

                        source_duration = VideoCutter.get_video_duration(self.video_path) or self.metadata.get("duration_s", 0)
                        target_dur = max_dur
                        if not target_dur:
                            target_dur = VideoCutter.calculate_cut_duration(
                                float(source_duration or 0.0), self.keep_seconds, self.skip_seconds
                            )
                        semantic_segments = SemanticClipFinder.find_best_clips(
                            cards,
                            target_duration=target_dur,
                            source_duration=source_duration,
                        )
                        if semantic_segments:
                            self._log(f"   Semantic ClipFinder: {len(semantic_segments)} cảnh ứng viên")
                            success, duration, error = VideoCutter.cut_video_with_segments(
                                self.video_path,
                                cut_output,
                                semantic_segments,
                            )
                            if success:
                                if not recap2_mode:
                                    self.render_blocks = VideoCalculator.get_render_blocks(semantic_segments)
                            else:
                                self._log(f"   Semantic ClipFinder fallback: {error}")
                except Exception as sem_err:
                    self._log(f"   Semantic ClipFinder bo qua: {sem_err}")

            if not success and self.smart_cut:
                success, duration, error, segments = VideoCutter.cut_video_smart(
                    self.video_path,
                    cut_output,
                    keep_seconds=self.keep_seconds,
                    skip_seconds=self.skip_seconds,
                    max_duration_seconds=max_dur,
                    progress_callback=lambda msg: self._log(f"   ✂️  {msg}"),
                    return_segments=True,
                )
                if success and segments:
                    from core.calculator import VideoCalculator
                    if not recap2_mode:
                        self.render_blocks = VideoCalculator.get_render_blocks(segments)
            elif not success:
                # cut_video_with_pattern không trả về segments
                # → tự tính segments từ pattern keep/skip để build render_blocks
                source_duration = VideoCutter.get_video_duration(self.video_path)
                raw_segments = VideoCutter.get_keep_segments(
                    source_duration, self.keep_seconds, self.skip_seconds
                )
                if max_dur:
                    raw_segments = VideoCutter._limit_segments_to_duration(raw_segments, max_dur)

                success, duration, error = VideoCutter.cut_video_with_pattern(
                    self.video_path,
                    cut_output,
                    keep_seconds=self.keep_seconds,
                    skip_seconds=self.skip_seconds,
                    max_duration_seconds=max_dur,
                )
                if success and raw_segments:
                    from core.calculator import VideoCalculator
                    if not recap2_mode:
                        self.render_blocks = VideoCalculator.get_render_blocks(raw_segments)

            if not success:
                self._step_fail("CLIP_FIND", error or "Cắt video thất bại")
                return False

            self.cut_video_path = cut_output
            self.cut_duration = duration
            if self.render_blocks:
                try:
                    raw_render_blocks_file = os.path.join(self.output_dir, "raw_render_blocks.json")
                    with open(raw_render_blocks_file, "w", encoding="utf-8") as f:
                        json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                if not used_ai_plan and not recap2_mode:
                    self.render_blocks = self._compact_render_blocks_for_ai(self.render_blocks, duration)
                    self.render_blocks = self._split_long_render_blocks(self.render_blocks)
            try:
                with open(render_blocks_file, "w", encoding="utf-8") as f:
                    json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            self._log(f"   ✅ {len(self.render_blocks)} render blocks | video băm {duration:.1f}s")
            self._ensure_cut_keyframes()
            self._step_done("CLIP_FIND", f"{duration:.1f}s → {cut_output}")
            return True

        except Exception as e:
            self._step_fail("CLIP_FIND", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 8: VOICE_SEGMENTS
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _smart_match_voice_to_scenes(
        script_blocks: list,
        render_blocks: list,
        transcript_srt: str = "",
    ) -> dict:
        """So sánh nội dung voice review với SRT gốc + smart_score của render_blocks
        để tìm cảnh hay nhất cho từng đoạn voice.

        Trả về dict: block_id → start_in_final_video đã được re-assign.

        Logic ưu tiên:
        1. Cảnh có thoại SRT gốc liên quan đến nội dung voice (keyword overlap)
        2. Cảnh có smart_score cao (cảnh đắt: nhân vật chính, hành động)
        3. Cảnh chưa bị dùng (tránh nhiều voice đọc trên cùng 1 cảnh)
        4. Fallback: giữ thứ tự tuyến tính gốc
        """
        if not script_blocks or not render_blocks:
            return {}

        # Load SRT gốc để lấy thoại theo timestamp
        srt_by_time: list = []
        if transcript_srt and os.path.exists(transcript_srt):
            try:
                from core.srt_processor import SRTParser
                srt_by_time = SRTParser.parse_srt(transcript_srt)
            except Exception:
                pass

        def _keywords(text: str) -> set:
            """Trích xuất từ khóa có nghĩa (bỏ stopwords)."""
            stopwords = {
                "là", "và", "của", "có", "không", "trong", "với", "được",
                "một", "này", "đó", "những", "cho", "từ", "khi", "đã",
                "như", "cũng", "thì", "vì", "nhưng", "mà", "hay", "nếu",
                "rằng", "để", "lên", "ra", "vào", "đến", "qua", "về", "tới",
            }
            words = re.findall(r"\b[\w\u00C0-\u024F\u1E00-\u1EFF]{3,}\b", text.lower())
            return {w for w in words if w not in stopwords}

        def _srt_text_for_block(rb: dict) -> str:
            """Lấy thoại SRT gốc trong khoảng original_start → original_end."""
            orig_s = float(rb.get("original_start") or 0)
            orig_e = float(rb.get("original_end") or orig_s + float(rb.get("duration") or 4))
            texts = [
                s.get("text", "") for s in srt_by_time
                if orig_s - 1.0 <= float(s.get("start_seconds", 0)) <= orig_e + 1.0
            ]
            return " ".join(texts)

        # Build render_block metadata
        rb_pool = []
        for rb in render_blocks:
            if not isinstance(rb, dict):
                continue
            rb_pool.append({
                "block_id":   rb.get("block_id"),
                "start":      float(rb.get("start_in_final_video") or 0),
                "duration":   float(rb.get("duration") or 4),
                "score":      float(rb.get("smart_score") or 0),
                "scene_role": rb.get("scene_role") or "",
                "srt_text":   _srt_text_for_block(rb),
                "used":       False,
            })

        # Sort pool by score desc để ưu tiên cảnh hay
        rb_pool_sorted = sorted(rb_pool, key=lambda x: x["score"], reverse=True)

        result: dict = {}  # block_id → start_in_final_video

        for sb in script_blocks:
            bid      = int(sb.get("block_id") or 0)
            sb_text  = sb.get("text") or sb.get("visual_hint") or ""
            sb_kw    = _keywords(sb_text)
            dur_need = float(sb.get("duration_hint_seconds") or 4.0)

            best_rb   = None
            best_score = -1.0

            for rb in rb_pool_sorted:
                if rb["used"]:
                    continue
                if rb["duration"] < dur_need * 0.5:  # cảnh quá ngắn
                    continue

                # Keyword overlap với thoại SRT của cảnh đó
                srt_kw   = _keywords(rb["srt_text"])
                overlap  = len(sb_kw & srt_kw) / max(len(sb_kw), 1)

                # Ưu tiên cảnh nhân vật chính / cao trào
                role_bonus = 0.3 if "nhan_vat_chinh" in rb["scene_role"] else 0.0
                role_bonus += 0.2 if rb["scene_role"] in ("climax", "conflict") else 0.0

                combined = rb["score"] * 0.4 + overlap * 0.4 + role_bonus * 0.2

                if combined > best_score:
                    best_score = combined
                    best_rb    = rb

            if best_rb:
                result[bid] = best_rb["start"]
                best_rb["used"] = True
            # Nếu không tìm được → giữ original (không thêm vào result)

        return result

    @staticmethod
    def _remove_embedded_dialogue_repetition(text: Any) -> str:
        """Remove generated anchor tails that paste raw dialogue after recap narration."""
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return cleaned
        marker_patterns = [
            r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+làm\s+rõ\s+bước\s+ngoặt.+$",
            r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+khiến\s+tình\s+thế.+$",
            r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+đẩy\s+câu\s+chuyện.+$",
            r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+$",
        ]
        for pattern in marker_patterns:
            next_text = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
            if next_text and next_text != cleaned:
                cleaned = next_text
                break

        def _fold(value: Any) -> str:
            normalized = unicodedata.normalize("NFKD", str(value or ""))
            no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return re.sub(r"\s+", " ", no_marks.lower()).strip()

        stop = {
            "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do",
            "day", "khi", "neu", "den", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
            "khong", "nguoi", "canh", "phim", "chi", "tiet", "manh", "moi",
        }

        def _tokens(value: Any) -> list:
            return [
                token for token in re.findall(r"[a-z0-9]+", _fold(value))
                if len(token) > 2 and token not in stop
            ]

        def _is_generated_padding_artifact(value: Any) -> bool:
            folded = _fold(value)
            return (
                "khoanh khac nay khong chi la mot phan ung thoang qua" in folded
                or bool(re.search(r"\bvoi\s+[^.!?]{0,160}khoanh\s+khac\b", folded))
                or ("lam bau khong khi cang hon" in folded and "day cau chuyen" in folded)
                or "tro thanh manh moi can chu y" in folded
                or "noi truc tiep cam xuc" in folded
                or "moi phan ung nho" in folded
                or "nhip review bam sat" in folded
                or (
                    "giup canh nay co diem tua ro hon" in folded
                    and "loi ke khong troi qua" in folded
                )
            )

        parts = [
            re.sub(r"\s+", " ", part).strip(" .")
            for part in re.split(r"\s*(?:[.!?]+|\s+\.\s+|\s*\|\s*)\s*", cleaned)
            if re.sub(r"\s+", " ", part).strip(" .")
        ]
        if len(parts) <= 1:
            return "" if _is_generated_padding_artifact(cleaned) else cleaned.strip()

        kept = []
        seen_tokens = set()
        for part in parts:
            if _is_generated_padding_artifact(part):
                continue
            token_set = set(_tokens(part))
            if token_set and seen_tokens:
                overlap = len(token_set & seen_tokens) / max(1, len(token_set))
                if overlap >= 0.72:
                    continue
            kept.append(part)
            seen_tokens.update(token_set)
        result = ". ".join(kept).strip()
        if result and result[-1] not in ".!?":
            result += "."
        return result or cleaned.strip()

    @staticmethod
    def _dedupe_script_blocks_for_tts(script_blocks: list, render_blocks: list) -> tuple:
        """Remove repeated narration sentences before TTS so the voice does not loop."""
        if not script_blocks:
            return script_blocks, 0

        def _fold(value: Any) -> str:
            normalized = unicodedata.normalize("NFKD", str(value or ""))
            no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return re.sub(r"\s+", " ", no_marks.lower()).strip()

        stopwords = {
            "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do", "day",
            "khi", "neu", "den", "tren", "duoi", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
            "ve", "dang", "rat", "hon", "se", "duoc", "khong", "nguoi", "canh", "phim",
        }

        def _sig(sentence: str) -> str:
            folded = _fold(sentence)
            tokens = [
                token for token in re.findall(r"[a-z0-9]+", folded)
                if len(token) > 2 and token not in stopwords
            ]
            if len(tokens) < 4:
                return ""
            return " ".join(tokens[:18])

        def _sentences(text: str) -> list:
            parts = re.split(r"(?<=[.!?。！？])\s+", str(text or "").strip())
            if len(parts) <= 1:
                parts = re.split(r"\s*[;；]\s*", str(text or "").strip())
            return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]

        def _clean_anchor(block: dict, rb: dict) -> str:
            candidates = [
                block.get("srt_anchor"),
                block.get("dialogue_text"),
                block.get("visual_anchor"),
                block.get("visual_hint"),
                rb.get("srt_anchor"),
                rb.get("dialogue_text"),
                rb.get("visual_anchor"),
                rb.get("visual_hint"),
            ]
            subs = rb.get("subtitles") or []
            if isinstance(subs, list):
                candidates.append(" ".join(str(s.get("text", "")) for s in subs[:2] if isinstance(s, dict)))
            for value in candidates:
                text = re.sub(r"\s+", " ", str(value or "")).strip()
                text = re.sub(
                    r"[\u2E80-\u2EFF\u2F00-\u2FDF\u3000-\u303F"
                    r"\u3040-\u30FF\u3100-\u312F\u3200-\u32FF"
                    r"\u3300-\u33FF\u3400-\u4DBF\u4E00-\u9FFF"
                    r"\uF900-\uFAFF\uFE30-\uFE4F\uAC00-\uD7AF"
                    r"\uFF00-\uFFEF]+",
                    " ",
                    text,
                )
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text[:120].rstrip(" .")
            return ""

        rb_map = {}
        for index, rb in enumerate(render_blocks or [], 1):
            if isinstance(rb, dict):
                for key in (rb.get("block_id"), rb.get("book_id"), index):
                    try:
                        rb_map[int(key)] = rb
                    except Exception:
                        pass

        seen_global = set()
        changed = 0
        result = []
        for index, block in enumerate(script_blocks, 1):
            if not isinstance(block, dict):
                result.append(block)
                continue
            item = dict(block)
            try:
                bid = int(item.get("block_id") or index)
            except Exception:
                bid = index
            rb = rb_map.get(bid) or {}
            text = str(item.get("text") or "").strip()
            kept = []
            local_seen = set()
            removed_here = 0
            for sentence in _sentences(text):
                sig = _sig(sentence)
                if sig and (sig in local_seen or sig in seen_global):
                    removed_here += 1
                    continue
                kept.append(sentence)
                if sig:
                    local_seen.add(sig)
                    seen_global.add(sig)

            if removed_here:
                changed += removed_here
                new_text = " ".join(kept).strip()
                if not new_text:
                    anchor = _clean_anchor(item, rb)
                    if anchor:
                        new_text = f"Ở nhịp {bid}, chi tiết {anchor} tạo thêm áp lực cho câu chuyện và mở ra hướng diễn biến mới."
                    else:
                        new_text = f"Ở nhịp {bid}, mạch phim chuyển sang hệ quả mới, giúp câu chuyện tiến thêm một bước thay vì lặp lại ý cũ."
                if new_text and new_text[-1] not in ".!?":
                    new_text += "."
                item["text"] = new_text
                item["dedupe_before_tts"] = True
                item["dedupe_removed_sentences"] = removed_here
            result.append(item)

        return result, changed

    def _rewrite_duplicate_sentences_for_tts(self, ai, script_blocks: list, render_blocks: list) -> tuple:
        """Ask Gemini once to rewrite blocks that contain repeated narration sentences."""
        if not script_blocks:
            return script_blocks, 0, 0

        def _fold(value: Any) -> str:
            normalized = unicodedata.normalize("NFKD", str(value or ""))
            no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return re.sub(r"\s+", " ", no_marks.lower()).strip()

        stopwords = {
            "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do", "day",
            "khi", "neu", "den", "tren", "duoi", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
            "ve", "dang", "rat", "hon", "se", "duoc", "khong", "nguoi", "canh", "phim",
        }

        def _sig(sentence: str) -> str:
            folded = _fold(sentence)
            tokens = [
                token for token in re.findall(r"[a-z0-9]+", folded)
                if len(token) > 2 and token not in stopwords
            ]
            if len(tokens) < 4:
                return ""
            return " ".join(tokens[:18])

        def _sentences(text: str) -> list:
            parts = re.split(r"(?<=[.!?。！？])\s+", str(text or "").strip())
            if len(parts) <= 1:
                parts = re.split(r"\s*[;；]\s*", str(text or "").strip())
            return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]

        def _id(item: dict, fallback: int) -> int:
            try:
                return int(item.get("block_id") or item.get("book_id") or fallback)
            except Exception:
                return fallback

        rb_map = {}
        for index, rb in enumerate(render_blocks or [], 1):
            if isinstance(rb, dict):
                for key in (rb.get("block_id"), rb.get("book_id"), index):
                    try:
                        rb_map[int(key)] = rb
                    except Exception:
                        pass

        payload = []
        seen_global = set()
        duplicate_sentence_count = 0
        for index, block in enumerate(script_blocks, 1):
            if not isinstance(block, dict):
                continue
            bid = _id(block, index)
            text = str(block.get("text") or "").strip()
            local_seen = set()
            duplicates = []
            for sentence in _sentences(text):
                sig = _sig(sentence)
                if not sig:
                    continue
                if sig in local_seen or sig in seen_global:
                    duplicates.append(sentence[:240])
                local_seen.add(sig)
            seen_global.update(local_seen)
            if not duplicates:
                continue
            duplicate_sentence_count += len(duplicates)
            rb = rb_map.get(bid) or {}
            payload.append({
                "block_id": bid,
                "current_text": text[:1400],
                "duplicate_sentences_to_replace": duplicates,
                "target_words": rb.get("target_words") or block.get("target_words") or 0,
                "duration_seconds": rb.get("duration") or block.get("duration_hint_seconds") or 4.0,
                "srt_reference": str(
                    rb.get("srt_anchor")
                    or rb.get("dialogue_text")
                    or block.get("srt_anchor")
                    or ""
                )[:700],
                "visual_reference": str(
                    rb.get("visual_anchor")
                    or rb.get("visual_hint")
                    or block.get("visual_anchor")
                    or block.get("visual_hint")
                    or ""
                )[:500],
            })

        if not payload:
            return script_blocks, 0, 0

        old_web_fallback = os.environ.get("AUTORECAP_GEMINI_WEB_FALLBACK")
        os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = "1"
        try:
            from engine.prompt_vault import PromptVault
            template = PromptVault.instance().get("duplicate_repair") or ""
            if not template:
                raise RuntimeError("duplicate_repair prompt is not vaulted")
            prompt = template.replace(
                "{input_blocks}",
                json.dumps(payload, ensure_ascii=False),
            )
            raw = ai._try_generate(prompt)
        finally:
            if old_web_fallback is None:
                os.environ.pop("AUTORECAP_GEMINI_WEB_FALLBACK", None)
            else:
                os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = old_web_fallback

        data = ai._extract_json_payload(raw) or {}
        repaired = data.get("script_blocks") if isinstance(data, dict) else []
        fixed_by_id = {}
        if isinstance(repaired, list):
            for item in repaired:
                if not isinstance(item, dict):
                    continue
                bid = _id(item, 0)
                text = ai._clean_review_script_text(item.get("text") or "")
                if bid and text and len(text.split()) >= 6:
                    fixed_by_id[bid] = text

        if not fixed_by_id:
            return script_blocks, 0, duplicate_sentence_count

        updated = []
        fixed_count = 0
        for index, block in enumerate(script_blocks, 1):
            if not isinstance(block, dict):
                updated.append(block)
                continue
            item = dict(block)
            bid = _id(item, index)
            if fixed_by_id.get(bid):
                item["text"] = fixed_by_id[bid]
                item["duplicate_sentence_ai_repaired"] = True
                fixed_count += 1
            updated.append(item)
        return updated, fixed_count, duplicate_sentence_count

    @staticmethod
    def _normalize_script_blocks_to_render_blocks(package: dict, render_blocks: list) -> tuple:
        """Force script_blocks to match render_blocks 1:1 after manual editor changes."""
        package = dict(package or {})
        blocks = [dict(block) for block in (package.get("script_blocks") or []) if isinstance(block, dict)]
        renders = [dict(block) for block in (render_blocks or []) if isinstance(block, dict)]
        if not blocks or not renders:
            return package, 0
        strategy = str(package.get("script_block_strategy") or "").strip().lower()
        if strategy == "chapter_budget_story_segment" or any(
            str(block.get("script_block_strategy") or "").strip().lower() == "chapter_budget_story_segment"
            for block in blocks
        ):
            package["script_blocks"] = blocks
            package["script_blocks_normalized_to_render"] = False
            package["script_blocks_keep_story_segments"] = True
            return package, 0

        def _id(item: dict, fallback: int) -> int:
            try:
                return int(item.get("block_id") or item.get("book_id") or fallback)
            except Exception:
                return fallback

        by_id = {}
        for index, block in enumerate(blocks, 1):
            by_id[_id(block, index)] = block

        normalized = []
        changed = 0
        for index, rb in enumerate(renders, 1):
            bid = _id(rb, index)
            item = dict(by_id.get(bid) or (blocks[index - 1] if index - 1 < len(blocks) else {}))
            if _id(item, bid) != bid:
                changed += 1
            item["block_id"] = bid
            for field in (
                "start_in_final_video", "end_in_final_video", "duration",
                "original_start", "original_end", "target_words",
                "srt_anchor", "dialogue_text", "visual_anchor", "visual_hint",
                "triangulated_srt_anchor", "cut_visible_srt", "srt_must_mention",
                "source_only_context",
                "script_text", "editor_text", "voice_text", "voice_source",
                "voice_narration_style", "script_editor_synced", "script_editor_locked",
                "character_focus", "focus_characters", "main_characters",
                "_tts_rate", "auto_time_balanced",
                "estimated_tts_duration", "estimated_final_voice_duration",
                "silent_padding_seconds", "needs_expand_words",
                "auto_time_speed_needed", "auto_time_speed_used",
                "auto_time_status", "auto_time_category", "auto_time_message",
                "auto_time_timing_diff_seconds", "auto_time_policy",
                "script_editor_sync_id", "script_editor_sync_version",
                "script_block_strategy", "recap2_beat_mode",
                "source_block_ids", "source_block_count",
                "raw_render_block_count", "beat_target_seconds",
                "review_clip", "review_clip_source", "source_video",
            ):
                if rb.get(field) and not item.get(field):
                    item[field] = rb.get(field)
            text = re.sub(r"\s+", " ", str(item.get("text") or "").strip())
            if not text:
                candidates = [
                    rb.get("editor_text"),
                    rb.get("voice_text"),
                    rb.get("script_text"),
                    rb.get("srt_anchor"),
                    rb.get("dialogue_text"),
                    rb.get("cut_visible_srt"),
                    rb.get("triangulated_srt_anchor"),
                    rb.get("visual_anchor"),
                    rb.get("visual_hint"),
                ]
                subs = rb.get("subtitles") or []
                if isinstance(subs, list):
                    candidates.append(" ".join(str(s.get("text", "")) for s in subs[:3] if isinstance(s, dict)))
                text = next((re.sub(r"\s+", " ", str(value or "")).strip() for value in candidates if str(value or "").strip()), "")
                if not text:
                    text = "Tiếp tục nhịp truyện ở đoạn này, giữ mạch cảm xúc nối sang cảnh kế tiếp."
                item["text"] = text
                item["auto_filled"] = True
                item["needs_rewrite"] = True
                changed += 1
            normalized.append(item)

        if len(blocks) != len(normalized):
            changed += abs(len(blocks) - len(normalized))
        package["script_blocks"] = normalized
        package["script"] = "\n\n".join(
            str(block.get("text", "")).strip()
            for block in normalized
            if str(block.get("text", "")).strip()
        ).strip()
        package["subtitle_chunks"] = [block["text"] for block in normalized if block.get("text")]
        package["script_blocks_normalized_to_render"] = bool(changed)
        return package, changed

    def _sync_editor_script_to_render_blocks(self) -> int:
        """Mirror confirmed Script Editor text into render_blocks for voice/SRT context."""
        if not self.ai_package.get("script_editor_synced") or not self.render_blocks:
            return 0
        if str(self.ai_package.get("script_block_strategy") or "").strip().lower() == "chapter_budget_story_segment":
            return 0
        blocks = [block for block in (self.ai_package.get("script_blocks") or []) if isinstance(block, dict)]
        if not blocks:
            return 0

        def _id(item: dict, fallback: int) -> int:
            try:
                return int(item.get("block_id") or item.get("book_id") or fallback)
            except Exception:
                return fallback

        by_id = {_id(block, index): block for index, block in enumerate(blocks, 1)}
        changed = 0
        for index, rb in enumerate(self.render_blocks, 1):
            if not isinstance(rb, dict):
                continue
            bid = _id(rb, index)
            block = by_id.get(bid)
            text = re.sub(r"\s+", " ", str((block or {}).get("text") or "").strip())
            if not text:
                continue
            for key in ("script_text", "editor_text", "voice_text"):
                if rb.get(key) != text:
                    rb[key] = text
                    changed += 1
            rb["script_editor_synced"] = True
            rb["script_editor_locked"] = True
            rb["voice_source"] = "script_editor"
            rb["voice_narration_style"] = normalize_review_style(
                self.ai_package.get("voice_narration_style")
                or block.get("voice_narration_style")
                or os.environ.get("AUTORECAP_REVIEW_STYLE")
            )
            sync_id = self.ai_package.get("script_editor_sync_id") or block.get("script_editor_sync_id")
            if sync_id:
                rb["script_editor_sync_id"] = sync_id
            rb["script_editor_sync_version"] = self.ai_package.get("script_editor_sync_version") or 2
            if block.get("_tts_rate"):
                rb["_tts_rate"] = block.get("_tts_rate")
            if block.get("target_words"):
                rb["target_words"] = block.get("target_words")
            for meta_key in (
                "script_block_strategy", "recap2_beat_mode",
                "source_block_ids", "source_block_count",
                "raw_render_block_count", "beat_target_seconds",
            ):
                if block.get(meta_key) is not None:
                    rb[meta_key] = block.get(meta_key)
            for timing_key in (
                "estimated_tts_duration", "estimated_final_voice_duration",
                "silent_padding_seconds", "needs_expand_words",
                "auto_time_timing_diff_seconds", "auto_time_pad_warn_seconds",
                "auto_time_word_count", "auto_time_speed_needed", "auto_time_speed_used",
                "auto_time_status", "auto_time_category", "auto_time_message", "auto_time_policy",
            ):
                if block.get(timing_key) is not None:
                    rb[timing_key] = block.get(timing_key)
            if block.get("start_in_final_video") is not None:
                rb["start_in_final_video"] = float(block.get("start_in_final_video") or 0.0)
                changed += 1
            if block.get("end_in_final_video") is not None:
                rb["end_in_final_video"] = float(block.get("end_in_final_video") or 0.0)
                changed += 1
            if rb.get("start_in_final_video") is not None and rb.get("end_in_final_video") is not None:
                rb["duration"] = max(
                    0.0,
                    float(rb.get("end_in_final_video") or 0.0) - float(rb.get("start_in_final_video") or 0.0),
                )
                rb["duration_hint_seconds"] = rb["duration"]
                changed += 1
            elif block.get("duration_hint_seconds") and not rb.get("duration"):
                rb["duration"] = block.get("duration_hint_seconds")
                rb["duration_hint_seconds"] = block.get("duration_hint_seconds")
                changed += 1
        if changed:
            try:
                with open(os.path.join(self.output_dir, "render_blocks.json"), "w", encoding="utf-8") as f:
                    json.dump(self.render_blocks, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
        self.ai_package["voice_source"] = "script_editor"
        self.ai_package["voice_narration_style"] = normalize_review_style(
            self.ai_package.get("voice_narration_style") or os.environ.get("AUTORECAP_REVIEW_STYLE")
        )
        self.ai_package["review_style_profile"] = self.ai_package["voice_narration_style"]
        self.ai_package["script_editor_sync_version"] = 2
        return changed

    def _repair_srt_alignment_once(
        self,
        ai,
        report: dict,
        max_blocks: int = 40,
    ) -> tuple:
        """Repair SRT-misaligned blocks in one grouped AI call, with deterministic fallback."""
        blocks = [dict(block) for block in (self.ai_package.get("script_blocks") or []) if isinstance(block, dict)]
        if not blocks or not self.render_blocks or not report:
            return False, 0

        bad_ids = []
        by_report = {}
        for item in report.get("blocks") or []:
            if not isinstance(item, dict):
                continue
            try:
                bid = int(item.get("block_id") or 0)
            except Exception:
                bid = 0
            if not bid:
                continue
            by_report[bid] = item
            if item.get("errors") or item.get("warnings"):
                bad_ids.append(bid)
        bad_ids = bad_ids[:max_blocks]
        if not bad_ids:
            return False, 0

        def _id(item: dict, fallback: int) -> int:
            try:
                return int(item.get("block_id") or item.get("book_id") or fallback)
            except Exception:
                return fallback

        rb_by_id = {_id(rb, index): rb for index, rb in enumerate(self.render_blocks, 1) if isinstance(rb, dict)}
        block_by_id = {_id(block, index): block for index, block in enumerate(blocks, 1)}

        payload = []
        for bid in bad_ids:
            block = block_by_id.get(bid) or {}
            rb = rb_by_id.get(bid) or {}
            rep = by_report.get(bid) or {}
            payload.append({
                "block_id": bid,
                "current_text": str(block.get("text") or "")[:900],
                "required_anchor": rep.get("required_anchor") or rb.get("srt_must_mention") or rb.get("cut_visible_srt") or rb.get("triangulated_srt_anchor") or "",
                "evidence": rep.get("evidence_preview") or rb.get("srt_anchor") or rb.get("dialogue_text") or "",
                "source_only_context_do_not_make_visible_action": rep.get("source_only_context") or rb.get("source_only_context") or "",
                "duration_seconds": rb.get("duration") or block.get("duration_hint_seconds") or 4.0,
                "target_words": rb.get("target_words") or block.get("target_words") or 0,
                "errors": rep.get("errors") or [],
                "warnings": rep.get("warnings") or [],
            })

        fixed_by_id = {}
        api_only = str(os.environ.get("AUTORECAP_ALIGNMENT_REPAIR_API_ONLY", "0") or "0").strip().lower() not in {
            "0", "false", "no", "off"
        }
        old_web_fallback = os.environ.get("AUTORECAP_GEMINI_WEB_FALLBACK")
        if api_only:
            os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = "0"
        try:
            prompt = (
                "Bạn là biên tập kịch bản review phim. Sửa TẤT CẢ block bị lệch SRT trong một lần.\n"
                "Trả về DUY NHẤT JSON hợp lệ dạng {\"script_blocks\":[{\"block_id\":1,\"text\":\"...\"}]}.\n"
                "Quy tắc: giữ đúng block_id, không đổi thứ tự timeline, không bịa cảnh, mỗi text phải nhắc required_anchor nếu có, "
                "không dùng SOURCE_ONLY_CONTEXT như hành động đang thấy trên màn hình, tiếng Việt có dấu, không giải thích.\n"
                f"INPUT_BLOCKS:\n{json.dumps(payload, ensure_ascii=False)}"
            )
            self._log(
                f"   [SRT_ALIGNMENT] Gọi Gemini API sửa gộp {len(payload)} blocks"
                + (" (không dùng Web fallback)" if api_only else "")
            )
            raw = ai._try_generate(prompt)
            data = ai._extract_json_payload(raw) or {}
            repaired = data.get("script_blocks") if isinstance(data, dict) else []
            if isinstance(repaired, list):
                for item in repaired:
                    if not isinstance(item, dict):
                        continue
                    try:
                        bid = int(item.get("block_id") or 0)
                    except Exception:
                        bid = 0
                    text = ai._clean_review_script_text(item.get("text") or "")
                    if bid and text:
                        fixed_by_id[bid] = text
        except Exception as exc:
            self._log(f"   [SRT_ALIGNMENT] API repair skip -> dùng sửa nội bộ: {exc}")
        finally:
            if api_only:
                if old_web_fallback is None:
                    os.environ.pop("AUTORECAP_GEMINI_WEB_FALLBACK", None)
                else:
                    os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = old_web_fallback

        changed = 0
        for bid in bad_ids:
            block = block_by_id.get(bid)
            if not block:
                continue
            if fixed_by_id.get(bid):
                block["text"] = fixed_by_id[bid]
                block["srt_alignment_ai_repaired"] = True
                changed += 1
                continue
            rep = by_report.get(bid) or {}
            anchor = str(rep.get("required_anchor") or "").strip()
            evidence = str(rep.get("evidence_preview") or "").strip()
            errors = rep.get("errors") or []
            if errors and (anchor or evidence):
                focus = self._usable_vietnamese_focus(
                    anchor,
                    evidence,
                    rb_by_id.get(bid, {}).get("visual_anchor") if rb_by_id.get(bid) else "",
                    rb_by_id.get(bid, {}).get("visual_hint") if rb_by_id.get(bid) else "",
                )
                block["text"] = self._generic_review_fallback(self.movie_title, focus)
                block["srt_alignment_anchor_injected"] = True
                block["srt_alignment_deterministic_rewrite"] = True
                changed += 1
            elif anchor and anchor.lower() not in str(block.get("text") or "").lower():
                text = str(block.get("text") or "").strip()
                if text and text[-1] not in ".!?":
                    text += "."
                focus = self._usable_vietnamese_focus(anchor, text)
                block["text"] = f"{text} {self._generic_review_fallback(self.movie_title, focus)}".strip()
                block["srt_alignment_anchor_injected"] = True
                changed += 1

        if changed:
            ordered = [block_by_id.get(_id(rb, index)) for index, rb in enumerate(self.render_blocks, 1)]
            ordered = [block for block in ordered if isinstance(block, dict)]
            self.ai_package["script_blocks"] = ordered
            self.ai_package["script"] = "\n\n".join(
                str(block.get("text", "")).strip()
                for block in ordered
                if str(block.get("text", "")).strip()
            ).strip()
            self.ai_package["subtitle_chunks"] = [block["text"] for block in ordered if block.get("text")]
            self.ai_package["srt_alignment_group_repair_used"] = True
            self.ai_package["srt_alignment_group_repair_count"] = changed
        return bool(changed), changed

    def _repair_srt_alignment_final_pass(
        self,
        ai,
        report: dict,
        max_blocks: int = 3,
    ) -> tuple:
        """Final small repair for the few hard SRT errors left after group repair."""
        blocks = [dict(block) for block in (self.ai_package.get("script_blocks") or []) if isinstance(block, dict)]
        if not blocks or not self.render_blocks or not report:
            return False, 0

        bad_ids = []
        by_report = {}
        for item in report.get("blocks") or []:
            if not isinstance(item, dict) or not item.get("errors"):
                continue
            try:
                bid = int(item.get("block_id") or 0)
            except Exception:
                bid = 0
            if bid:
                bad_ids.append(bid)
                by_report[bid] = item
        bad_ids = bad_ids[:max_blocks]
        if not bad_ids:
            return False, 0

        def _id(item: dict, fallback: int) -> int:
            try:
                return int(item.get("block_id") or item.get("book_id") or fallback)
            except Exception:
                return fallback

        rb_by_id = {_id(rb, index): rb for index, rb in enumerate(self.render_blocks, 1) if isinstance(rb, dict)}
        block_by_id = {_id(block, index): block for index, block in enumerate(blocks, 1)}

        payload = []
        for bid in bad_ids:
            block = block_by_id.get(bid) or {}
            rb = rb_by_id.get(bid) or {}
            rep = by_report.get(bid) or {}
            required_anchor = (
                rep.get("required_anchor")
                or rb.get("srt_must_mention")
                or rb.get("cut_visible_srt")
                or rb.get("triangulated_srt_anchor")
                or ""
            )
            payload.append({
                "block_id": bid,
                "current_text": str(block.get("text") or "")[:900],
                "must_include_anchor": str(required_anchor)[:240],
                "cut_visible_srt": str(rb.get("cut_visible_srt") or rb.get("triangulated_srt_anchor") or "")[:300],
                "evidence": str(rep.get("evidence_preview") or rb.get("srt_anchor") or rb.get("dialogue_text") or "")[:400],
                "forbidden_source_only_context": str(rep.get("source_only_context") or rb.get("source_only_context") or "")[:260],
                "duration_seconds": rb.get("duration") or block.get("duration_hint_seconds") or 4.0,
                "target_words": rb.get("target_words") or block.get("target_words") or 0,
                "errors": rep.get("errors") or [],
            })

        fixed_by_id = {}
        allow_web = str(os.environ.get("AUTORECAP_ALIGNMENT_FINAL_WEB", "1") or "1").strip().lower() not in {
            "0", "false", "no", "off"
        }
        old_web_fallback = os.environ.get("AUTORECAP_GEMINI_WEB_FALLBACK")
        if allow_web:
            os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = "1"
        try:
            prompt = (
                "Bạn là biên tập viên review phim tiếng Việt. Sửa riêng các block còn lỗi SRT cuối cùng.\n"
                "Trả về DUY NHẤT JSON hợp lệ: {\"script_blocks\":[{\"block_id\":1,\"text\":\"...\"}]}.\n"
                "Luật bắt buộc:\n"
                "- Mỗi text phải nhắc rõ nội dung must_include_anchor/cut_visible_srt bằng câu tự nhiên.\n"
                "- Không nhắc forbidden_source_only_context như hành động đang thấy trên màn hình.\n"
                "- Không dùng câu kỹ thuật như 'ở cảnh này', 'điểm cần theo sát', 'setup', 'block', 'book'.\n"
                "- Viết như lời thuyết minh review chuyên nghiệp, tiếng Việt có dấu, không giải thích.\n"
                "- Giữ gần target_words nếu có.\n"
                f"INPUT_BLOCKS:\n{json.dumps(payload, ensure_ascii=False)}"
            )
            self._log(
                f"   [SRT_ALIGNMENT] Final repair: sửa riêng {len(payload)} block còn lỗi"
                + (" bằng Gemini Web/API" if allow_web else " bằng API")
            )
            raw = ai._try_generate(prompt)
            data = ai._extract_json_payload(raw) or {}
            repaired = data.get("script_blocks") if isinstance(data, dict) else []
            if isinstance(repaired, list):
                for item in repaired:
                    if not isinstance(item, dict):
                        continue
                    bid = _id(item, 0)
                    text = ai._clean_review_script_text(item.get("text") or "")
                    if bid and text and len(text.split()) >= 6:
                        fixed_by_id[bid] = text
        except Exception as exc:
            self._log(f"   [SRT_ALIGNMENT] Final repair AI skip -> dùng sửa nội bộ: {exc}")
        finally:
            if allow_web:
                if old_web_fallback is None:
                    os.environ.pop("AUTORECAP_GEMINI_WEB_FALLBACK", None)
                else:
                    os.environ["AUTORECAP_GEMINI_WEB_FALLBACK"] = old_web_fallback

        changed = 0
        for bid in bad_ids:
            block = block_by_id.get(bid)
            if not block:
                continue
            if fixed_by_id.get(bid):
                block["text"] = fixed_by_id[bid]
                block["srt_alignment_final_ai_repaired"] = True
                changed += 1
                continue

            rb = rb_by_id.get(bid) or {}
            rep = by_report.get(bid) or {}
            anchor = self._usable_vietnamese_focus(
                rep.get("required_anchor")
                or "",
                rb.get("srt_must_mention"),
                rb.get("cut_visible_srt"),
                rb.get("triangulated_srt_anchor"),
                rep.get("evidence_preview"),
                rb.get("visual_anchor"),
                rb.get("visual_hint"),
                block.get("text"),
                limit=140,
            )
            target_words = int((rb.get("target_words") or block.get("target_words") or 0) or 0)
            # A factual scene description is safer than invented emotional filler.
            text = self._strip_cjk_text(rep.get('required_anchor') or anchor).rstrip('.!?') + '.'
            block["text"] = text
            block["srt_alignment_final_deterministic_repair"] = True
            changed += 1

        if changed:
            ordered = [block_by_id.get(_id(rb, index)) for index, rb in enumerate(self.render_blocks, 1)]
            ordered = [block for block in ordered if isinstance(block, dict)]
            self.ai_package["script_blocks"] = ordered
            self.ai_package["script"] = "\n\n".join(
                str(block.get("text", "")).strip()
                for block in ordered
                if str(block.get("text", "")).strip()
            ).strip()
            self.ai_package["subtitle_chunks"] = [block["text"] for block in ordered if block.get("text")]
            self.ai_package["srt_alignment_final_repair_used"] = True
            self.ai_package["srt_alignment_final_repair_count"] = changed
        return bool(changed), changed

    @staticmethod
    def _strip_cjk_text(value: Any) -> str:
        text = str(value or "")
        text = re.sub(
            r"[\u2E80-\u2EFF\u2F00-\u2FDF\u3000-\u303F"
            r"\u3040-\u30FF\u3100-\u312F\u3200-\u32FF"
            r"\u3300-\u33FF\u3400-\u4DBF\u4E00-\u9FFF"
            r"\uF900-\uFAFF\uFE30-\uFE4F\uAC00-\uD7AF"
            r"\uFF00-\uFFEF]+",
            " ",
            text,
        )
        text = text.replace("|", ". ")
        text = re.sub(r"\s+", " ", text)
        return text.strip(" .,:;")

    @classmethod
    def _usable_vietnamese_focus(cls, *values: Any, limit: int = 120) -> str:
        for value in values:
            original = str(value or "")
            cleaned = cls._strip_cjk_text(value)
            had_cjk = cleaned != original.strip()
            cleaned = re.sub(
                r"\b(?:manh\s+mối|ở\s+cảnh\s+này|điểm\s+cần\s+theo\s+sát|setup|block|book)\b",
                " ",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(r"\s+[A-Za-zÀ-ỹà-ỹĐđ]$", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")
            if not cleaned:
                continue
            if len(cleaned.split()) < 3:
                continue
            if had_cjk and len(cleaned.split()) < 6:
                continue
            if len(re.findall(r"[A-Za-zÀ-ỹà-ỹĐđ]", cleaned)) < 10:
                continue
            return cleaned[:limit].rstrip(" ,.;:")
        return ""

    # Template pool để tránh lặp câu mở đầu
    _FALLBACK_TEMPLATES_WITH_FOCUS = [
        "{focus} — khoảnh khắc đủ để đẩy câu chuyện sang một hướng không ai ngờ tới.",
        "Cảnh {focus} khiến mạch truyện chuyển sang nhịp căng hơn, buộc mọi người phải chú ý.",
        "{focus} — chi tiết nhỏ nhưng đủ sức làm thay đổi cục diện của cả câu chuyện.",
        "Từ {focus}, tình thế bắt đầu nghiêng về một hướng mới mà các nhân vật chưa kịp chuẩn bị.",
        "{focus} trở thành điểm then chốt, kéo theo hàng loạt phản ứng dây chuyền trong câu chuyện.",
        "Nhìn vào {focus}, người xem hiểu ngay rằng câu chuyện đang bước sang một giai đoạn mới.",
        "{focus} — dấu hiệu cho thấy cục diện sắp đảo lộn hoàn toàn.",
        "Cảnh {focus} lặng lẽ nhưng đủ sức làm thay đổi mọi thứ trong tập phim này.",
    ]
    _FALLBACK_TEMPLATES_NO_FOCUS = [
        "Mạch truyện tiếp tục căng lên với những bước ngoặt bất ngờ dồn dập.",
        "Câu chuyện leo thang theo chiều hướng không ai dự đoán được.",
        "Các nhân vật phải đối mặt với một tình huống mới đẩy câu chuyện lên cao trào.",
        "Diễn biến tiếp theo khiến mọi sắp xếp trước đó trở nên vô nghĩa.",
        "Những gì xảy ra tiếp theo sẽ quyết định hướng đi của toàn bộ câu chuyện.",
    ]
    _fallback_call_counter: int = 0

    @classmethod
    def _generic_review_fallback(cls, movie_title: str = "", focus: str = "") -> str:
        import threading
        # Thread-safe counter để chọn template khác nhau mỗi lần gọi
        cls._fallback_call_counter = (getattr(cls, "_fallback_call_counter", 0) + 1) % 100
        idx = cls._fallback_call_counter
        focus = cls._usable_vietnamese_focus(focus, limit=110)
        if focus:
            templates = cls._FALLBACK_TEMPLATES_WITH_FOCUS
            tpl = templates[idx % len(templates)]
            return tpl.format(focus=focus)
        templates = cls._FALLBACK_TEMPLATES_NO_FOCUS
        tpl = templates[idx % len(templates)]
        title = movie_title or "bộ phim"
        return tpl

    def step_voice_segments(self) -> bool:
        """Tao voice TUNG BLOCK, moi block khop dung duration cua canh video.

        THIET KE: Voice phai khop tung canh (vd: canh "dau lau duoi ao"
        o giay 20-24s thi voice noi ve no cung o giay 20-24s).

        Quy trinh moi block:
          1. TTS o rate=0% (tu nhien nhat)
          2. Do duration thuc cua TTS
          3. atempo nen/gian vua khit target_duration cua block
             (atempo giu pitch -> giong van muot)
          4. step_voice_concat dat moi block dung start_in_final_video

        Resume-safe: moi block seg_XXXX.mp3 da co thi bo qua.
        """
        self._step_start("VOICE_SEGMENTS")
        try:
            from core.ai_engine import AIEngine
            from core.voice_timing import VoiceTimingController

            script_blocks = self.ai_package.get("script_blocks", [])
            full_script   = self.ai_package.get("script", "")
            if script_blocks and self.render_blocks and any(
                isinstance(block, dict) and block.get("source_block_ids")
                for block in script_blocks
            ):
                from core.recap_engine import restore_story_segment_coverage
                coverage = restore_story_segment_coverage(script_blocks, self.render_blocks)
                self.ai_package["story_source_coverage"] = coverage
                self.ai_package["timing_contract_version"] = 8
            if not full_script and not script_blocks:
                self._step_fail("VOICE_SEGMENTS", "Không có kịch bản")
                return False
            if script_blocks and len(script_blocks) > self._max_ai_blocks(self.cut_duration) * 2:
                self._step_fail(
                    "VOICE_SEGMENTS",
                    f"AI package cu qua nhieu block ({len(script_blocks)}). Hay chay lai AI_FULL de gom book truoc khi tao voice.",
                )
                return False

            ai           = AIEngine(api_key=self.gemini_api_key)
            segments_dir = os.path.join(self.output_dir, "voice_segments")
            Path(segments_dir).mkdir(exist_ok=True)
            continuous_default = "1" if self._recap2_beat_mode_enabled() else "0"
            timing_policy = {
                "version": 8,
                "mode": "recap2_scene_anchored" if self._recap2_beat_mode_enabled() else "scene_pinned",
                "min_voice_speed": float(os.environ.get("AUTORECAP_MIN_VOICE_SPEED", "1.0") or "1.0"),
                "max_voice_speed": float(os.environ.get("AUTORECAP_MAX_VOICE_SPEED", "1.5") or "1.5"),
                "continuous_voice": str(os.environ.get("AUTORECAP_CONTINUOUS_VOICE", continuous_default) or continuous_default).strip().lower()
                not in {"0", "false", "no", "off"},
            }

            try:
                from utils.helpers import FFmpegUtils
                ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
            except Exception:
                import shutil
                ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

            def _probe_dur(p):
                return VoiceTimingController.probe_duration(p, ffmpeg_bin)

            def _atempo_chain(ratio):
                # ratio = tts_dur / target_dur ; >1 = voice dai hon canh -> nen nhanh hon
                # Gioi han toc do toi da mac dinh 1.5x de giong khong bi doc qua nhanh.
                min_speed = float(os.environ.get("AUTORECAP_MIN_VOICE_SPEED", "1.0") or "1.0")
                max_speed = float(os.environ.get("AUTORECAP_MAX_VOICE_SPEED", "1.5") or "1.5")
                ratio = max(max(1.0, min_speed), min(ratio, max(1.0, max_speed)))
                return f"atempo={ratio:.6f}"

            def _sync_timing_sidecar(source_audio, output_audio, scale=1.0):
                source_sidecar = str(source_audio) + ".timing.json"
                output_sidecar = str(output_audio) + ".timing.json"
                if not os.path.exists(source_sidecar):
                    return
                try:
                    with open(source_sidecar, "r", encoding="utf-8") as timing_file:
                        entries = json.load(timing_file)
                    normalized = []
                    for entry in entries if isinstance(entries, list) else []:
                        if not isinstance(entry, dict):
                            continue
                        item = dict(entry)
                        item["offset"] = round(float(item.get("offset") or 0.0) * scale, 6)
                        item["duration"] = round(float(item.get("duration") or 0.0) * scale, 6)
                        normalized.append(item)
                    if normalized:
                        with open(output_sidecar, "w", encoding="utf-8") as timing_file:
                            json.dump(normalized, timing_file, ensure_ascii=False)
                except Exception as timing_error:
                    self._log(f"   ⚠️ Không đồng bộ được timeline từ TTS: {timing_error}")

            def _clamp_tts_rate(rate_text):
                try:
                    pct = float(str(rate_text or "+0%").strip().replace("%", ""))
                except Exception:
                    pct = 0.0
                min_speed = float(os.environ.get("AUTORECAP_MIN_VOICE_SPEED", "1.0") or "1.0")
                max_speed = float(os.environ.get("AUTORECAP_MAX_VOICE_SPEED", "1.5") or "1.5")
                min_pct = max(0.0, (min_speed - 1.0) * 100.0)
                max_pct = max(0.0, (max_speed - 1.0) * 100.0)
                pct = max(min_pct, min(max_pct, pct))
                return f"+{pct:.0f}%" if pct >= 0 else f"{pct:.0f}%"

            # Resume cache
            seg_meta_path = os.path.join(self.output_dir, "voice_segments.json")
            cached = {}
            if os.path.exists(seg_meta_path):
                try:
                    pkg_file_for_cache = os.path.join(self.output_dir, "ai_package.json")
                    current_editor_sync_version = self.ai_package.get("script_editor_sync_version")
                    current_editor_sync_id = self.ai_package.get("script_editor_sync_id")
                    cache_is_stale = (
                        os.path.exists(pkg_file_for_cache)
                        and os.path.getmtime(pkg_file_for_cache) > os.path.getmtime(seg_meta_path)
                    )
                    if cache_is_stale:
                        self._log("   voice_segments cache cu hon Script Editor -> tao voice lai")
                    else:
                        for s in json.load(open(seg_meta_path, encoding="utf-8")):
                            bid = s.get("block_id")
                            ap  = s.get("audio_path", "")
                            if (
                                current_editor_sync_version
                                and s.get("script_editor_synced")
                                and s.get("script_editor_sync_version") != current_editor_sync_version
                            ):
                                continue
                            if (
                                current_editor_sync_id
                                and s.get("script_editor_synced")
                                and s.get("script_editor_sync_id") != current_editor_sync_id
                            ):
                                continue
                            if bid is not None and ap and os.path.exists(ap) and os.path.getsize(ap) > 0:
                                cached[int(bid)] = s
                    if cached:
                        self._log(f"   resume: {len(cached)} block da co")
                except Exception:
                    pass

            # Align script -> render blocks
            editor_synced = bool(
                self.ai_package.get("script_editor_synced")
                or any(isinstance(block, dict) and block.get("script_editor_locked") for block in script_blocks)
            )

            if script_blocks and self.render_blocks and not editor_synced:
                from core.premium_pipeline import PremiumReviewPipeline
                script_blocks = PremiumReviewPipeline.align_script_blocks_to_book_map(
                    script_blocks, full_script, self.render_blocks
                )
                script_blocks = PremiumReviewPipeline.enforce_block_word_targets(
                    script_blocks, full_script, self.render_blocks
                )
                for block in script_blocks:
                    if isinstance(block, dict) and block.get("text"):
                        block["text"] = PremiumReviewPipeline.restore_known_vietnamese_phrases(
                            block.get("text", "")
                        )
                spoken_script = "\n\n".join(
                    str(block.get("text", "")).strip()
                    for block in script_blocks
                    if str(block.get("text", "")).strip()
                ).strip()
                if spoken_script:
                    full_script = spoken_script
                    self.ai_package["script"] = full_script
                    self.ai_package["script_blocks"] = script_blocks
            elif script_blocks and self.render_blocks and editor_synced:
                self._log("   🔒 Dùng kịch bản đã xác nhận trong Script Editor, không tự cân chữ lại")
                try:
                    from core.premium_pipeline import PremiumReviewPipeline
                    for block in script_blocks:
                        if isinstance(block, dict) and block.get("text"):
                            block["text"] = PremiumReviewPipeline.restore_known_vietnamese_phrases(
                                block.get("text", "")
                            )
                    full_script = "\n\n".join(
                        str(block.get("text", "")).strip()
                        for block in script_blocks
                        if str(block.get("text", "")).strip()
                    ).strip()
                    if full_script:
                        self.ai_package["script"] = full_script
                        self.ai_package["script_blocks"] = script_blocks
                except Exception:
                    pass

            if script_blocks and self.render_blocks:
                self.ai_package, normalized_count = self._normalize_script_blocks_to_render_blocks(
                    self.ai_package,
                    self.render_blocks,
                )
                if normalized_count:
                    script_blocks = self.ai_package.get("script_blocks", [])
                    full_script = self.ai_package.get("script", full_script)
                    self._log(
                        f"   🔧 Chuẩn hóa kịch bản theo render_blocks: "
                        f"{len(script_blocks)}/{len(self.render_blocks)} blocks"
                    )
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)

            editor_synced = bool(self.ai_package.get("script_editor_synced"))
            if editor_synced:
                synced_render_count = self._sync_editor_script_to_render_blocks()
                if synced_render_count:
                    self._log(f"   🔗 Đồng bộ Script Editor -> render_blocks: {synced_render_count} cập nhật")

            try:
                from core.vietnamese_text import VietnameseTextGuard
                vi_report = VietnameseTextGuard.report_blocks(script_blocks)
                if editor_synced and (vi_report.get("needs_repair") or VietnameseTextGuard.needs_diacritic_repair(full_script)):
                    weak_count = int(vi_report.get("weak_block_count") or 0)
                    self._log(
                        f"   🔒 Script Editor đã xác nhận -> bỏ qua gate dấu tiếng Việt ({weak_count} block cần xem lại)"
                    )
                elif vi_report.get("needs_repair") or VietnameseTextGuard.needs_diacritic_repair(full_script):
                    self._log(
                        f"   Sua dau tieng Viet truoc TTS: {vi_report.get('weak_block_count', 0)} block"
                    )
                    self.ai_package, vi_report = ai.repair_vietnamese_diacritics_package(
                        self.ai_package,
                        self.render_blocks,
                    )
                    script_blocks = self.ai_package.get("script_blocks", [])
                    full_script = self.ai_package.get("script", full_script)
                    if vi_report.get("needs_repair"):
                        self._step_fail(
                            "VOICE_SEGMENTS",
                            "Kịch bản vẫn thiếu dấu tiếng Việt sau khi sửa; không tạo voice để tránh đọc sai.",
                        )
                        return False
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            except Exception as e:
                self._step_fail(
                    "VOICE_SEGMENTS",
                    f"Không sửa được dấu tiếng Việt trước TTS: {e}",
                )
                return False

            if script_blocks and self.render_blocks:
                try:
                    from core.script_grounding import ScriptGroundingValidator
                    self.ai_package, _ = ScriptGroundingValidator.annotate_package(
                        self.ai_package,
                        self.render_blocks,
                    )
                except Exception as e:
                    self._log(f"   ⚠️  Không kiểm định lại grounding trước TTS: {e}")
                try:
                    from core.scene_mapping_validator import SceneMappingValidator
                    self.ai_package, _ = SceneMappingValidator.annotate_package(
                        self.ai_package,
                        self._ensure_scene_cards(),
                        self.render_blocks,
                    )
                except Exception as e:
                    self._log(f"   ⚠️  Không kiểm định lại scene mapping trước TTS: {e}")
                try:
                    self.ai_package, srt_alignment_report = self._annotate_srt_alignment(
                        self.ai_package,
                        self.render_blocks,
                    )
                    if srt_alignment_report.get("error_count") and self.ai_package.get("script_editor_synced"):
                        self._log(
                            f"   [SRT_ALIGNMENT] Truoc TTS: {srt_alignment_report.get('error_count')} loi "
                            "-> đã xác nhận Script Editor, không tự sửa lại"
                        )
                    elif srt_alignment_report.get("error_count"):
                        self._log(f"   [SRT_ALIGNMENT] Truoc TTS: {srt_alignment_report.get('error_count')} loi")
                        repair_changed, repair_count = self._repair_srt_alignment_once(
                            ai,
                            srt_alignment_report,
                        )
                        if repair_changed:
                            self._log(f"   [SRT_ALIGNMENT] Group repair 1 lượt: sửa {repair_count} blocks")
                            self.ai_package, srt_alignment_report = self._annotate_srt_alignment(
                                self.ai_package,
                                self.render_blocks,
                            )
                            script_blocks = self.ai_package.get("script_blocks", [])
                            full_script = self.ai_package.get("script", full_script)
                            with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        remaining_errors = int(srt_alignment_report.get("error_count") or 0)
                        final_max = int(os.environ.get("AUTORECAP_ALIGNMENT_FINAL_MAX", "3") or "3")
                        if 0 < remaining_errors <= final_max:
                            final_changed, final_count = self._repair_srt_alignment_final_pass(
                                ai,
                                srt_alignment_report,
                                max_blocks=final_max,
                            )
                            if final_changed:
                                self._log(f"   [SRT_ALIGNMENT] Final repair: sửa thêm {final_count} blocks")
                                self.ai_package, srt_alignment_report = self._annotate_srt_alignment(
                                    self.ai_package,
                                    self.render_blocks,
                                )
                                script_blocks = self.ai_package.get("script_blocks", [])
                                full_script = self.ai_package.get("script", full_script)
                                with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                                    json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    self._log(f"   [SRT_ALIGNMENT] Không kiểm định lại trước TTS: {e}")

                # ── AUTO-FILL blocks trống trước Market check ──────────────────
                try:
                    _pkg_blocks = self.ai_package.get("script_blocks") or []
                    if self.ai_package.get("script_editor_synced"):
                        _empty_after_editor = [
                            str(_b.get("block_id") or _i + 1)
                            for _i, _b in enumerate(_pkg_blocks)
                            if isinstance(_b, dict) and not str(_b.get("text", "")).strip()
                        ]
                        if _empty_after_editor:
                            self._step_fail(
                                "VOICE_SEGMENTS",
                                "Còn block trống sau Script Editor: "
                                + ", ".join(_empty_after_editor[:12])
                                + ". Hãy bấm Auto-fill trong Script Editor để Gemini Web viết từ SRT dịch.",
                            )
                            return False
                    _rb_lk = {}
                    for _xi, _xrb in enumerate(self.render_blocks, 1):
                        if isinstance(_xrb, dict):
                            for _xk in (_xrb.get("block_id"), _xrb.get("book_id"), _xi):
                                try: _rb_lk[int(_xk)] = _xrb
                                except: pass
                    _filled = 0
                    for _xi, _xb in enumerate(_pkg_blocks):
                        if not isinstance(_xb, dict) or _xb.get("text","").strip():
                            continue
                        try: _xbid = int(_xb.get("block_id") or _xi + 1)
                        except: _xbid = _xi + 1
                        _xrb = _rb_lk.get(_xbid) or {}
                        _xsrt = str(_xrb.get("srt_anchor") or _xrb.get("dialogue_text") or "").strip()
                        if not _xsrt:
                            _xsubs = _xrb.get("subtitles") or []
                            _xsrt = " ".join(str(_s.get("text","")) for _s in _xsubs[:3] if _s.get("text")).strip()
                        _focus = self._usable_vietnamese_focus(
                            _xsrt,
                            _xrb.get("cut_visible_srt"),
                            _xrb.get("triangulated_srt_anchor"),
                            _xrb.get("visual_anchor"),
                            _xrb.get("visual_hint"),
                        )
                        if _focus:
                            _xsrt = self._generic_review_fallback(self.movie_title, _focus)
                        else:
                            _xsrt = f"Tiếp tục câu chuyện trong {self.movie_title or 'phim'}."
                        _xb["text"] = _xsrt
                        _pkg_blocks[_xi]["text"] = _xsrt
                        _filled += 1
                    if _filled:
                        self.ai_package["script_blocks"] = _pkg_blocks
                        self.ai_package["script"] = "\n\n".join(
                            b.get("text","") for b in _pkg_blocks if b.get("text")
                        )
                        script_blocks = _pkg_blocks
                        full_script = self.ai_package["script"]
                        _pf = os.path.join(self.output_dir, "ai_package.json")
                        with open(_pf, "w", encoding="utf-8") as _fp:
                            json.dump(self.ai_package, _fp, indent=2, ensure_ascii=False)
                        self._log(f"   🔧 Auto-fill {_filled} block trống bằng SRT gốc")
                except Exception as _afe:
                    self._log(f"   ⚠️ Auto-fill: {_afe}")
                # ── END AUTO-FILL ─────────────────────────────────────────────

                try:
                    self.ai_package, market_report = self._annotate_market_readiness(
                        self.ai_package,
                        self.render_blocks,
                        self.ai_package.get("context_coverage_report"),
                        self.ai_package.get("visual_scene_evidence_report"),
                    )
                    if self._market_strict() and not market_report.get("ready"):
                        # Chỉ fail nếu có ERROR khác ngoài các type được bỏ qua
                        _bypass_types = {
                            "some_under_target_blocks",
                            # Khi Script Editor đã xác nhận hoặc recap2_beat_mode:
                            # kịch bản viết tự do hơn SRT → grounding yếu là bình thường
                            "script_grounding_too_weak",
                            "script_grounding_needs_rewrite",
                        }
                        real_errors = [
                            item for item in (market_report.get("issues") or [])
                            if item.get("level") == "ERROR"
                            and item.get("type") not in _bypass_types
                        ]
                        if real_errors and self.ai_package.get("script_editor_synced"):
                            serious_types = {
                                "duplicate_script_blocks",
                                "empty_script_blocks",
                            }
                            if not self.ai_package.get("recap2_beat_mode"):
                                serious_types.add("too_many_under_target_blocks")
                            serious_errors = [
                                item for item in real_errors
                                if str(item.get("type") or "") in serious_types
                            ]
                            if serious_errors:
                                serious_names = ", ".join(str(item.get("type") or "unknown") for item in serious_errors[:6])
                                self._step_fail(
                                    "VOICE_SEGMENTS",
                                    "Script Editor vẫn còn lỗi nghiêm trọng trước TTS: "
                                    f"{serious_names}. Cần bấm AI sửa review/sửa block trước khi tạo voice.",
                                )
                                return False
                            self._log(
                                "   🔒 Script Editor đã xác nhận -> chỉ bỏ qua lỗi market không nghiêm trọng, tiếp tục tạo voice/render"
                            )
                        elif real_errors:
                            # Trong auto-pipeline (không có Script Editor): bỏ qua
                            # too_many_under_target_blocks vì timing repair / atempo sẽ xử lý.
                            _auto_bypass = {"too_many_under_target_blocks", "some_under_target_blocks"}
                            _blocking = [
                                item for item in real_errors
                                if str(item.get("type") or "") not in _auto_bypass
                            ]
                            if _blocking:
                                self._step_fail("VOICE_SEGMENTS", self._market_fail_message(market_report))
                                return False
                            self._log(
                                "   ℹ️  Market: block ngắn hơn target → timing repair sẽ xử lý, tiếp tục TTS"
                            )
                        else:
                            self._log("   ℹ️  Market WARN (đã fill blocks) → tiếp tục TTS")
                except Exception as e:
                    self._step_fail("VOICE_SEGMENTS", f"Không kiểm định được market readiness trước TTS: {e}")
                    return False

            if script_blocks and self.ai_package.get("script_editor_synced"):
                self._log("   🔒 Script Editor đã xác nhận -> không sửa/dedupe lại trước TTS")
            elif script_blocks:
                try:
                    script_blocks, dup_ai_fixed, dup_sentence_count = self._rewrite_duplicate_sentences_for_tts(
                        ai,
                        script_blocks,
                        self.render_blocks,
                    )
                    if dup_ai_fixed:
                        self.ai_package["script_blocks"] = script_blocks
                        self.ai_package["script"] = "\n\n".join(
                            str(block.get("text", "")).strip()
                            for block in script_blocks
                            if isinstance(block, dict) and str(block.get("text", "")).strip()
                        ).strip()
                        full_script = self.ai_package["script"]
                        self.ai_package["duplicate_sentence_ai_repair_used"] = True
                        self.ai_package["duplicate_sentence_ai_repair_blocks"] = dup_ai_fixed
                        with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                            json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        self._log(
                            f"   🔁 Gemini sửa câu lặp: {dup_ai_fixed} block / {dup_sentence_count} câu"
                        )
                except Exception as dup_exc:
                    self._log(f"   🔁 Gemini sửa câu lặp lỗi -> dùng fallback bỏ câu: {dup_exc}")

                script_blocks, dedupe_count = self._dedupe_script_blocks_for_tts(
                    script_blocks,
                    self.render_blocks,
                )
                if dedupe_count:
                    self.ai_package["script_blocks"] = script_blocks
                    self.ai_package["script"] = "\n\n".join(
                        str(block.get("text", "")).strip()
                        for block in script_blocks
                        if isinstance(block, dict) and str(block.get("text", "")).strip()
                    ).strip()
                    full_script = self.ai_package["script"]
                    self.ai_package["tts_dedup_removed_sentences"] = dedupe_count
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                    self._log(f"   🔁 Chống lặp trước TTS fallback: bỏ {dedupe_count} câu trùng")

            # RECAP2 allocates the selected duration while Gemini writes the
            # chapter segments. Never truncate those approved texts again at
            # the TTS boundary: doing so removes narration and the ending.
            if self.ai_package.get("recap2_beat_mode") and self.max_video_minutes and script_blocks:
                self.ai_package.pop("target_review_budget_fit", None)

            if script_blocks:
                def _is_channel_cta(text: Any) -> bool:
                    folded = unicodedata.normalize("NFKD", str(text or "").lower())
                    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
                    follow_action = any(
                        marker in folded
                        for marker in ("theo doi", "follow", "dang ky", "subscribe")
                    )
                    channel_context = any(
                        marker in folded
                        for marker in ("kenh", "channel", "tap moi", "phan tiep", "don xem", "xem tiep")
                    )
                    return follow_action and channel_context

                def _strip_channel_cta(text: Any) -> tuple[str, int]:
                    original = re.sub(r"\s+", " ", str(text or "")).strip()
                    if not original:
                        return "", 0
                    kept = []
                    removed = 0
                    for sentence in re.split(r"(?<=[.!?…])\s+", original):
                        sentence = sentence.strip()
                        if sentence and _is_channel_cta(sentence):
                            removed += 1
                        elif sentence:
                            kept.append(sentence)
                    return " ".join(kept).strip(), removed

                removed_body_ctas = 0
                for body_block in script_blocks[:-1]:
                    if not isinstance(body_block, dict):
                        continue
                    clean_body, removed = _strip_channel_cta(body_block.get("text", ""))
                    if removed:
                        body_block["text"] = clean_body
                        body_block["body_cta_removed"] = True
                        removed_body_ctas += removed

                final_block = next(
                    (block for block in reversed(script_blocks) if isinstance(block, dict)),
                    None,
                )
                if final_block is not None:
                    # Always rebuild the ending so there is exactly one CTA and it
                    # is the final spoken sentence of the final movie block.
                    text, removed_final_ctas = _strip_channel_cta(final_block.get("text", ""))
                    cta = "Đừng quên theo dõi kênh để đón xem tập mới nhất và phần tiếp theo của câu chuyện."
                    if text and text[-1] not in ".!?…":
                        text += "."
                    final_block["text"] = f"{text} {cta}".strip() if text else cta
                    final_block["final_cta_added"] = True
                    self.ai_package["script_blocks"] = script_blocks
                    self.ai_package["script"] = "\n\n".join(
                        str(block.get("text", "")).strip()
                        for block in script_blocks
                        if isinstance(block, dict) and str(block.get("text", "")).strip()
                    ).strip()
                    self.ai_package["subtitle_chunks"] = [
                        str(block.get("text", "")).strip()
                        for block in script_blocks
                        if isinstance(block, dict) and str(block.get("text", "")).strip()
                    ]
                    self.ai_package["final_channel_cta_added"] = True
                    self.ai_package["body_channel_cta_removed_count"] = removed_body_ctas
                    self.ai_package["final_channel_cta_replaced_count"] = removed_final_ctas
                    full_script = self.ai_package["script"]
                    cached = {}
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                    self._log(
                        "   📣 CTA chỉ ở cuối phim: "
                        f"đã bỏ {removed_body_ctas} CTA giữa phim, giữ đúng 1 câu cuối"
                    )

            render_map = {}
            for i, rb in enumerate(self.render_blocks, 1):
                if isinstance(rb, dict):
                    for k in (rb.get("block_id"), rb.get("book_id"), i):
                        try: render_map[int(k)] = rb
                        except Exception: pass

            voice_segments = []
            skipped = generated = 0
            total = len(script_blocks) if script_blocks else 1
            editor_actual_timing_errors = []
            self._log(f"   🔊 Bắt đầu tạo giọng đọc TTS cho {total} block...")

            if not script_blocks or len(script_blocks) <= 1:
                # Single fallback
                seg_path = os.path.join(segments_dir, "seg_0000.mp3")
                combined = AIEngine._clean_tts_text(full_script)
                if combined and combined[-1] not in ".!?":
                    combined += "."
                target_dur = self.cut_duration or 0.0
                raw_path = os.path.join(segments_dir, "raw_0000.mp3")
                timing_repair = {"action": "none"}
                tts_dur = 0.0
                for attempt in range(2):
                    asyncio.run(ai.text_to_speech(combined, raw_path, voice=self.voice, rate="+0%"))
                    tts_dur = _probe_dur(raw_path)
                    if self.ai_package.get("recap2_beat_mode"):
                        repair = {"action": "none", "changed": False, "reason": "recap2_beat_flow"}
                    else:
                        repair = VoiceTimingController.repair_text(
                            combined,
                            target_dur,
                            tts_dur,
                            {"text": combined, "target_words": VoiceTimingController.target_words(target_dur)},
                        )
                    timing_repair = repair
                    if attempt == 0 and repair.get("changed"):
                        combined = repair.get("text", combined)
                        try:
                            os.unlink(raw_path)
                        except Exception:
                            pass
                        continue
                    break
                if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
                    import shutil as _sh
                    _sh.copy2(raw_path, seg_path)
                    try:
                        os.unlink(raw_path)
                    except Exception:
                        pass
                final_dur = _probe_dur(seg_path)
                voice_segments.append({
                    "block_id": 0, "audio_path": seg_path,
                    "start_in_video": 0.0,
                    "target_duration": target_dur,
                    "text": combined,
                    "text_preview": combined[:80],
                    "text_hash": VoiceTimingController.text_hash(combined),
                    "raw_duration": round(tts_dur, 3),
                    "audio_duration": round(final_dur or tts_dur, 3),
                    "duration_ratio": round((final_dur or tts_dur) / target_dur, 3) if target_dur > 0 else 0,
                    "timing_status": VoiceTimingController.assess(final_dur or tts_dur, target_dur).get("status", "unknown"),
                    "timing_repair": timing_repair,
                })
                full_script = combined
                self.ai_package["script"] = combined
                generated = 1
            else:
                technical_tts_markers = (
                    "ở cảnh này,",
                    "điểm cần theo sát",
                    "đúng với phần đang xuất hiện trên video",
                    "setup / xây dựng",
                    "thay vì lướt qua",
                    "như một cảnh chuyển",
                    "cần làm rõ setup",
                    "cần làm rõ",
                    "vì thế đoạn này",
                    "vì thế cảnh này",
                    "block ",
                    "book ",
                    "manh mối ",
                    "ở mốc ",
                    "khiến tình thế trở nên căng hơn",
            "buộc các nhân vật phải bước tiếp",
            "khoảnh khắc này không chỉ là một phản ứng thoáng qua",
            "giúp cảnh này có điểm tựa rõ hơn",
            "để lời kể không trôi qua như một đoạn chuyển cảnh",
            "làm bầu không khí căng hơn",
            "đẩy câu chuyện của",
                    'trở thành manh mối cần chú ý',
                    'nối trực tiếp cảm xúc',
                    'mỗi phản ứng nhỏ',
                    'nhịp review bám sát',
                    "áp lực trong cảnh tăng lên rõ rệt",
                    "mở ra một hướng nghi vấn mới",
                    "buộc nhân vật phải xử lý thận trọng hơn",
                    "áp lực của vụ việc tiếp tục dồn lên các nhân vật",
                    "mạch điều tra chuyển sang một nhịp căng hơn",
                    "cảnh này giữ nhịp dẫn chuyện đi tiếp",
                    "trở thành manh mối cần chú ý",
                    "nói trực tiếp cảm xúc",
                    "mỗi phản ứng nhỏ",
                    "nhịp review bám sát",
                )

                def _has_technical_tts_text(value):
                    text_value = str(value or "")
                    lower = text_value.lower()
                    if any(marker in lower for marker in technical_tts_markers):
                        return True
                    return self._strip_cjk_text(text_value) != text_value.strip()

                def _natural_tts_fallback(rb, bid):
                    candidates = [
                        rb.get("srt_must_mention"),
                        rb.get("cut_visible_srt"),
                        rb.get("triangulated_srt_anchor"),
                        rb.get("srt_anchor"),
                        rb.get("dialogue_text"),
                        rb.get("visual_anchor"),
                        rb.get("visual_hint"),
                    ]
                    focus = self._usable_vietnamese_focus(*candidates, limit=115)
                    return self._generic_review_fallback(self.movie_title, focus)

                sanitized_before_tts = 0
                for idx, block in enumerate(script_blocks, 1):
                    self._log(f"[TTS_PROGRESS] {idx}/{total}")
                    text = block.get("text", "").strip()
                    try: bid = int(block.get("block_id", idx))
                    except: bid = idx

                    rb         = render_map.get(bid) or (self.render_blocks[idx-1] if idx-1 < len(self.render_blocks) else {})
                    story_segment = bool(block.get("source_block_ids")) or block.get("script_block_strategy") == "chapter_budget_story_segment"
                    start_t    = float(
                        (block.get("start_in_final_video") if story_segment else None)
                        or rb.get("start_in_final_video")
                        or 0.0
                    )
                    target_dur = float(
                        block.get("source_coverage_duration_seconds")
                        or block.get("target_voice_duration_seconds")
                        or (block.get("duration_hint_seconds") if story_segment else None)
                        or rb.get("duration")
                        or block.get("duration_hint_seconds")
                        or 4.0
                    )
                    seg_path   = os.path.join(segments_dir, f"seg_{bid:04d}.mp3")
                    if not text and editor_synced:
                        self._step_fail(
                            "VOICE_SEGMENTS",
                            f"Block {bid} rỗng sau Script Editor. Hãy bấm Auto-fill trong Script Editor.",
                        )
                        return False
                    if not text:
                        text = str(rb.get("srt_anchor") or rb.get("dialogue_text") or "").strip()
                        if not text:
                            _subs = rb.get("subtitles") or []
                            text = " ".join(str(_s.get("text", "")) for _s in _subs[:3] if _s.get("text")).strip()
                        if not text:
                            text = str(rb.get("visual_anchor") or rb.get("visual_hint") or rb.get("one_main_idea") or "").strip()
                        if not text:
                            text = f"Tiếp tục câu chuyện trong {self.movie_title or 'phim'}."
                        block["text"] = text
                        self._log(f"   block {bid}: block rỗng -> fill trước TTS")
                    sanitized_text = self._remove_embedded_dialogue_repetition(text)
                    if sanitized_text and sanitized_text != text:
                        text = sanitized_text
                        block["text"] = sanitized_text
                        block["embedded_dialogue_repetition_removed"] = True
                        sanitized_before_tts += 1
                    clean = AIEngine._clean_tts_text(text)
                    cleaned_sanitized = self._remove_embedded_dialogue_repetition(clean)
                    if cleaned_sanitized and cleaned_sanitized != clean:
                        clean = cleaned_sanitized
                        block["text"] = cleaned_sanitized
                        block["embedded_dialogue_repetition_removed"] = True
                        sanitized_before_tts += 1
                    # Chỉ check `clean` (đã lọc CJK/markers), không check `text` gốc.
                    # Nếu check `text` gốc: AI viết script có chữ Hán inline → CJK check
                    # kích hoạt fallback dù Vietnamese đã được clean OK.
                    if (not editor_synced) and _has_technical_tts_text(clean):
                        clean = AIEngine._clean_tts_text(_natural_tts_fallback(rb, bid))
                        block["text"] = clean
                        block["technical_fallback_rewritten_before_tts"] = True
                        self._log(f"   block {bid}: sửa câu fallback kỹ thuật trước TTS")

                    # ── Fix 1: clean rỗng sau khi lọc → fallback text gốc ──
                    if (not clean or not clean.strip()) and editor_synced:
                        self._step_fail(
                            "VOICE_SEGMENTS",
                            f"Block {bid} rỗng sau khi clean TTS. Hãy sửa lại trong Script Editor.",
                        )
                        return False
                    if not clean or not clean.strip():
                        # Thử lọc nhẹ hơn: chỉ bỏ timestamp và markers
                        import re as _re
                        fallback = _re.sub(
                            r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}",
                            "", text
                        )
                        fallback = _re.sub(r"^\s*\d+\s*$", "", fallback, flags=_re.MULTILINE)
                        fallback = _re.sub(
                            r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\uFF00-\uFFEF，。；：！？]+",
                            " ", fallback
                        )
                        fallback = self._strip_cjk_text(fallback)
                        fallback = " ".join(fallback.split()).strip()
                        if fallback and len(fallback) >= 5:
                            clean = fallback
                            self._log(f"   block {bid}: text rỗng sau clean → dùng fallback ({len(fallback)} chars)")
                        else:
                            # Hoàn toàn không có text hữu dụng → tạo câu placeholder
                            clean = f"Tiếp tục câu chuyện trong {self.movie_title or 'phim'}."
                            self._log(f"   block {bid}: text rỗng → dùng placeholder")

                    # ── Fix 2: block quá ngắn → mở rộng trước TTS ──
                    target_words = int(rb.get("target_words") or block.get("target_words") or 0)
                    actual_words = len(clean.split())
                    min_words = max(8, int(target_words * 0.70)) if target_words > 0 else 0
                    if (not self.ai_package.get("recap2_beat_mode")) and target_words > 0 and actual_words < min_words and not editor_synced:
                        ai_timing_repair = str(os.environ.get("AUTORECAP_AI_TIMING_REPAIR", "0") or "0").strip().lower() in {
                            "1", "true", "yes", "on"
                        }
                        if ai_timing_repair:
                            try:
                                srt_ref = str(rb.get("srt_anchor") or rb.get("dialogue_text") or "").strip()
                                if not srt_ref:
                                    _subs = rb.get("subtitles") or []
                                    srt_ref = " ".join(str(_s.get("text","")) for _s in _subs[:3] if _s.get("text"))
                                expand_prompt = (
                                    f"Mở rộng đoạn thuyết minh phim '{self.movie_title or 'phim'}' sau "
                                    f"để đạt ~{target_words} từ (đang có {actual_words} từ, cần ~{target_dur:.1f}s đọc).\n"
                                    f"Thoại gốc tham chiếu: {srt_ref[:200] or '[không có]'}\n"
                                    f"TEXT HIỆN TẠI: {clean}\n"
                                    f"Mở rộng bằng cách thêm mô tả hành động, cảm xúc, chi tiết cảnh. "
                                    f"Tiếng Việt CÓ DẤU. Trả về chỉ văn bản thuyết minh, không giải thích."
                                )
                                expanded = ai._try_generate(expand_prompt)
                                if expanded:
                                    expanded_clean = AIEngine._clean_tts_text(expanded.strip())
                                    expanded_words = len(expanded_clean.split()) if expanded_clean else 0
                                    if expanded_clean and expanded_words >= min_words:
                                        self._log(f"   block {bid}: AI expand {actual_words}->{expanded_words} words")
                                        clean = expanded_clean
                                        block["text"] = clean
                                    elif expanded_clean:
                                        self._log(f"   block {bid}: AI expand still short ({expanded_words}/{min_words} words) -> use padding")
                            except Exception as _exp_e:
                                self._log(f"   block {bid}: AI expand skip ({_exp_e})")
                        else:
                            self._log(f"   block {bid}: mo rong noi bo truoc TTS ({actual_words}/{min_words} words)")
                        if len(clean.split()) < min_words:
                            padded = VoiceTimingController.expand_short_text(
                                clean,
                                target_dur,
                                max(0.1, len(clean.split()) / 2.5),
                                {**rb, **block, "text": clean},
                            )
                            padded_clean = AIEngine._clean_tts_text(padded)
                            if padded_clean and len(padded_clean.split()) > len(clean.split()):
                                self._log(f"   block {bid}: expand {len(clean.split())}->{len(padded_clean.split())} words")
                                clean = padded_clean
                                block["text"] = clean
                                block["editor_timing_expanded"] = bool(editor_synced)
                    # ── END PRE-TTS FIXES ──────────────────────────────────────

                    repaired_text = getattr(self, '_automatic_voice_repair_texts', {}).get(bid)
                    if repaired_text:
                        clean = AIEngine._clean_tts_text(repaired_text)
                        block['text'] = clean

                    if clean and clean[-1] not in ".!?":
                        clean += "."
                    text_hash = VoiceTimingController.text_hash(clean)

                    # Resume only when the cached audio was generated from the
                    # exact current block text and passed timing validation.
                    cached_seg = cached.get(bid)
                    cached_audio_complete = False
                    if cached_seg and cached_seg.get("audio_path"):
                        cached_audio_complete, _cached_duration, _cached_minimum = (
                            AIEngine._tts_audio_is_complete(
                                clean,
                                cached_seg.get("audio_path"),
                                block.get("_tts_rate") or "+0%",
                            )
                        )
                    if (
                        cached_seg
                        and cached_audio_complete
                        and cached_seg.get("text_hash") == text_hash
                        and cached_seg.get("audio_path")
                        and os.path.exists(cached_seg.get("audio_path"))
                        and os.path.getsize(cached_seg.get("audio_path")) > 0
                        and cached_seg.get("timing_status") in ("ok", "unknown", "accepted_editor", "recap2_scene_anchored")
                        and cached_seg.get("timing_policy") == timing_policy
                    ):
                        voice_segments.append(cached_seg)
                        skipped += 1
                        continue

                    raw_path = os.path.join(segments_dir, f"raw_{bid:04d}.mp3")
                    timing_repair = {"action": "none"}
                    tts_dur = 0.0
                    # Dùng _tts_rate từ Script Editor nếu có (được tính sẵn để vừa khít cảnh)
                    block_rate = str(block.get("_tts_rate") or "+0%").strip()
                    if not block_rate.endswith("%"):
                        block_rate = "+0%"
                    block_rate = _clamp_tts_rate(block_rate)
                    for attempt in range(2):
                        asyncio.run(ai.text_to_speech(clean, raw_path, voice=self.voice, rate=block_rate))

                        if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                            self._log(f"   block {bid}: TTS rong, bo qua")
                            break

                        tts_dur = _probe_dur(raw_path)
                        if editor_synced:
                            repair = {
                                "action": "none",
                                "changed": False,
                                "reason": "script_editor_confirmed",
                            }
                            allow_editor_expand = (not self.ai_package.get("recap2_beat_mode")) and str(
                                os.environ.get("AUTORECAP_EDITOR_TTS_EXPAND", "0") or "0"
                            ).strip().lower() not in {"0", "false", "no", "off"}
                            assessment = VoiceTimingController.assess(tts_dur, target_dur)
                            if allow_editor_expand and assessment.get("status") == "too_short":
                                expanded_text = VoiceTimingController.expand_short_text(
                                    clean,
                                    target_dur,
                                    tts_dur,
                                    {**rb, **block, "text": clean},
                                )
                                expanded_text = AIEngine._clean_tts_text(expanded_text)
                                if expanded_text and len(expanded_text.split()) > len(clean.split()):
                                    repair = {
                                        "text": expanded_text,
                                        "action": "expanded",
                                        "before_status": "too_short",
                                        "before_ratio": assessment.get("ratio", 0.0),
                                        "changed": True,
                                        "reason": "script_editor_voice_too_short",
                                    }
                        else:
                            repair = {"action": "none", "changed": False, "reason": "recap2_beat_flow"} if self.ai_package.get("recap2_beat_mode") else VoiceTimingController.repair_text(clean, target_dur, tts_dur, block)
                        timing_repair = repair
                        if attempt == 0 and repair.get("changed"):
                            clean = repair.get("text", clean)
                            block["text"] = clean
                            block["timing_repair"] = repair
                            text_hash = VoiceTimingController.text_hash(clean)
                            try:
                                os.unlink(raw_path)
                            except Exception:
                                pass
                            self._log(
                                f"   block {bid}: repair {repair.get('action')} "
                                f"({repair.get('before_ratio', 0):.2f}x)"
                            )
                            continue
                        break

                    if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
                        continue

                    # Chỉ nén khi voice thật dài hơn toàn bộ evidence range của
                    # beat. Voice ngắn hơn được giữ tự nhiên; video sẽ cắt theo
                    # audio thật ở ClipAssembler.
                    if tts_dur > 0 and target_dur > 0:
                        ratio = tts_dur / target_dur
                        if ratio > 1.02:
                            af = _atempo_chain(ratio)
                            r = subprocess.run([
                                ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                                "-i", raw_path, "-af", af,
                                "-c:a", "libmp3lame", "-b:a", "192k", seg_path
                            ], **FFmpegUtils.subprocess_kwargs(
                                check=False, capture_output=True, text=True
                            ))
                            if r.returncode != 0:
                                import shutil as _sh; _sh.copy2(raw_path, seg_path)
                        else:
                            import shutil as _sh; _sh.copy2(raw_path, seg_path)
                    else:
                        import shutil as _sh; _sh.copy2(raw_path, seg_path)

                    # ── AUTO-FIX VOICE: nếu sau atempo vẫn lệch nhiều → retry TTS với rate cao hơn ──
                    final_dur = _probe_dur(seg_path)
                    _sync_timing_sidecar(
                        raw_path,
                        seg_path,
                        (float(final_dur or tts_dur) / float(tts_dur)) if tts_dur > 0 else 1.0,
                    )
                    if final_dur > 0 and target_dur > 0:
                        final_ratio = final_dur / target_dur
                        # Nếu vẫn dài hơn 20% hoặc ngắn hơn 40% → thử TTS lại với rate tối ưu
                        if (not self.ai_package.get("recap2_beat_mode")) and (not editor_synced) and (final_ratio > 1.20 or final_ratio < 0.60):
                            words = len(clean.split())
                            natural_dur = words / 2.5  # 2.5 từ/giây tự nhiên
                            opt_ratio = natural_dur / target_dur
                            max_voice_speed = float(os.environ.get("AUTORECAP_MAX_VOICE_SPEED", "1.5") or "1.5")
                            max_rate_pct = max(0.0, (max_voice_speed - 1.0) * 100.0)
                            opt_rate_pct = max(0.0, min(max_rate_pct, (opt_ratio - 1.0) * 100.0))
                            opt_rate = _clamp_tts_rate(f"+{opt_rate_pct:.0f}%")
                            if opt_rate != block_rate:  # tránh retry vô ích
                                self._log(f"   block {bid}: auto-fix rate {block_rate}→{opt_rate} (ratio {final_ratio:.2f})")
                                retry_raw = os.path.join(segments_dir, f"retry_{bid:04d}.mp3")
                                asyncio.run(ai.text_to_speech(clean, retry_raw, voice=self.voice, rate=opt_rate))
                                if os.path.exists(retry_raw) and os.path.getsize(retry_raw) > 0:
                                    retry_dur = _probe_dur(retry_raw)
                                    retry_ratio = retry_dur / target_dur if target_dur > 0 else 1.0
                                    # Dùng retry nếu gần target hơn
                                    if abs(retry_ratio - 1.0) < abs(final_ratio - 1.0):
                                        # Áp thêm atempo nhẹ nếu cần
                                        if retry_ratio > 1.02:
                                            af2 = _atempo_chain(retry_ratio)
                                            r2 = subprocess.run([
                                                ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                                                "-i", retry_raw, "-af", af2,
                                                "-c:a", "libmp3lame", "-b:a", "192k", seg_path
                                            ], **FFmpegUtils.subprocess_kwargs(
                                                check=False, capture_output=True, text=True
                                            ))
                                            if r2.returncode != 0:
                                                import shutil as _sh; _sh.copy2(retry_raw, seg_path)
                                        else:
                                            import shutil as _sh; _sh.copy2(retry_raw, seg_path)
                                        final_dur = _probe_dur(seg_path)
                                        block["_tts_rate"] = opt_rate
                                    try: os.unlink(retry_raw)
                                    except Exception: pass
                    # ── END AUTO-FIX ───────────────────────────────────────────

                    try: os.unlink(raw_path)
                    except Exception: pass
                    try:
                        if os.path.exists(raw_path + ".timing.json"):
                            os.unlink(raw_path + ".timing.json")
                    except Exception:
                        pass

                    actual_voice_dur = float(final_dur or tts_dur or 0.0)
                    recap2_flow = bool(self.ai_package.get("recap2_beat_mode"))
                    timing_status = VoiceTimingController.assess(actual_voice_dur, target_dur).get("status", "unknown")
                    if editor_synced:
                        if recap2_flow:
                            timing_status = "recap2_scene_anchored"
                        else:
                            actual_diff = abs(float(target_dur or 0.0) - actual_voice_dur) if target_dur > 0 and actual_voice_dur > 0 else 0.0
                            try:
                                max_editor_diff = float(os.environ.get("AUTORECAP_EDITOR_MAX_TIMING_DIFF_SECONDS", "0.0") or "0.0")
                            except Exception:
                                max_editor_diff = 0.0
                            if actual_diff <= max_editor_diff:
                                timing_status = "accepted_editor"
                            else:
                                timing_status = "editor_too_short" if actual_voice_dur < target_dur else "editor_too_long"
                                editor_actual_timing_errors.append({
                                    "block_id": bid,
                                    "voice_seconds": round(actual_voice_dur, 3),
                                    "scene_seconds": round(float(target_dur or 0.0), 3),
                                    "diff_seconds": round(actual_diff, 3),
                                    "status": timing_status,
                                })
                    generated += 1
                    timing_status_vi = {
                        "recap2_scene_anchored": "đã neo theo cảnh thật",
                        "accepted_editor": "đã xác nhận từ editor",
                        "editor_too_short": "voice ngắn hơn cảnh",
                        "editor_too_long": "voice dài hơn cảnh",
                        "ok": "đạt",
                        "too_short": "voice ngắn",
                        "too_long": "voice dài",
                        "unknown": "chưa rõ",
                    }.get(str(timing_status), str(timing_status))
                    self._log(
                        f"   block {bid}/{total}: TTS {tts_dur:.1f}s"
                        f" -> final {final_dur or tts_dur:.1f}s / cảnh {target_dur:.1f}s @ {start_t:.1f}s"
                        f" [{timing_status_vi}]"
                    )

                    voice_segments.append({
                        "block_id": bid, "audio_path": seg_path,
                        "start_in_video": start_t, "target_duration": target_dur,
                        "text": clean,
                        "text_preview": clean[:80],
                        "script_editor_synced": bool(editor_synced),
                        "voice_source": "script_editor" if editor_synced else "pipeline",
                        "voice_narration_style": (
                            normalize_review_style(
                                self.ai_package.get("voice_narration_style")
                                or block.get("voice_narration_style")
                                or os.environ.get("AUTORECAP_REVIEW_STYLE")
                            )
                        ),
                        "text_hash": text_hash,
                        "raw_duration": round(tts_dur, 3),
                        "audio_duration": round(final_dur or tts_dur, 3),
                        "duration_ratio": round((final_dur or tts_dur) / target_dur, 3) if target_dur > 0 else 0,
                        "timing_status": timing_status,
                        "timing_repair": timing_repair,
                        "chapter_index": block.get("chapter_index"),
                        "chapter_position": block.get("chapter_position"),
                        "chapter_size": block.get("chapter_size"),
                        "chapter_synopsis": block.get("chapter_synopsis"),
                        "chapter_prev_tail": block.get("chapter_prev_tail"),
                        "chapter_logline": block.get("chapter_logline"),
                        "next_chapter_synopsis": block.get("next_chapter_synopsis"),
                        "narrative_goal": block.get("narrative_goal"),
                        "bridge_line": block.get("bridge_line") or block.get("bridge_to_next"),
                        "bridge_to_next": block.get("bridge_to_next") or block.get("bridge_line"),
                        "character_focus": block.get("character_focus"),
                        "srt_anchor": block.get("srt_anchor"),
                        "visual_anchor": block.get("visual_anchor"),
                        "review_sync_source": block.get("review_sync_source"),
                        "map_reduce_outline_valid": block.get("map_reduce_outline_valid"),
                        "chapter_range": block.get("chapter_range"),
                        "outline_coverage": block.get("outline_coverage"),
                        "estimated_tts_duration": block.get("estimated_tts_duration"),
                        "estimated_final_voice_duration": block.get("estimated_final_voice_duration"),
                        "silent_padding_seconds": block.get("silent_padding_seconds"),
                        "needs_expand_words": block.get("needs_expand_words"),
                        "auto_time_speed_needed": block.get("auto_time_speed_needed"),
                        "auto_time_speed_used": block.get("auto_time_speed_used"),
                        "auto_time_status": block.get("auto_time_status"),
                        "auto_time_category": block.get("auto_time_category"),
                        "auto_time_message": block.get("auto_time_message"),
                        "auto_time_timing_diff_seconds": block.get("auto_time_timing_diff_seconds"),
                        "auto_time_policy": block.get("auto_time_policy"),
                        "script_editor_sync_version": self.ai_package.get("script_editor_sync_version"),
                        "script_editor_sync_id": self.ai_package.get("script_editor_sync_id") or block.get("script_editor_sync_id"),
                        "timeline_locked": bool(block.get("timeline_locked")),
                        "timing_policy": timing_policy,
                    })

                    with open(seg_meta_path, "w", encoding="utf-8") as f:
                        json.dump(voice_segments, f, indent=2, ensure_ascii=False)

            self.voice_segments = voice_segments
            if script_blocks and len(voice_segments) != len(script_blocks):
                missing_count = max(0, len(script_blocks) - len(voice_segments))
                self._step_fail(
                    "VOICE_SEGMENTS",
                    f"Thiếu {missing_count} file voice ({len(voice_segments)}/{len(script_blocks)} block). "
                    "App đã dừng trước khi nối/render để không tạo video hụt tiếng.",
                )
                return False
            if self.ai_package.get("recap2_beat_mode"):
                planned_voice = float(
                    self.ai_package.get("target_review_seconds")
                    or self.ai_package.get("planned_duration_seconds")
                    or 0.0
                )
                actual_voice = sum(
                    max(0.0, float(segment.get("audio_duration") or 0.0))
                    for segment in voice_segments
                    if isinstance(segment, dict)
                )
                self.ai_package["actual_voice_duration_seconds"] = round(actual_voice, 3)
                if planned_voice > 0:
                    self._log(
                        f"   RECAP2: voice thực {actual_voice:.1f}s | ngân sách chapter {planned_voice:.1f}s "
                        "(không ép cắt/dừng pipeline)"
                    )
            if editor_synced and (not self.ai_package.get("recap2_beat_mode")) and editor_actual_timing_errors:
                self.ai_package["script_editor_actual_tts_timing_errors"] = editor_actual_timing_errors
                try:
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                allow_render = str(
                    os.environ.get("AUTORECAP_ALLOW_RENDER_WITH_EDITOR_TTS_MISMATCH", "0") or "0"
                ).strip().lower() in {"1", "true", "yes", "on"}
                if not allow_render:
                    ids = ", ".join(str(item.get("block_id")) for item in editor_actual_timing_errors[:12])
                    if len(editor_actual_timing_errors) > 12:
                        ids += f", +{len(editor_actual_timing_errors) - 12}"
                    self._step_fail(
                        "VOICE_SEGMENTS",
                        "TTS thực tế vẫn lệch cảnh sau Script Editor: "
                        f"{len(editor_actual_timing_errors)} block ({ids}). "
                        "Cần mở Script Editor, bấm AI sửa review hoặc Tự chỉnh tốc độ lại cho các block này.",
                    )
                    return False
            if script_blocks:
                self.ai_package["script_blocks"] = script_blocks
                repaired_script = "\n\n".join(
                    str(block.get("text", "")).strip()
                    for block in script_blocks
                    if str(block.get("text", "")).strip()
                ).strip()
                if repaired_script:
                    self.ai_package["script"] = repaired_script
            try:
                with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                    json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            with open(seg_meta_path, "w", encoding="utf-8") as f:
                json.dump(voice_segments, f, indent=2, ensure_ascii=False)

            info = f" (resume {skipped}, moi {generated})" if skipped else f" ({generated} block)"
            self._step_done("VOICE_SEGMENTS", f"{len(voice_segments)} block{info}")
            return True

        except Exception as e:
            self._step_fail("VOICE_SEGMENTS", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 9: VOICE_CONCAT
    # ──────────────────────────────────────────────────────────────

    def step_voice_concat(self) -> bool:
        """Ghep cac block voice vao timeline theo start_in_video.

        Moi block da duoc atempo nen vua duration o step truoc.
        Dat moi block dung vi tri start_in_video + silence gap giua cac block
        -> voice khop dung tung canh video.
        """
        concat_path = os.path.join(self.output_dir, "voice_track.mp3")
        seg_meta_path = os.path.join(self.output_dir, "voice_segments.json")
        editor_synced_for_concat = bool(self.ai_package.get("script_editor_synced"))
        env_continuous = os.environ.get("AUTORECAP_CONTINUOUS_VOICE")
        # RECAP2 beat mode follows story/timeline continuity: keep narration
        # flowing across beat blocks so the review does not create silent gaps.
        # Set AUTORECAP_CONTINUOUS_VOICE=0 only when strict scene-pinned audio is needed.
        continuous_default = "1" if self._recap2_beat_mode_enabled() else "0"
        continuous_voice = str(env_continuous if env_continuous is not None else continuous_default).strip().lower() not in {
            "0", "false", "no", "off"
        }
        try:
            max_voice_gap = float(
                os.environ.get(
                    "AUTORECAP_MAX_VOICE_GAP",
                    "0.12" if editor_synced_for_concat else "0.35",
                )
                or ("0.12" if editor_synced_for_concat else "0.35")
            )
        except Exception:
            max_voice_gap = 0.12 if editor_synced_for_concat else 0.35
        max_voice_gap = max(0.0, min(max_voice_gap, 2.0))
        voice_concat_policy = {
            "version": 4,
            "continuous_voice": bool(continuous_voice),
            "max_voice_gap": round(max_voice_gap, 3),
            "script_editor_synced": bool(editor_synced_for_concat),
            "script_editor_sync_id": self.ai_package.get("script_editor_sync_id") if editor_synced_for_concat else None,
        }

        if (
            self._cached(concat_path)
            and self.ai_package.get("voice_concat_policy") == voice_concat_policy
            and (
                not os.path.exists(seg_meta_path)
                or os.path.getmtime(concat_path) >= os.path.getmtime(seg_meta_path)
            )
        ):
            self.concat_audio_path = concat_path
            self._resume_skip("VOICE_CONCAT", os.path.basename(concat_path))
            return True

        self._step_start("VOICE_CONCAT")
        try:
            from utils.helpers import FFmpegUtils
            ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
        except Exception:
            import shutil
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

        try:
            segs = sorted(self.voice_segments, key=lambda s: s.get("start_in_video", 0.0))
            if not segs:
                self._step_fail("VOICE_CONCAT", "Không có voice segment")
                return False

            def fmt(v): return f"{max(0.001, float(v)):.4f}"

            FADE = 0.030  # 30ms fade chong pop
            tmp_files = []
            try:
                # Step 1: chuan hoa moi block sang WAV + fade nhe
                norm_blocks = []
                for idx, seg in enumerate(segs):
                    src = seg.get("audio_path", "")
                    if not src or not os.path.exists(src):
                        norm_blocks.append(None)
                        continue
                    tmp = tempfile.NamedTemporaryFile(suffix=f"_n{idx:04d}.wav", delete=False).name
                    tmp_files.append(tmp)
                    # probe dur de tinh fade-out
                    pr = subprocess.run(
                        [ffmpeg_bin, "-i", src, "-f", "null", "-"],
                        **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True),
                    )
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", pr.stderr)
                    d = (int(m.group(1))*3600 + int(m.group(2))*60 +
                         int(m.group(3)) + int(m.group(4))/100) if m else 4.0
                    af = (f"afade=t=in:st=0:d={fmt(FADE)},"
                          f"afade=t=out:st={fmt(max(0, d - FADE))}:d={fmt(FADE)}")
                    r = subprocess.run([
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", src, "-af", af,
                        "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", tmp
                    ], **FFmpegUtils.subprocess_kwargs(
                        check=False, capture_output=True, text=True
                    ))
                    if r.returncode != 0:
                        norm_blocks.append(None)
                    else:
                        norm_blocks.append(tmp)

                # Step 2: build timeline voi silence gap
                timeline = []
                prev_end = 0.0
                strict_timeline_gaps = []
                try:
                    strict_gap_limit = float(os.environ.get("AUTORECAP_EDITOR_MAX_TIMING_DIFF_SECONDS", "0.0") or "0.0")
                except Exception:
                    strict_gap_limit = 0.0
                strict_gap_limit = max(0.0, strict_gap_limit)
                if continuous_voice:
                    self._log(f"   🔊 Continuous voice: gap toi da {max_voice_gap:.2f}s")
                for idx, seg in enumerate(segs):
                    nb = norm_blocks[idx]
                    if nb is None:
                        continue
                    desired_start = float(seg.get("start_in_video") or 0.0)
                    start = desired_start
                    # probe dur thuc cua block da chuan hoa
                    pr = subprocess.run(
                        [ffmpeg_bin, "-i", nb, "-f", "null", "-"],
                        **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True),
                    )
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", pr.stderr)
                    bdur = (int(m.group(1))*3600 + int(m.group(2))*60 +
                            int(m.group(3)) + int(m.group(4))/100) if m else 4.0

                    if continuous_voice and prev_end > 0:
                        gap_to_scene = desired_start - prev_end
                        if gap_to_scene > max_voice_gap:
                            start = prev_end + max_voice_gap
                        elif gap_to_scene < 0:
                            start = prev_end

                    gap = start - prev_end
                    if (
                        editor_synced_for_concat
                        and not continuous_voice
                        and prev_end > 0
                        and gap > strict_gap_limit + 0.001
                    ):
                        strict_timeline_gaps.append({
                            "block_id": seg.get("block_id") or idx + 1,
                            "gap_seconds": round(gap, 3),
                            "prev_voice_end": round(prev_end, 3),
                            "scene_start": round(desired_start, 3),
                        })
                    if gap > 0.01:
                        sil = tempfile.NamedTemporaryFile(suffix=f"_s{idx:04d}.wav", delete=False).name
                        tmp_files.append(sil)
                        subprocess.run([
                            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={fmt(gap)}",
                            "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", sil
                        ], **FFmpegUtils.subprocess_kwargs(
                            check=False, capture_output=True
                        ))
                        timeline.append(sil)
                    timeline.append(nb)
                    seg["actual_voice_start"] = round(start, 3)
                    seg["actual_voice_duration"] = round(bdur, 3)
                    seg["actual_voice_end"] = round(start + bdur, 3)
                    seg["voice_timeline_shift"] = round(start - desired_start, 3)
                    seg["voice_concat_policy"] = voice_concat_policy
                    prev_end = max(prev_end, start) + bdur

                try:
                    self.ai_package["voice_concat_policy"] = voice_concat_policy
                    self.ai_package["voice_concat_continuous"] = bool(continuous_voice)
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                    with open(os.path.join(self.output_dir, "voice_segments.json"), "w", encoding="utf-8") as f:
                        json.dump(segs, f, indent=2, ensure_ascii=False)
                    self.voice_segments = segs
                except Exception:
                    pass

                # Trailing silence den het video
                # RECAP2 ClipAssembler mode: video cuối = tổng TTS duration (không phải cut_video)
                # → không cần trailing silence fill cut_duration
                if self._recap2_beat_mode_enabled():
                    video_dur = float(prev_end or 0.0)  # voice kết thúc ở đây là hết
                else:
                    video_dur = self.cut_duration or 0.0
                trailing_gap = max(0.0, float(video_dur or 0.0) - float(prev_end or 0.0))
                if (
                    editor_synced_for_concat
                    and not continuous_voice
                    and strict_timeline_gaps
                ):
                    self.ai_package["voice_concat_timeline_gaps"] = strict_timeline_gaps
                    try:
                        with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                            json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                    preview = ", ".join(
                        f"{item.get('block_id')}:{item.get('gap_seconds')}s"
                        for item in strict_timeline_gaps[:12]
                    )
                    if len(strict_timeline_gaps) > 12:
                        preview += f", +{len(strict_timeline_gaps) - 12}"
                    self._step_fail(
                        "VOICE_CONCAT",
                        "Voice bị hụt giữa các cảnh sau Script Editor "
                        f"({len(strict_timeline_gaps)} khoảng hở: {preview}). "
                        "Cần mở Script Editor -> AI sửa review để viết lại các block trước khoảng hở.",
                    )
                    return False
                if (
                    editor_synced_for_concat
                    and not continuous_voice
                    and trailing_gap > strict_gap_limit + 0.001
                    # ClipAssembler mode: không fail vì video sẽ fit TTS, không phải TTS fit video
                    and not self._recap2_beat_mode_enabled()
                ):
                    self.ai_package["voice_concat_trailing_gap_seconds"] = round(trailing_gap, 3)
                    try:
                        with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                            json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                    self._step_fail(
                        "VOICE_CONCAT",
                        f"Voice kết thúc sớm hơn video băm {trailing_gap:.3f}s. "
                        "Không render im lặng cuối video; cần AI sửa review để viết thêm cho block cuối.",
                    )
                    return False
                if continuous_voice and self._recap2_beat_mode_enabled() and video_dur > 0:
                    try:
                        max_trailing_silence = float(
                            os.environ.get("AUTORECAP_RECAP2_MAX_TRAILING_SILENCE", "8.0") or "8.0"
                        )
                    except Exception:
                        max_trailing_silence = 8.0
                    max_trailing_silence = max(1.0, max_trailing_silence)
                    # ClipAssembler: voice track không cần fill toàn bộ cut_video
                    # ClipAssembler sẽ cắt clip đúng phần có voice → trailing gap OK
                    if trailing_gap > max_trailing_silence and not self._recap2_beat_mode_enabled():
                        self.ai_package["voice_concat_trailing_gap_seconds"] = round(trailing_gap, 3)
                        self.ai_package["voice_concat_total_voice_end_seconds"] = round(float(prev_end or 0.0), 3)
                        self.ai_package["voice_concat_video_duration_seconds"] = round(float(video_dur or 0.0), 3)
                        try:
                            with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                        self._step_fail(
                            "VOICE_CONCAT",
                            f"Tổng voice kết thúc sớm hơn video băm {trailing_gap:.1f}s. "
                            "Không render phần cuối im lặng; cần mở Script Editor -> AI sửa review để viết dày hơn/không lặp cho các block cuối hoặc block quá ngắn.",
                        )
                        return False

                if video_dur > prev_end + 0.01:
                    trail = tempfile.NamedTemporaryFile(suffix="_trail.wav", delete=False).name
                    tmp_files.append(trail)
                    subprocess.run([
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={fmt(video_dur - prev_end)}",
                        "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", trail
                    ], **FFmpegUtils.subprocess_kwargs(
                        check=False, capture_output=True
                    ))
                    timeline.append(trail)

                if not timeline:
                    self._step_fail("VOICE_CONCAT", "Timeline rong")
                    return False

                # Step 3: concat + loudnorm
                lst = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                for p in timeline:
                    lst.write(f"file '{p.replace(chr(92), '/')}'\n")
                lst.close()

                merged = tempfile.NamedTemporaryFile(suffix="_m.wav", delete=False).name
                tmp_files.append(merged)
                r = subprocess.run([
                    ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", lst.name,
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", merged
                ], **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ))
                if r.returncode != 0:
                    raise RuntimeError(f"Concat that bai: {r.stderr[:200]}")

                r = subprocess.run([
                    ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", merged,
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,highpass=f=80",
                    "-c:a", "libmp3lame", "-b:a", "192k", concat_path
                ], **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ))
                if r.returncode != 0:
                    raise RuntimeError(f"Loudnorm that bai: {r.stderr[:200]}")

                try: os.unlink(lst.name)
                except Exception: pass

            finally:
                for f in tmp_files:
                    try:
                        if f and os.path.exists(f): os.unlink(f)
                    except Exception: pass

            self.concat_audio_path = concat_path
            size_kb = os.path.getsize(concat_path) // 1024
            self._step_done("VOICE_CONCAT",
                            f"voice_track.mp3 ({size_kb}KB) | {len(segs)} block synced")
            return True

        except Exception as e:
            self._step_fail("VOICE_CONCAT", str(e))
            return False

    # ──────────────────────────────────────────────────────────────

    def _repair_recorded_voice_alignment(self) -> bool:
        """Repair failed scene anchors and regenerate audio before writing SRT."""
        from core.srt_alignment import SrtAlignmentValidator
        from core.ai_engine import AIEngine
        import copy
        try:
            for attempt in range(3):
                package = copy.deepcopy(self.ai_package)
                spoken = {int(seg['block_id']): seg.get('text','') for seg in self.voice_segments}
                for index, block in enumerate(package.get('script_blocks') or [],1):
                    bid = int(block.get('block_id') or index)
                    if bid in spoken: block['text'] = spoken[bid]
                # These are scene-anchor checks on each block's recorded words,
                # not source timestamps compared to a continuous audio timeline.
                _,report = SrtAlignmentValidator.annotate_package(package,self.render_blocks)
                bad = [item for item in report.get('blocks',[]) if item.get('errors')]
                if not bad:
                    self._log('   ✅ Lời voice đã qua kiểm tra nội dung bám cảnh')
                    return True
                if attempt == 2:
                    self._step_fail('VOICE_SEGMENTS','Tự sửa 2 lượt vẫn còn block chưa bám cảnh: '+', '.join(str(b['block_id']) for b in bad))
                    return False
                self._log('   🔧 Tự sửa lời kể block '+', '.join(str(b['block_id']) for b in bad)+' rồi tạo lại voice/SRT')
                self.ai_package = package
                ai = AIEngine(api_key=self.gemini_api_key)
                changed,_ = self._repair_srt_alignment_final_pass(ai,report,max_blocks=len(bad))
                if not changed:
                    self._step_fail('VOICE_SEGMENTS','Không tạo được nội dung sửa cho các block lỗi')
                    return False
                bad_ids = {int(item['block_id']) for item in bad}
                texts = dict(spoken)
                for index,block in enumerate(self.ai_package.get('script_blocks') or [],1):
                    bid = int(block.get('block_id') or index)
                    if bid in bad_ids: texts[bid] = block.get('text','')
                self._automatic_voice_repair_texts = texts
                with open(os.path.join(self.output_dir,'ai_package.json'),'w',encoding='utf-8') as file:
                    json.dump(self.ai_package,file,ensure_ascii=False,indent=2)
                try:
                    if not self.step_voice_segments() or not self.step_voice_concat():
                        return False
                finally:
                    self._automatic_voice_repair_texts = {}
            return False
        except Exception as error:
            self._step_fail('VOICE_SEGMENTS',f'Không tự sửa được lời voice: {error}')
            return False

    def step_voice_srt(self) -> bool:
        """Đồng bộ phụ đề giọng đọc thuyết minh với timeline video."""
        srt_path = os.path.join(self.output_dir, "voice_subtitles.srt")
        seg_meta_path = os.path.join(self.output_dir, "voice_segments.json")
        continuous_voice_srt = bool(self.ai_package.get("voice_concat_continuous"))
        default_render_timeline = "0" if continuous_voice_srt else "1"
        voice_srt_timeline_mode = "render_blocks_v1" if str(
            os.environ.get("AUTORECAP_VOICE_SRT_RENDER_TIMELINE", default_render_timeline) or default_render_timeline
        ).strip().lower() not in {"0", "false", "no", "off"} else "actual_voice_v1"
        voice_srt_policy = {
            "version": 3,
            "timeline": voice_srt_timeline_mode,
            "script_editor_synced": bool(self.ai_package.get("script_editor_synced")),
            "script_editor_sync_id": self.ai_package.get("script_editor_sync_id")
            if self.ai_package.get("script_editor_synced") else None,
            "voice_concat_continuous": bool(continuous_voice_srt),
        }

        try:
            from core.premium_pipeline import PremiumReviewPipeline

            def restore_text(value):
                return PremiumReviewPipeline.restore_known_vietnamese_phrases(value)
        except Exception:
            def restore_text(value):
                return str(value or "")

        try:
            from core.ai_engine import AIEngine as _SubtitleAIEngine
        except Exception:
            _SubtitleAIEngine = None

        bad_subtitle_markers = (
            "ở cảnh này,",
            "điểm cần theo sát",
            "đúng với phần đang xuất hiện trên video",
            "setup / xây dựng",
            "thay vì lướt qua",
            "như một cảnh chuyển",
            "cần làm rõ setup",
            "cần làm rõ",
                    "vì thế đoạn này",
                    "vì thế cảnh này",
                    "block ",
                    "book ",
                    "manh mối ",
                    "ở mốc ",
                    "khiến tình thế trở nên căng hơn",
                    "buộc các nhân vật phải bước tiếp",
                    "khoảnh khắc này không chỉ là một phản ứng thoáng qua",
                    "giúp cảnh này có điểm tựa rõ hơn",
                    "để lời kể không trôi qua như một đoạn chuyển cảnh",
                    "làm bầu không khí căng hơn",
                    "đẩy câu chuyện của",
                    'trở thành manh mối cần chú ý',
                    'nối trực tiếp cảm xúc',
                    'mỗi phản ứng nhỏ',
                    'nhịp review bám sát',
                )

        def subtitle_text_needs_rewrite(value):
            text = str(value or "")
            lower = text.lower()
            if any(marker in lower for marker in bad_subtitle_markers):
                return True
            folded = _fold_for_voice_srt(text)
            if any(marker in folded for marker in (
                "ap luc trong canh tang len ro ret",
                "mo ra mot huong nghi van moi",
                "buoc nhan vat phai xu ly than trong hon",
                "tu sau bao nhieu lan suy xet",
                "ap luc cua vu viec tiep tuc don len cac nhan vat",
                "mach dieu tra chuyen sang mot nhip cang hon",
                "canh nay giu nhip dan chuyen di tiep",
            )):
                return True
            if looks_truncated_review_text(text):
                return True
            if has_repeated_review_ngram(text):
                return True
            return self._strip_cjk_text(text) != text.strip()

        def _fold_for_voice_srt(value):
            normalized = unicodedata.normalize("NFKD", str(value or ""))
            no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return re.sub(r"\s+", " ", no_marks.lower()).strip()

        def looks_truncated_review_text(value):
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if not text:
                return False
            words = re.findall(r"[A-Za-zÀ-ỹà-ỹĐđ]+", text)
            if len(words) < 8:
                return False
            folded_tail = _fold_for_voice_srt(words[-1])
            dangling = {
                "v", "p", "c", "n", "t", "ch", "ng", "cong", "ngu", "nguoi",
                "va", "la", "neu", "nhung", "khong", "duoc", "se", "da", "de",
                "voi", "cua", "trong", "nhu", "thi", "nen", "bi", "cho", "mot",
            }
            if folded_tail in dangling:
                return True
            if text[-1] not in ".!?…" and len(words) >= 18:
                tail2 = " ".join(_fold_for_voice_srt(w) for w in words[-2:])
                if any(tail2.endswith(marker) for marker in (" khong", " duoc", " trong", " nguoi", " cong")):
                    return True
            return False

        def has_repeated_review_ngram(value):
            folded = _fold_for_voice_srt(value)
            stop = {
                "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do",
                "day", "khi", "neu", "den", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
                "khong", "nguoi", "canh", "phim", "dieu",
            }
            tokens = [token for token in re.findall(r"[a-z0-9]+", folded) if len(token) > 2 and token not in stop]
            if len(tokens) < 14:
                return False
            for n in (6, 8):
                seen = set()
                for pos in range(len(tokens) - n + 1):
                    gram = " ".join(tokens[pos:pos + n])
                    if gram in seen:
                        return True
                    seen.add(gram)
            return False

        # Resume
        if self._cached(srt_path):
            try:
                if self._cached(seg_meta_path) and os.path.getmtime(seg_meta_path) > os.path.getmtime(srt_path):
                    self._log("   VOICE_SRT cache cu hon voice_segments -> ghi lai SRT")
                    raise ValueError("stale_voice_srt")
                if self.ai_package.get("voice_srt_timeline_policy") != voice_srt_policy:
                    self._log("   VOICE_SRT policy moi -> ghi lai SRT")
                    raise ValueError("voice_srt_policy_changed")
                cached_text = open(srt_path, encoding="utf-8").read()
                if restore_text(cached_text) == cached_text:
                    self.voice_srt_path = srt_path
                    if not self._validate_voice_srt_alignment(srt_path):
                        self._log("   VOICE_SRT cache không đạt alignment -> ghi lại SRT")
                        raise ValueError("cached_voice_srt_alignment_failed")
                    self._resume_skip("VOICE_SRT", os.path.basename(srt_path))
                    return True
                self._log("   VOICE_SRT cache còn câu padding không dấu -> ghi lại SRT")
            except Exception:
                pass

        self._step_start("VOICE_SRT")
        try:
            if not self.voice_segments and os.path.exists(seg_meta_path):
                try:
                    self.voice_segments = json.load(open(seg_meta_path, encoding="utf-8"))
                except Exception:
                    self.voice_segments = []
            if not self.voice_segments:
                self._step_fail("VOICE_SRT", "Không có voice_segments để tạo subtitle")
                return False

            def _id(item: dict, fallback: int) -> int:
                try:
                    return int(item.get("block_id") or item.get("book_id") or fallback)
                except Exception:
                    return fallback

            render_by_id = {
                _id(rb, index): rb
                for index, rb in enumerate(self.render_blocks or [], 1)
                if isinstance(rb, dict)
            }
            package_text_by_id = {}
            package_meta_by_id = {}
            package_meta_fields = (
                "chapter_index", "chapter_position", "chapter_size", "chapter_synopsis",
                "chapter_prev_tail", "chapter_logline", "next_chapter_synopsis", "narrative_goal", "bridge_line",
                "bridge_to_next", "character_focus", "srt_anchor", "visual_anchor",
                "review_sync_source", "map_reduce_outline_valid", "chapter_range",
                "outline_coverage", "timeline_locked", "voice_narration_style", "voice_source",
                "estimated_tts_duration", "estimated_final_voice_duration",
                "silent_padding_seconds", "needs_expand_words",
                "auto_time_speed_needed", "auto_time_speed_used",
                "auto_time_status", "auto_time_policy",
            )
            for block_index, block in enumerate(self.ai_package.get("script_blocks") or [], 1):
                if not isinstance(block, dict):
                    continue
                bid_for_package = _id(block, block_index)
                package_text_by_id[bid_for_package] = str(block.get("text") or "").strip()
                package_meta_by_id[bid_for_package] = {
                    key: block.get(key)
                    for key in package_meta_fields
                    if key in block and block.get(key) not in (None, "", [])
                }
            editor_synced = bool(self.ai_package.get("script_editor_synced"))
            # voice_segments.text records what TTS actually read. Editor changes
            # must regenerate audio upstream, never replace this transcript here.
            try:
                from core.srt_alignment import SrtAlignmentValidator as _VoiceSrtAlign
            except Exception:
                _VoiceSrtAlign = None

            def fallback_voice_line(seg, current_text=""):
                try:
                    bid = int(seg.get("block_id") or 0)
                except Exception:
                    bid = 0
                rb = render_by_id.get(bid) or {}
                start_hint = float(seg.get("start_in_video") or rb.get("start_in_final_video") or 0.0)
                candidates = [
                    rb.get("srt_must_mention"),
                    rb.get("cut_visible_srt"),
                    rb.get("triangulated_srt_anchor"),
                    rb.get("srt_anchor"),
                    rb.get("dialogue_text"),
                    rb.get("visual_anchor"),
                    rb.get("visual_hint"),
                ]
                script_text = self._strip_cjk_text(package_text_by_id.get(bid) or current_text or "")
                script_text = re.sub(r"\s+", " ", script_text).strip()
                if script_text and not subtitle_text_needs_rewrite(script_text) and len(script_text.split()) >= 6:
                    return script_text
                focus = self._usable_vietnamese_focus(*candidates, limit=115)
                if focus:
                    focus_words = focus.split()
                    focus_folded = _fold_for_voice_srt(focus)
                    if (
                        len(focus_words) > 8
                        or any(ch in focus for ch in ".!?;:")
                        or looks_truncated_review_text(focus)
                        or any(marker in focus_folded for marker in (" ap luc trong canh", " mo ra mot huong", " buoc nhan vat"))
                    ):
                        focus = ""
                if focus:
                    variants = [
                        f"{focus} trở thành điểm khiến các nhân vật phải dè chừng hơn trước bước đi kế tiếp.",
                        f"{focus} khiến cuộc điều tra có thêm một hướng cần theo sát, thay vì chỉ dừng ở lời khai bên ngoài.",
                        f"Khi {focus} xuất hiện, thế cờ bắt đầu đổi hướng và nguy cơ phía sau trở nên khó lường hơn.",
                    ]
                    return variants[bid % len(variants)]
                variants = [
                    "Áp lực của vụ việc tiếp tục dồn lên các nhân vật, khiến lựa chọn kế tiếp trở nên khó đoán hơn.",
                    "Mạch điều tra chuyển sang một nhịp căng hơn, nơi mỗi phản ứng đều có thể kéo theo hậu quả mới.",
                    "Cảnh này giữ nhịp dẫn chuyện đi tiếp, đồng thời đặt thêm sức nặng cho quyết định phía sau.",
                ]
                return variants[bid % len(variants)]

            def remove_embedded_dialogue_repetition(text):
                """Drop generated anchor tails that paste raw SRT dialogue back into narration."""
                cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
                if not cleaned:
                    return cleaned
                marker_patterns = [
                    r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+làm\s+rõ\s+bước\s+ngoặt.+$",
                    r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+khiến\s+tình\s+thế.+$",
                    r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+?\s+đẩy\s+câu\s+chuyện.+$",
                    r"\s+(?:Chi\s+tiết|Manh\s+mối)\s+.+$",
                ]
                for pattern in marker_patterns:
                    next_text = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
                    if next_text and next_text != cleaned:
                        cleaned = next_text
                        break

                def _fold(value):
                    normalized = unicodedata.normalize("NFKD", str(value or ""))
                    no_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
                    return re.sub(r"\s+", " ", no_marks.lower()).strip()

                def _tokens(value):
                    stop = {
                        "va", "la", "thi", "roi", "co", "mot", "nhung", "nhu", "cho", "voi", "nay", "do",
                        "day", "khi", "neu", "den", "trong", "tu", "sau", "truoc", "cua", "cac", "de",
                        "khong", "nguoi", "canh", "phim", "chi", "tiet", "manh", "moi",
                    }
                    return [
                        token for token in re.findall(r"[a-z0-9]+", _fold(value))
                        if len(token) > 2 and token not in stop
                    ]

                def _is_generated_padding_artifact(value):
                    folded = _fold(value)
                    return (
                        "khoanh khac nay khong chi la mot phan ung thoang qua" in folded
                        or bool(re.search(r"\bvoi\s+[^.!?]{0,160}khoanh\s+khac\b", folded))
                        or ("lam bau khong khi cang hon" in folded and "day cau chuyen" in folded)
                        or "tro thanh manh moi can chu y" in folded
                        or "noi truc tiep cam xuc" in folded
                        or "moi phan ung nho" in folded
                        or "nhip review bam sat" in folded
                        or "ap luc trong canh tang len ro ret" in folded
                        or "mo ra mot huong nghi van moi" in folded
                        or "buoc nhan vat phai xu ly than trong hon" in folded
                        or "ap luc cua vu viec tiep tuc don len cac nhan vat" in folded
                        or "mach dieu tra chuyen sang mot nhip cang hon" in folded
                        or "canh nay giu nhip dan chuyen di tiep" in folded
                        or (
                            "giup canh nay co diem tua ro hon" in folded
                            and "loi ke khong troi qua" in folded
                        )
                    )

                parts = [
                    re.sub(r"\s+", " ", part).strip(" .")
                    for part in re.split(r"\s*(?:[.!?]+|\s+\.\s+|\s*\|\s*)\s*", cleaned)
                    if re.sub(r"\s+", " ", part).strip(" .")
                ]
                if len(parts) <= 1:
                    return "" if _is_generated_padding_artifact(cleaned) else cleaned.strip()

                kept = []
                seen_tokens = set()
                for part in parts:
                    if _is_generated_padding_artifact(part):
                        continue
                    tokens = _tokens(part)
                    token_set = set(tokens)
                    if token_set and seen_tokens:
                        overlap = len(token_set & seen_tokens) / max(1, len(token_set))
                        if overlap >= 0.72:
                            continue
                    kept.append(part)
                    seen_tokens.update(token_set)
                result = ". ".join(kept).strip()
                if result and result[-1] not in ".!?":
                    result += "."
                return result or cleaned.strip()

            def trim_truncated_voice_tail(text):
                cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
                if not cleaned or not looks_truncated_review_text(cleaned):
                    return cleaned
                # Prefer keeping the last complete sentence. This removes AI/web
                # responses that were cut mid-word, e.g. "công v" or "Tôi p".
                last_punct = max(cleaned.rfind("."), cleaned.rfind("!"), cleaned.rfind("?"), cleaned.rfind("…"))
                if last_punct >= 35:
                    return cleaned[:last_punct + 1].strip()
                last_comma = max(cleaned.rfind(","), cleaned.rfind(";"), cleaned.rfind(":"))
                if last_comma >= 35:
                    candidate = cleaned[:last_comma].strip()
                    if candidate and candidate[-1] not in ".!?":
                        candidate += "."
                    return candidate
                words = cleaned.split()
                while len(words) > 6 and looks_truncated_review_text(" ".join(words)):
                    words.pop()
                candidate = " ".join(words).strip()
                if candidate and candidate[-1] not in ".!?":
                    candidate += "."
                return candidate

            def clean_voice_subtitle_text(value, seg):
                try:
                    bid_for_editor = int(seg.get("block_id") or 0)
                except Exception:
                    bid_for_editor = 0
                if editor_synced and package_text_by_id.get(bid_for_editor):
                    source_editor_text = remove_embedded_dialogue_repetition(package_text_by_id[bid_for_editor])
                    if _SubtitleAIEngine is not None:
                        try:
                            cleaned_editor = _SubtitleAIEngine._clean_tts_text(source_editor_text)
                        except Exception:
                            cleaned_editor = source_editor_text
                    else:
                        cleaned_editor = source_editor_text
                    cleaned_editor = self._strip_cjk_text(cleaned_editor)
                    cleaned_editor = remove_embedded_dialogue_repetition(cleaned_editor)
                    cleaned_editor = trim_truncated_voice_tail(cleaned_editor)
                    cleaned_editor = re.sub(r"\s+", " ", str(cleaned_editor or "")).strip()
                    if not cleaned_editor or subtitle_text_needs_rewrite(cleaned_editor):
                        cleaned_editor = fallback_voice_line(seg, cleaned_editor)
                        cleaned_editor = trim_truncated_voice_tail(remove_embedded_dialogue_repetition(cleaned_editor))
                    return cleaned_editor
                raw = restore_text(value)
                raw = remove_embedded_dialogue_repetition(raw)
                cleaned = raw
                if _SubtitleAIEngine is not None:
                    try:
                        cleaned = _SubtitleAIEngine._clean_review_script_text(raw)
                    except Exception:
                        cleaned = raw
                cleaned = self._strip_cjk_text(cleaned)
                cleaned = remove_embedded_dialogue_repetition(cleaned)
                cleaned = trim_truncated_voice_tail(cleaned)
                cleaned = re.sub(r"\s+", " ", str(cleaned or "")).strip()
                if subtitle_text_needs_rewrite(cleaned) or len(cleaned.split()) < 5:
                    cleaned = fallback_voice_line(seg, cleaned)
                cleaned = self._strip_cjk_text(cleaned)
                cleaned = remove_embedded_dialogue_repetition(cleaned)
                cleaned = trim_truncated_voice_tail(cleaned)
                try:
                    bid = int(seg.get("block_id") or 0)
                except Exception:
                    bid = 0
                rb = render_by_id.get(bid) or {}
                required_anchor = (
                    rb.get("srt_must_mention")
                    or rb.get("cut_visible_srt")
                    or rb.get("triangulated_srt_anchor")
                    or ""
                )
                if required_anchor and _VoiceSrtAlign is not None and not editor_synced:
                    try:
                        if not _VoiceSrtAlign._mentions_anchor(cleaned, required_anchor, strict=True):
                            cleaned = fallback_voice_line(seg, cleaned)
                    except Exception:
                        pass
                return cleaned

            srt_lines = []
            idx = 1
            cleaned_by_id = {}
            use_render_timeline = str(
                os.environ.get("AUTORECAP_VOICE_SRT_RENDER_TIMELINE", default_render_timeline) or default_render_timeline
            ).strip().lower() not in {"0", "false", "no", "off"}

            for seg in self.voice_segments:
                if use_render_timeline:
                    start_t = seg.get("start_in_video", seg.get("actual_voice_start", 0.0))
                    dur = seg.get("target_duration") or seg.get("actual_voice_duration") or seg.get("audio_duration") or 4.0
                else:
                    start_t = seg.get("actual_voice_start", seg.get("start_in_video", 0.0))
                    dur = (
                        seg.get("actual_voice_duration")
                        or seg.get("audio_duration")
                        or seg.get("target_duration")
                        or 4.0
                    )
                end_t = start_t + dur
                # Audio is already synthesized. Never rewrite its transcript here.
                text = ' '.join(str(seg.get('text', '')).split())
                try:
                    cleaned_by_id[int(seg.get("block_id") or idx)] = text
                except Exception:
                    cleaned_by_id[idx] = text

                def fmt_ts(s):
                    h = int(s // 3600)
                    m = int((s % 3600) // 60)
                    sec = s % 60
                    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")

                srt_lines.append(str(idx))
                srt_lines.append(f"{fmt_ts(start_t)} --> {fmt_ts(end_t)}")
                words = text.split()
                lines = []
                cur = []
                for w in words:
                    cur.append(w)
                    if len(" ".join(cur)) > 40:
                        if cur[:-1]:
                            lines.append(" ".join(cur[:-1]))
                        cur = [w]
                if cur:
                    lines.append(" ".join(cur))
                srt_lines.append("\n".join(lines))
                srt_lines.append("")
                idx += 1

            if cleaned_by_id and self.ai_package.get("script_blocks"):
                updated_blocks = []
                for block_index, block in enumerate(self.ai_package.get("script_blocks") or [], 1):
                    if not isinstance(block, dict):
                        continue
                    item = dict(block)
                    bid = _id(item, block_index)
                    if cleaned_by_id.get(bid):
                        item["text"] = cleaned_by_id[bid]
                        item["voice_srt_text_synced"] = True
                        item["script_editor_synced"] = bool(editor_synced or item.get("script_editor_synced"))
                    updated_blocks.append(item)
                if updated_blocks:
                    self._log("   🔒 VOICE_SRT giữ nguyên lời đã tạo audio; không xóa câu sau TTS")
                    self.ai_package["script_blocks"] = updated_blocks
                    self.ai_package["script"] = "\n\n".join(
                        str(block.get("text", "")).strip()
                        for block in updated_blocks
                        if str(block.get("text", "")).strip()
                    ).strip()
                    self.ai_package["subtitle_chunks"] = [
                        block["text"] for block in updated_blocks if block.get("text")
                    ]

            with open(srt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(srt_lines))

            self.voice_srt_path = srt_path
            if not self._validate_voice_srt_alignment(srt_path):
                self._step_fail("VOICE_SRT", "Phụ đề không khớp nội dung/thời gian voice đã tạo; xem VOICE_CAPTIONS trong log")
                return False
            try:
                self.ai_package["voice_srt_validated"] = True
                self.ai_package["voice_srt_validated_path"] = srt_path
                self.ai_package["voice_srt_validated_mtime"] = os.path.getmtime(srt_path)
                self.ai_package["voice_srt_timeline_policy"] = voice_srt_policy
                pkg_file = os.path.join(self.output_dir, "ai_package.json")
                with open(pkg_file, "w", encoding="utf-8") as f:
                    json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            self._step_done("VOICE_SRT", f"{idx - 1} subtitle → {srt_path}")
            return True

        except Exception as e:
            self._step_fail("VOICE_SRT", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # STEP 11: RENDER_FINAL
    # ──────────────────────────────────────────────────────────────

    def step_render_final(self) -> bool:
        """Ghép clip theo voice thật thành video recap hoàn chỉnh.

        Ngân sách review chỉ định hướng AI_FULL, không ép duration ở bước render.
        Tiếng gốc bị tắt hoàn toàn (tránh bản quyền), chỉ giữ voice review.
        """
        movie_safe = "".join(
            c if c.isalnum() or c in " _-" else "_"
            for c in (self.movie_title or "recap")
        ).strip().replace(" ", "_")[:40]
        final_path = os.path.join(self.output_dir, f"{movie_safe}_final.mp4")
        from core.preview_design import apply_design, fingerprint
        design = dict(self.preview_design)
        if design.get('burn') and not design.get('srt'):
            design['srt'] = self.voice_srt_path
        render_final_policy = {
            "preview_design": fingerprint(design),
            "title_layout": {
                "placement": "inside_source_frame_v2_flush_edges",
                "header": self.header_text, "footer": self.footer_text,
                "header_size": self.header_font_size, "footer_size": self.footer_font_size,
                "header_color": list(self.header_color), "footer_color": list(self.footer_color),
                "header_bar_color": list(self.header_bar_color), "footer_bar_color": list(self.footer_bar_color),
            },
            "version": 8,
            "clip_assembler_source_override": True,
            "duration_policy": "actual_voice_no_pad_no_global_atempo",
            "scene_sync_policy": "actual_tts_sentence_timeline_to_srt_evidence",
            "target_review_seconds": round(float((self.max_video_minutes * 60.0) if self.max_video_minutes else 0.0), 3),
            "script_editor_synced": bool(self.ai_package.get("script_editor_synced")),
            "script_editor_sync_id": self.ai_package.get("script_editor_sync_id")
            if self.ai_package.get("script_editor_synced") else None,
            "voice_concat_policy": self.ai_package.get("voice_concat_policy"),
            "voice_srt_timeline_policy": self.ai_package.get("voice_srt_timeline_policy"),
        }

        # Resume
        if self._cached(final_path):
            stale_reason = ""
            concat_dep = self.concat_audio_path or os.path.join(self.output_dir, "voice_track.mp3")
            srt_dep = self.voice_srt_path or os.path.join(self.output_dir, "voice_subtitles.srt")
            for dep_path, dep_name in (
                (concat_dep, "voice_track"),
                (srt_dep, "voice_subtitles"),
            ):
                try:
                    if dep_path and os.path.exists(dep_path) and os.path.getmtime(dep_path) > os.path.getmtime(final_path):
                        stale_reason = dep_name
                        break
                except Exception:
                    pass
            if self.ai_package.get("render_final_policy") != render_final_policy:
                stale_reason = stale_reason or "script_editor_sync"
            if stale_reason:
                self._log(f"   RENDER_FINAL cache cu hon {stale_reason} -> render lai")
            else:
                self.final_video_path = final_path
                self._resume_skip("RENDER_FINAL", os.path.basename(final_path))
                return True

        self._step_start("RENDER_FINAL")
        try:
            # An interrupted design pass must not leave a raw output eligible
            # for resume under the previous successful render policy.
            self.ai_package.pop("render_final_policy", None)
            with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as cache:
                json.dump(self.ai_package, cache, ensure_ascii=False, indent=2)
            if not self.concat_audio_path or not os.path.exists(self.concat_audio_path):
                self._step_fail("RENDER_FINAL", "Không có voice track (voice_track.mp3)")
                return False

            try:
                from utils.helpers import FFmpegUtils
                ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
            except Exception:
                import shutil
                ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

            # ── RECAP2.0 STYLE: ClipAssembler ───────────────────────────────
            # Khi recap2_beat_mode bật: cắt clip fit TTS duration thay vì
            # dùng cut_video cố định + atempo.
            if self._recap2_beat_mode_enabled() and self.voice_segments and self.ai_package.get("script_blocks"):
                try:
                    from core.clip_assembler import assemble_clip_based_video
                    from engine.video_engine import VideoEngine as _VE

                    # Build title overlay filter
                    h_text = getattr(self, "header_text", "") or ""
                    f_text = getattr(self, "footer_text", "") or ""
                    title_vf = ""
                    if h_text.strip() or f_text.strip():
                        try:
                            title_vf = _VE._build_title_overlay_filter(
                                header=h_text, footer=f_text,
                                header_color=getattr(self, "header_color", (255, 255, 0)),
                                footer_color=getattr(self, "footer_color", (255, 255, 255)),
                                header_bar_color=getattr(self, "header_bar_color", (255, 0, 0)),
                                footer_bar_color=getattr(self, "footer_bar_color", (0, 174, 255)),
                                header_font_size=getattr(self, "header_font_size", 80),
                                footer_font_size=getattr(self, "footer_font_size", 60),
                                header_pos=getattr(self, "header_pos", None),
                                footer_pos=getattr(self, "footer_pos", None),
                                frame_width=int(
                                    (self.metadata.get("video", {}) or {}).get("width")
                                    or 1920
                                ),
                            )
                        except Exception:
                            title_vf = ""

                    # Load SRT segments cho dialogue center
                    srt_segs = []
                    if self.transcript_srt and os.path.exists(self.transcript_srt):
                        try:
                            from core.srt_processor import SRTParser
                            srt_segs = SRTParser.parse_srt(self.transcript_srt)
                        except Exception:
                            pass

                    self._log("   🎬 RECAP2.0 ClipAssembler: cắt clip fit TTS duration...")
                    ok = assemble_clip_based_video(
                        source_video=self.video_path,
                        script_blocks=self.ai_package.get("script_blocks") or [],
                        render_blocks=self.render_blocks,
                        voice_segments=self.voice_segments,
                        scenes=self.scenes,
                        srt_segments=srt_segs,
                        output_path=final_path,
                        ffmpeg_bin=ffmpeg_bin,
                        ffprobe_bin=self._ffprobe(),
                        bg_music_path=getattr(self, "bg_music_path", "") or "",
                        header_vf=title_vf,
                        # Chỉ truyền ngân sách để log. ClipAssembler không được
                        # fail/pad/atempo nhằm ép final video về giá trị này.
                        target_duration_seconds=(self.max_video_minutes * 60.0) if self.max_video_minutes else 0.0,
                        log=self._log,
                    )
                    if ok and os.path.exists(final_path):
                        apply_design(final_path, design)
                        final_duration = self._media_duration(final_path)
                        if final_duration <= 0:
                            self._step_fail(
                                "RENDER_FINAL",
                                "ClipAssembler đã tạo file nhưng không đọc được thời lượng video cuối.",
                            )
                            return False

                        # ClipAssembler vừa mux từng voice block vào đúng clip và đã
                        # tự QA tổng thời lượng block ở cuối assemble_clip_based_video.
                        # voice_track.mp3 được ghép ở bước VOICE_CONCAT theo timeline
                        # trung gian nên có thể chứa rounding/khoảng đệm khác tổng các
                        # block. Không dùng file đó để đánh rớt một video đã QA hợp lệ.
                        concat_voice_duration = self._media_duration(self.concat_audio_path)
                        concat_delta = (
                            final_duration - concat_voice_duration
                            if concat_voice_duration > 0 else 0.0
                        )
                        if concat_voice_duration > 0 and abs(concat_delta) > 0.50:
                            self._log(
                                f"   ℹ️ Voice track trung gian {concat_voice_duration:.2f}s "
                                f"khác video cuối {final_duration:.2f}s ({concat_delta:+.2f}s). "
                                "ClipAssembler dùng voice từng block nên không lấy sai số này làm lỗi render."
                            )
                        self.final_video_path = final_path
                        self.ai_package["render_final_policy"] = render_final_policy
                        self.ai_package["render_final_qa"] = {
                            "passed": True,
                            "video_duration": round(final_duration, 3),
                            "policy": "clip_assembler_block_voice_authoritative",
                            "concat_voice_duration_diagnostic": round(concat_voice_duration, 3),
                            "concat_difference_diagnostic": round(concat_delta, 3),
                        }
                        with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                            json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        size_mb = round(os.path.getsize(final_path) / 1e6, 1)
                        self._step_done("RENDER_FINAL", f"{os.path.basename(final_path)} ({size_mb} MB) [RECAP2 style]")
                        return True
                    else:
                        self._step_fail(
                            "RENDER_FINAL",
                            "ClipAssembler RECAP2 thất bại. Không fallback render cũ để tránh video cuối bị im lặng.",
                        )
                        return False
                except Exception as ca_e:
                    self._step_fail(
                        "RENDER_FINAL",
                        f"ClipAssembler RECAP2 lỗi: {ca_e}. Không fallback render cũ để tránh video cuối bị im lặng.",
                    )
                    return False
            # ── END RECAP2.0 CLIP ASSEMBLER ──────────────────────────────────
                import shutil
                ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

            if not self.cut_video_path or not os.path.exists(self.cut_video_path):
                self._step_fail("RENDER_FINAL", "Không có video băm cho renderer legacy")
                return False

            # Lấy duration thực của video băm bằng ffprobe
            try:
                from utils.helpers import FFmpegUtils as _FFU
                _ffprobe = _FFU.ffprobe_executable()
            except Exception:
                import shutil as _sh
                _ffprobe = _sh.which("ffprobe") or "ffprobe"

            _dur_result = subprocess.run(
                [_ffprobe, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", self.cut_video_path],
                **FFmpegUtils.subprocess_kwargs(capture_output=True, text=True),
            )
            try:
                video_duration = float(_dur_result.stdout.strip())
            except Exception:
                video_duration = self.cut_duration or 0.0
            self._log(
                f"   🎬 Ghép {os.path.basename(self.cut_video_path)} ({video_duration:.1f}s) "
                f"+ {os.path.basename(self.concat_audio_path)}"
            )

            # ── AUDIO SEPARATOR (RECAP2.0 upgrade) ──────────────────────────
            # Ưu tiên: file nhạc nền user chọn (bgm_path) → dùng trực tiếp
            # Fallback: tách từ video gốc (chỉ khi AUTORECAP_BG_MUSIC=1)
            # Mặc định: không có nhạc nền (tránh bản quyền)
            bg_music_enabled = str(os.environ.get("AUTORECAP_BG_MUSIC", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
            instrumental_path = ""

            # Ưu tiên 1: file nhạc nền user chọn trực tiếp
            user_bgm = getattr(self, "bg_music_path", "") or ""
            if user_bgm and os.path.exists(user_bgm):
                instrumental_path = user_bgm
                self._log(f"   🎵 Nhạc nền: {os.path.basename(user_bgm)}")
            # Ưu tiên 2: tách từ video (nếu bật env)
            elif bg_music_enabled:
                try:
                    from core.recap_engine import separate_vocals_ffmpeg
                    sep_dir = os.path.join(self.output_dir, "audio_sep")
                    _, instrumental_path = separate_vocals_ffmpeg(
                        video_path=self.cut_video_path,
                        output_dir=sep_dir,
                        ffmpeg_bin=ffmpeg_bin,
                        log=self._log,
                    )
                    if not instrumental_path or not os.path.exists(instrumental_path):
                        instrumental_path = ""
                except Exception as sep_e:
                    self._log(f"   ⚠️ Audio separator skip: {sep_e}")
                    instrumental_path = ""
            # ── END AUDIO ────────────────────────────────────────────────────

            # Mux: pad voice với silence đến đúng độ dài video
            # → video không bị cắt ngắn khi voice kết thúc sớm hơn
            # Tiếng gốc [0:a] bị bỏ hoàn toàn (tránh bản quyền)
            af = "apad"
            if video_duration > 0:
                af = f"apad=pad_dur={video_duration:.4f}"

            # ── TITLE OVERLAY ─────────────────────────────────────────────────
            # Nếu user nhập tiêu đề header/footer → burn vào video bằng drawtext
            h_text = getattr(self, "header_text", "") or ""
            f_text = getattr(self, "footer_text", "") or ""
            has_title = bool(h_text.strip() or f_text.strip())
            title_vf = ""
            if has_title:
                try:
                    from engine.video_engine import VideoEngine as _VE
                    title_vf = _VE._build_title_overlay_filter(
                        header=h_text,
                        footer=f_text,
                        header_color=getattr(self, "header_color", (255, 255, 0)),
                        footer_color=getattr(self, "footer_color", (255, 255, 255)),
                        header_bar_color=getattr(self, "header_bar_color", (255, 0, 0)),
                        footer_bar_color=getattr(self, "footer_bar_color", (0, 174, 255)),
                        header_font_size=getattr(self, "header_font_size", 80),
                        footer_font_size=getattr(self, "footer_font_size", 60),
                        header_pos=getattr(self, "header_pos", None),
                        footer_pos=getattr(self, "footer_pos", None),
                        frame_width=int(
                            (self.metadata.get("video", {}) or {}).get("width")
                            or 1920
                        ),
                    )
                    self._log(f"   🎨 Burn tiêu đề: '{h_text}' | '{f_text}'")
                except Exception as te:
                    self._log(f"   ⚠️ Bỏ qua title overlay: {te}")
                    title_vf = ""
            # ── END TITLE OVERLAY ─────────────────────────────────────────────

            if instrumental_path and os.path.exists(instrumental_path):
                try:
                    bgm_vol = float(os.environ.get("AUTORECAP_BGM_VOLUME", "0.06") or "0.06")
                except (TypeError, ValueError):
                    bgm_vol = 0.06
                bgm_vol = max(0.01, min(0.20, bgm_vol))
                audio_fc = (
                    f"[1:a]{af},atrim=0:{video_duration:.4f},volume=1.25[voice_pad];"
                    f"[2:a]aloop=loop=-1:size=2e+09,volume={bgm_vol:.3f},"
                    f"atrim=0:{video_duration:.4f},asetpts=PTS-STARTPTS[bg];"
                    "[bg][voice_pad]sidechaincompress="
                    "threshold=0.025:ratio=10:attack=20:release=500[bg_duck];"
                    "[voice_pad][bg_duck]amix=inputs=2:duration=first:normalize=0,"
                    "alimiter=limit=0.95[a_mix]"
                )
                if title_vf:
                    # Có cả nhạc nền + tiêu đề → cần re-encode video
                    filter_complex = f"[0:v]{title_vf}[vout];{audio_fc}"
                    cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", self.cut_video_path,
                        "-i", self.concat_audio_path,
                        "-i", instrumental_path,
                        "-filter_complex", filter_complex,
                        "-map", "[vout]", "-map", "[a_mix]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k",
                    ]
                else:
                    filter_complex = audio_fc
                    cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", self.cut_video_path,
                        "-i", self.concat_audio_path,
                        "-i", instrumental_path,
                        "-filter_complex", filter_complex,
                        "-map", "0:v:0", "-map", "[a_mix]",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    ]
                self._log(f"   🎵 Mix voice + nhạc nền ({int(bgm_vol*100)}%)")
            else:
                if title_vf:
                    # Có tiêu đề nhưng không có nhạc nền
                    filter_complex = (
                        f"[0:v]{title_vf}[vout];"
                        f"[1:a]{af},atrim=0:{video_duration:.4f}[a_padded]"
                    )
                    cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", self.cut_video_path,
                        "-i", self.concat_audio_path,
                        "-filter_complex", filter_complex,
                        "-map", "[vout]", "-map", "[a_padded]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k",
                    ]
                else:
                    # Không có tiêu đề, không có nhạc nền — copy video stream nhanh
                    cmd = [
                        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", self.cut_video_path,
                        "-i", self.concat_audio_path,
                        "-filter_complex",
                        f"[1:a]{af},atrim=0:{video_duration:.4f}[a_padded]",
                        "-map", "0:v:0", "-map", "[a_padded]",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    ]

            if video_duration > 0:
                cmd += ["-t", f"{video_duration:.4f}"]
            cmd.append(final_path)

            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "ffmpeg render thất bại")

            apply_design(final_path, design)
            self.final_video_path = final_path
            try:
                self.ai_package["render_final_policy"] = render_final_policy
                with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                    json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            size_mb = round(os.path.getsize(final_path) / 1e6, 1)
            self._step_done("RENDER_FINAL", f"{os.path.basename(final_path)} ({size_mb} MB)")
            return True

        except Exception as e:
            self._step_fail("RENDER_FINAL", str(e))
            return False

    # ──────────────────────────────────────────────────────────────
    # Run full pipeline
    # ──────────────────────────────────────────────────────────────

    def run(
        self,
        skip_transcript: bool = False,
        skip_scene_detect: bool = False,
        skip_keyframes: bool = False,
    ) -> bool:
        """
        Chạy toàn bộ 10 bước.
        Returns True nếu hoàn tất (kể cả các bước non-fatal bị skip).
        """
        self._reset_debug_log()
        self._log("\n" + "=" * 60)
        self._log("🎬 AutoRecapPro V2 - Full Pipeline bắt đầu")
        self._log("=" * 60 + "\n")

        try:
            return self._run_steps(
                skip_transcript=skip_transcript,
                skip_scene_detect=skip_scene_detect,
                skip_keyframes=skip_keyframes,
            )
        finally:
            # Luôn đóng Gemini Web driver dù pipeline pass hay fail
            try:
                from engine.ai_engine import AIEngine
                AIEngine.close_web_driver()
                self._log("   🌐 Đã đóng Gemini Web browser")
            except Exception:
                pass

    def _run_steps(
        self,
        skip_transcript: bool = False,
        skip_scene_detect: bool = False,
        skip_keyframes: bool = False,
    ) -> bool:
        """Thực thi các bước pipeline (được bọc bởi run() để đảm bảo cleanup)."""

        # 1. METADATA
        if not self.step_metadata():
            return False

        # 2. TRANSCRIPT
        if skip_transcript:
            self._step_skip("TRANSCRIPT", "Đã bỏ qua theo yêu cầu")
        else:
            # SRT nguồn là evidence bắt buộc để viết recap bám đúng lời thoại
            # và toàn bộ timeline phim. Nếu CapCut không tạo được SRT thì dừng
            # ngay, tránh chạy tiếp tới AI_FULL rồi mới lỗi hoặc đoán nội dung.
            if not self.step_transcript():
                return False

        # 2b. AUTO_TRANSLATE: nếu SRT là tiếng Trung → tự dịch sang tiếng Việt
        self.step_auto_translate()

        # Resume chỉ hợp lệ khi mọi output phía sau được tạo từ đúng video/SRT.
        self._log('   🔎 Kiểm tra dữ liệu video/SRT trước khi phân cảnh...')
        self._validate_pipeline_inputs()

        # 3. SCENE_DETECT
        if skip_scene_detect:
            self._step_skip("SCENE_DETECT", "Đã bỏ qua theo yêu cầu")
        else:
            self.step_scene_detect()  # non-fatal

        # 4. SUBTITLE_MAP
        self.step_subtitle_map()  # non-fatal

        # 5. KEYFRAMES
        if skip_keyframes:
            self._step_skip("KEYFRAMES", "Đã bỏ qua theo yêu cầu")
        else:
            self.step_keyframes()  # non-fatal

        # Backend-only artifacts for better recap planning. No GUI step is added.
        self._ensure_scene_cards()
        self._ensure_story_outline()

        # 6. AI_FULL: viết story chapter và narration toàn phim trước.
        # AI dùng scene/SRT/keyframe context để lập kế hoạch story, chưa cắt video thật.
        if not self._with_retry(self.step_ai_full, "AI_FULL"):
            return False

        # 7+8. CLIP_FIND và VOICE_SEGMENTS
        # Nếu có Script Review callback: chạy tuần tự (user cần duyệt kịch bản trước khi TTS).
        # Nếu auto mode (không có callback): chạy song song — VOICE_SEGMENTS chỉ cần
        # ai_package.script_blocks (đã có từ AI_FULL), không phụ thuộc output của CLIP_FIND.
        _has_script_review = bool(self.script_review_callback and callable(self.script_review_callback))

        if _has_script_review:
            # ── CHẾ ĐỘ CÓ SCRIPT REVIEW: tuần tự ────────────────────────────
            # 7. CLIP_FIND trước để có preview clip trong Script Editor.
            if not self._with_retry(self.step_clip_find, "CLIP_FIND"):
                return False
            if self.ai_package.get("script_blocks"):
                self.ai_package = self._attach_review_clips_to_package(self.ai_package, self.render_blocks)
                try:
                    with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                        json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            # ── SCRIPT REVIEW ─────────────────────────────────────────────────
            self._log("\n⏸️  Dừng để chỉnh sửa kịch bản — xác nhận xong mới tạo voice...\n")
            if self.step_callback:
                self._safe_step_callback("SCRIPT_REVIEW", "waiting")
            proceed = self.script_review_callback(self)
            if not proceed:
                error = getattr(self, 'script_review_error', '')
                if error:
                    self._log(f"❌ Không mở được Script Editor: {error}. Pipeline dừng trước khi tạo voice.")
                else:
                    self._log("⏸️ Pipeline dừng tại Script Review: chưa xác nhận tạo voice.")
                return False
            # Reload ai_package sau khi user chỉnh sửa
            pkg_file = os.path.join(self.output_dir, "ai_package.json")
            if os.path.exists(pkg_file):
                try:
                    import json as _json
                    with open(pkg_file, encoding="utf-8") as _f:
                        self.ai_package = _json.load(_f)
                    self.ai_package = self._attach_review_clips_to_package(self.ai_package, self.render_blocks)
                    with open(pkg_file, "w", encoding="utf-8") as _wf:
                        _json.dump(self.ai_package, _wf, indent=2, ensure_ascii=False)
                    self._log(f"   ♻️  Tải lại kịch bản sau chỉnh sửa: {len(self.ai_package.get('script_blocks', []))} blocks")
                except Exception as _e:
                    self._log(f"   ⚠️  Không reload được ai_package: {_e}")
            if self.step_callback:
                self._safe_step_callback("SCRIPT_REVIEW", "done")
            # 8. VOICE_SEGMENTS sau Script Review
            if not self._with_retry(self.step_voice_segments, "VOICE_SEGMENTS"):
                return False

        else:
            # ── CHẾ ĐỘ TỰ ĐỘNG: CLIP_FIND và VOICE_SEGMENTS chạy song song ──
            # VOICE_SEGMENTS chỉ đọc ai_package.script_blocks (đã có từ AI_FULL).
            # CLIP_FIND viết cut_video; VOICE_SEGMENTS viết voice_segments — không xung đột.
            self._log("   🚀 Chế độ tự động: CLIP_FIND + VOICE_SEGMENTS chạy song song...")
            _results: Dict[str, bool] = {}
            _errors: Dict[str, str] = {}

            def _run_clip_find():
                try:
                    ok = self._with_retry(self.step_clip_find, "CLIP_FIND")
                    _results["clip_find"] = bool(ok)
                    if ok and self.ai_package.get("script_blocks"):
                        self.ai_package = self._attach_review_clips_to_package(
                            self.ai_package, self.render_blocks
                        )
                        try:
                            with open(os.path.join(self.output_dir, "ai_package.json"), "w", encoding="utf-8") as f:
                                json.dump(self.ai_package, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                except Exception as e:
                    _results["clip_find"] = False
                    _errors["clip_find"] = str(e)

            def _run_voice_segments():
                try:
                    ok = self._with_retry(self.step_voice_segments, "VOICE_SEGMENTS")
                    _results["voice_segments"] = bool(ok)
                except Exception as e:
                    _results["voice_segments"] = False
                    _errors["voice_segments"] = str(e)

            t_clip = threading.Thread(target=_run_clip_find, name="CLIP_FIND", daemon=True)
            t_voice = threading.Thread(target=_run_voice_segments, name="VOICE_SEGMENTS", daemon=True)
            t_clip.start()
            t_voice.start()
            t_clip.join()
            t_voice.join()

            if not _results.get("clip_find"):
                err = _errors.get("clip_find", "")
                self._log(f"❌ CLIP_FIND thất bại khi chạy song song{': ' + err if err else ''}")
                return False
            if not _results.get("voice_segments"):
                err = _errors.get("voice_segments", "")
                self._log(f"❌ VOICE_SEGMENTS thất bại khi chạy song song{': ' + err if err else ''}")
                return False
            self._log("   ✅ CLIP_FIND + VOICE_SEGMENTS hoàn tất song song.")

        # 9. VOICE_CONCAT
        if not self._with_retry(self.step_voice_concat, "VOICE_CONCAT"):
            return False

        # 10. VOICE_SRT
        if not self._with_retry(self._repair_recorded_voice_alignment, "VOICE_SRT"):
            return False
        if not self._with_retry(self.step_voice_srt, "VOICE_SRT"):
            return False

        # 11. RENDER_FINAL
        if not self._with_retry(self.step_render_final, "RENDER_FINAL"):
            return False

        self._log("\n" + "=" * 60)
        self._log("🏁 Pipeline hoàn tất!")
        self._log("=" * 60)
        self._log(self.summary())

        return True

    # ──────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = ["\n📊 KẾT QUẢ PIPELINE:\n"]
        for name in STEP_NAMES:
            lines.append(f"  {self.steps[name]}")

        lines.append(f"\n📁 OUTPUT DIR: {self.output_dir}")
        if self.cut_video_path:
            lines.append(f"  ✂️  Video băm:   {os.path.basename(self.cut_video_path)}")
        if self.concat_audio_path:
            lines.append(f"  🎤 Voice track: {os.path.basename(self.concat_audio_path)}")
        if self.voice_srt_path:
            lines.append(f"  📝 Phụ đề:      {os.path.basename(self.voice_srt_path)}")
        if self.final_video_path and os.path.exists(self.final_video_path):
            size_mb = round(os.path.getsize(self.final_video_path) / 1e6, 1)
            lines.append(f"  🎬 Video cuối:  {os.path.basename(self.final_video_path)} ({size_mb} MB)")

        return "\n".join(lines)

    def get_outputs(self) -> Dict[str, str]:
        """Trả về dict các file output chính."""
        return {
            "metadata": os.path.join(self.output_dir, "metadata.json"),
            "transcript_srt": self.transcript_srt,
            "scenes": os.path.join(self.output_dir, "scenes.json"),
            "subtitle_map": os.path.join(self.output_dir, "subtitle_map.json"),
            "scene_cards": os.path.join(self.output_dir, "scene_cards.json"),
            "story_outline": os.path.join(self.output_dir, "story_outline.json"),
            "keyframes_dir": os.path.join(self.output_dir, "keyframes"),
            "ai_package": os.path.join(self.output_dir, "ai_package.json"),
            "render_blocks": os.path.join(self.output_dir, "render_blocks.json"),
            "srt_triangulation_report": os.path.join(self.output_dir, "srt_triangulation_report.json"),
            "srt_alignment_report": os.path.join(self.output_dir, "srt_alignment_report.json"),
            "cut_video": self.cut_video_path,
            "voice_segments_dir": os.path.join(self.output_dir, "voice_segments"),
            "voice_track": self.concat_audio_path,
            "voice_srt": self.voice_srt_path,
            "final_video": self.final_video_path,
        }






