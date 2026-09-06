"""Video processing engine using FFmpeg"""
import subprocess
import os
import tempfile
import json
from typing import Tuple, Optional, Dict, List
from utils.helpers import FFmpegUtils


def _color_to_ass(color_name: str) -> str:
    """Chuyển tên màu thông dụng sang hex BGR (định dạng ASS/SSA cho FFmpeg subtitles).
    Trả về 6 ký tự hex BGR (không có prefix), ví dụ: 'FFFFFF' cho white.
    """
    _map = {
        "white":  "FFFFFF",
        "yellow": "00FFFF",
        "cyan":   "FFFF00",
        "green":  "00FF00",
        "red":    "0000FF",
        "blue":   "FF0000",
        "orange": "0080FF",
        "pink":   "FF80FF",
        "black":  "000000",
    }
    return _map.get((color_name or "white").lower(), "FFFFFF")


class VideoEngine:
    """Handles video processing with FFmpeg, including rendering, cutting, and audio mixing"""
    
    # Default codec settings for faster processing
    DEFAULT_VIDEO_CODEC = 'libx264'
    DEFAULT_VIDEO_PRESET = 'ultrafast'  # Speed optimization
    DEFAULT_VIDEO_CRF = '20'  # Quality (lower = better, 0-51)
    DEFAULT_AUDIO_CODEC = 'aac'
    DEFAULT_AUDIO_BITRATE = '192k'

    @staticmethod
    def _ffmpeg_bin():
        return FFmpegUtils.ffmpeg_executable()

    @staticmethod
    def _audio_encoder_for_output(output_path):
        ext = os.path.splitext((output_path or "").lower())[1]
        if ext == ".mp3":
            return "libmp3lame", ["-b:a", VideoEngine.DEFAULT_AUDIO_BITRATE]
        if ext == ".wav":
            return "pcm_s16le", []
        return "aac", ["-b:a", VideoEngine.DEFAULT_AUDIO_BITRATE]

    @staticmethod
    def _probe_frame_width(video_path: str, default: int = 1920) -> int:
        """Read source width without opening a visible ffprobe window."""
        try:
            command = [
                FFmpegUtils.ffprobe_executable(), "-v", "error",
                "-select_streams", "v:0", "-show_entries", "stream=width",
                "-of", "json", video_path,
            ]
            result = subprocess.run(
                command,
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout or "{}")
                width = int((payload.get("streams") or [{}])[0].get("width") or 0)
                if width > 0:
                    return width
        except Exception:
            pass
        return int(default or 1920)
    
    @staticmethod
    def _escape_ffmpeg_text(text: str) -> str:
        """Escape special characters for FFmpeg text filter"""
        return (text or "").replace("'", "\\'").replace(":", "\\:")

    @staticmethod
    def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
        """Convert RGB tuple to FFmpeg hex color format"""
        return "0x%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)

    @staticmethod
    def _prepare_title_layout(
        text: str,
        requested_font_size: int,
        min_font_size: int = 40,
        safe_width: Optional[int] = None,
        max_lines: int = 2,
        frame_width: int = 1920,
        auto_fit: bool = True,
    ):
        """Lay out titles in pixels of the real source frame."""
        clean = " ".join(str(text or "").split())
        try:
            frame_width = max(320, int(frame_width or 1920))
        except Exception:
            frame_width = 1920
        width_scale = frame_width / 1920.0
        if safe_width is None:
            safe_width = max(240, int(round(frame_width * 0.9167)))
        else:
            safe_width = max(240, int(safe_width))
        scaled_min_size = max(18, int(round(min_font_size * width_scale)))
        literal_size = max(1, int(round((requested_font_size or 1) * width_scale)))
        scaled_requested_size = literal_size if not auto_fit else max(scaled_min_size, literal_size)
        start_size = scaled_requested_size
        if not clean:
            return [""], start_size

        # Literal mode is retained for callers that explicitly want to inspect
        # overflow. Normal preview/render uses auto_fit=True below.
        if not auto_fit:
            explicit_lines = [line.strip() for line in str(text or "").splitlines()]
            return explicit_lines or [clean], start_size

        def measure(value: str, size: int) -> int:
            try:
                from PIL import ImageFont
                font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
                left, _top, right, _bottom = font.getbbox(value)
                return max(0, right - left)
            except Exception:
                return int(len(value) * size * 0.56)

        def wrap(size: int):
            lines = []
            current = ""
            for word in clean.split():
                candidate = word if not current else f"{current} {word}"
                if not current or measure(candidate, size) <= safe_width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines

        step = max(1, int(round(2 * width_scale)))
        for size in range(start_size, scaled_min_size - 1, -step):
            lines = wrap(size)
            if len(lines) <= max_lines and all(measure(line, size) <= safe_width for line in lines):
                return lines, size
        emergency_min = max(14, int(round(28 * width_scale)))
        for size in range(scaled_min_size - step, emergency_min - 1, -step):
            lines = wrap(size)
            if len(lines) <= max_lines and all(measure(line, size) <= safe_width for line in lines):
                return lines, size
        return wrap(emergency_min), emergency_min

    @staticmethod
    def _build_readable_title_overlay_filter(
        header,
        footer,
        header_color,
        footer_color,
        header_bar_color,
        footer_bar_color,
        header_font_size,
        footer_font_size,
        header_pos,
        footer_pos,
        frame_width=1920,
    ):
        from core.preview_design import title_padding
        top_pad, bottom_pad = title_padding(frame_width, header.strip(), footer.strip())
        if not (top_pad or bottom_pad):
            return "null"
        def clamp_ratio(value, default):
            try:
                return max(0.02, min(0.98, float(value)))
            except Exception:
                return default

        hx = clamp_ratio((header_pos or {}).get("x"), 0.5) if isinstance(header_pos, dict) else 0.5
        fx = clamp_ratio((footer_pos or {}).get("x"), 0.5) if isinstance(footer_pos, dict) else 0.5
        header_lines, hfs = VideoEngine._prepare_title_layout(
            header, header_font_size, min_font_size=20, frame_width=frame_width,
            max_lines=2, auto_fit=True,
        )
        footer_lines, ffs = VideoEngine._prepare_title_layout(
            footer, footer_font_size, min_font_size=20, frame_width=frame_width,
            max_lines=2, auto_fit=True,
        )

        width_scale = max(0.5, float(frame_width or 1920) / 1920.0)
        pad = top_pad or bottom_pad
        center = pad // 2
        hgap = max(4, int(hfs * 0.12))
        fgap = max(4, int(ffs * 0.12))
        h_text_height = len(header_lines) * hfs + max(0, len(header_lines) - 1) * hgap
        f_text_height = len(footer_lines) * ffs + max(0, len(footer_lines) - 1) * fgap
        h_box_pad = max(12, int(hfs * 0.22))
        f_box_pad = max(12, int(ffs * 0.22))
        h_bar_height = min(pad, h_text_height + h_box_pad * 2)
        f_bar_height = min(pad, f_text_height + f_box_pad * 2)
        h_bar_y = 0
        f_bar_y = f"ih-{f_bar_height}"
        h_text_y = (h_bar_height - h_text_height) // 2
        f_text_y = f"h-{f_bar_height}+{(f_bar_height - f_text_height) // 2}"

        def rgba(color):
            r, g, b = color
            return f"0x{int(r):02x}{int(g):02x}{int(b):02x}ff"

        # Titles cover the upper/lower part of the source picture; never pad
        # the frame, so logo/subtitle/blur coordinates do not move on toggle.
        filters = []
        if top_pad:
            filters.append(f"drawbox=x=0:y={h_bar_y}:w=iw:h={h_bar_height}:color={rgba(header_bar_color)}:t=fill")
        if bottom_pad:
            filters.append(f"drawbox=x=0:y={f_bar_y}:w=iw:h={f_bar_height}:color={rgba(footer_bar_color)}:t=fill")
        header_hex = VideoEngine._rgb_to_hex(header_color)
        footer_hex = VideoEngine._rgb_to_hex(footer_color)
        for index, line in enumerate(header_lines if top_pad else []):
            y = h_text_y + index * (hfs + hgap)
            filters.append(
                "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
                f"text='{VideoEngine._escape_ffmpeg_text(line)}':fontcolor={header_hex}:"
                f"fontsize={hfs}:x=(w*{hx:.6f})-(tw/2):y={y}"
            )
        for index, line in enumerate(footer_lines if bottom_pad else []):
            y = f"({f_text_y})+{index * (ffs + fgap)}"
            filters.append(
                "drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
                f"text='{VideoEngine._escape_ffmpeg_text(line)}':fontcolor={footer_hex}:"
                f"fontsize={ffs}:x=(w*{fx:.6f})-(tw/2):y={y}"
            )
        return ",".join(filters)

    @staticmethod
    def _build_title_overlay_filter(
        header,
        footer,
        header_color=(255, 255, 0),
        footer_color=(255, 255, 255),
        header_bar_color=(255, 0, 0),
        footer_bar_color=(0, 174, 255),
        header_font_size=80,
        footer_font_size=60,
        header_pos=None,
        footer_pos=None,
        frame_width=1920,
    ):
        # Preview and final render share one wrapping policy. The UI size is
        # the preferred maximum; long titles wrap and shrink only as needed.
        return VideoEngine._build_readable_title_overlay_filter(
            header,
            footer,
            header_color,
            footer_color,
            header_bar_color,
            footer_bar_color,
            header_font_size,
            footer_font_size,
            header_pos,
            footer_pos,
            frame_width,
        )

        # Legacy single-line implementation retained below for compatibility
        # while old builds are phased out.
        def clamp_ratio(value, default):
            try:
                value = float(value)
                return max(0.02, min(0.98, value))
            except Exception:
                return default

        header_esc = VideoEngine._escape_ffmpeg_text(header)
        footer_esc = VideoEngine._escape_ffmpeg_text(footer)
        header_hex = VideoEngine._rgb_to_hex(header_color)
        footer_hex = VideoEngine._rgb_to_hex(footer_color)
        header_bar_hex = VideoEngine._rgb_to_hex(header_bar_color)
        footer_bar_hex = VideoEngine._rgb_to_hex(footer_bar_color)

        header_x_ratio = clamp_ratio((header_pos or {}).get("x"), 0.5) if isinstance(header_pos, dict) else 0.5
        header_y_ratio = clamp_ratio((header_pos or {}).get("y"), 0.12) if isinstance(header_pos, dict) else 0.12
        footer_x_ratio = clamp_ratio((footer_pos or {}).get("x"), 0.5) if isinstance(footer_pos, dict) else 0.5
        footer_y_ratio = clamp_ratio((footer_pos or {}).get("y"), 0.88) if isinstance(footer_pos, dict) else 0.88
        header_fs = int(header_font_size)
        footer_fs = int(footer_font_size)
        header_text_y = f"(h*{header_y_ratio:.6f}-{header_fs}/2)"
        footer_text_y = f"(h*{footer_y_ratio:.6f}-{footer_fs}/2)"
        header_box_pad = max(12, int(header_fs * 0.22))
        footer_box_pad = max(12, int(footer_fs * 0.22))

        # Padding 200px mỗi bên — tiêu đề nằm trong vùng đen, không đè lên video
        PAD = 200  # px đen trên và dưới video
        # Vị trí text: căn giữa trong vùng padding
        h_center = PAD // 2                    # 100
        f_center = PAD // 2                    # 100 từ dưới
        h_y = h_center - header_fs // 2       # số nguyên
        # drawtext uses w/h (main_w/main_h), not drawbox's iw/ih aliases.
        # Using "ih" here makes FFmpeg fail with "Undefined constant ih".
        f_y = f"h-{f_center + footer_fs // 2}"

        # Bar height = font + padding
        h_bar_h = header_fs + header_box_pad * 2
        f_bar_h = footer_fs + footer_box_pad * 2
        h_bar_y = h_center - h_bar_h // 2
        f_bar_y_expr = f"ih-{f_center + f_bar_h // 2}"

        # Màu box dưới dạng hex RGB cho drawbox
        def rgb_hex(color):
            r, g, b = color
            return f"0x{r:02x}{g:02x}{b:02x}ff"

        h_box_color = rgb_hex(header_bar_color)
        f_box_color = rgb_hex(footer_bar_color)

        return (
            # 1. Pad thêm 200px đen trên/dưới
            f"pad=iw:ih+{PAD * 2}:0:{PAD}:black,"
            # 2. Vẽ bar full-width header (đỏ)
            f"drawbox=x=0:y={h_bar_y}:w=iw:h={h_bar_h}:color={h_box_color}:t=fill,"
            # 3. Vẽ bar full-width footer (xanh)
            f"drawbox=x=0:y={f_bar_y_expr}:w=iw:h={f_bar_h}:color={f_box_color}:t=fill,"
            # 4. Text header
            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='{header_esc}':fontcolor={header_hex}:fontsize={header_fs}:x=(w*{header_x_ratio:.6f})-(tw/2):y={h_y},"
            # 5. Text footer
            f"drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='{footer_esc}':fontcolor={footer_hex}:fontsize={footer_fs}:x=(w*{footer_x_ratio:.6f})-(tw/2):y={f_y}"
        )

    @staticmethod
    def process_video_with_script_sync(input_path, output_path, tts_path, bgm_path, header, footer, script_timeline=None, max_duration_seconds=None):
        """
        Process video with script-based timeline synchronization.
        
        Args:
            script_timeline: List of (start_time, end_time, marker) tuples from ScriptProcessor
                            If None, falls back to default cutting pattern
        """
        # For now, if script_timeline provided, use it to calculate keep/skip
        # Otherwise fall back to process_video_v2
        if script_timeline is None:
            # Fallback to standard processing
            return VideoEngine.process_video_v2(input_path, output_path, tts_path, bgm_path, header, footer, 3, 10, max_duration_seconds)
        
        # TODO: Implement script-based dynamic cutting
        # For initial version, we'll use a simplified approach:
        # Extract only KEEP segments and concatenate them
        
        return VideoEngine._process_with_keep_segments(
            input_path, output_path, tts_path, bgm_path, header, footer,
            script_timeline, max_duration_seconds
        )
    
    @staticmethod
    def _process_with_keep_segments(
        input_path, output_path, tts_path, bgm_path, header, footer, script_timeline, max_duration_seconds,
        header_bar_color=(255, 0, 0), footer_bar_color=(0, 174, 255)
    ):
        """Process video by extracting only KEEP segments from timeline"""
        ffmpeg_bin = VideoEngine._ffmpeg_bin()
        
        # Build trim+concat filter for KEEP segments
        keep_segments = [(s, e) for s, e, m in script_timeline if m == 'KEEP']
        
        if not keep_segments:
            # No KEEP segments found, return error
            return False, "No KEEP segments found in script timeline"
        
        segment_files = []
        try:
            # Create trim commands for each KEEP segment
            for idx, (start, end) in enumerate(keep_segments):
                segment_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False).name
                segment_files.append(segment_file)
                
                # Use exact trim with re-encode for better compatibility
                trim_cmd = [
                    ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                    '-i', input_path,
                    '-ss', str(start), '-to', str(end),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
                    '-c:a', 'copy',
                    segment_file
                ]
                
                result = subprocess.run(
                    trim_cmd,
                    **FFmpegUtils.subprocess_kwargs(
                        check=False, capture_output=True, text=True
                    ),
                )
                if result.returncode != 0:
                    return False, f"Failed to trim segment {idx}: {result.stderr}"
            
            # Write concat file
            concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            concat_segments = [f"file '{f}'" for f in segment_files]
            concat_file.write('\n'.join(concat_segments))
            concat_file.close()
            
            # Concatenate segments
            concat_output = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False).name
            concat_cmd = [
                ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'concat', '-safe', '0',
                '-i', concat_file.name,
                '-c', 'copy',
                concat_output
            ]
            
            result = subprocess.run(
                concat_cmd,
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            if result.returncode != 0:
                return False, f"Failed to concatenate segments: {result.stderr}"
            
            # Now process the concatenated video with text and audio
            v_filter = VideoEngine._build_title_overlay_filter(
                header,
                footer,
                header_color=(255, 255, 0),
                footer_color=(255, 255, 255),
                header_bar_color=header_bar_color,
                footer_bar_color=footer_bar_color,
                frame_width=VideoEngine._probe_frame_width(concat_output),
            )
            # Anti-copyright
            anti_fp = "hflip,crop=iw*0.995:ih*0.995:iw*0.0025:ih*0.0025,scale=iw:ih,eq=saturation=1.03:contrast=1.02"
            v_filter = anti_fp + "," + v_filter
            
            # Base command
            command = [
                ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                '-i', concat_output,
                '-i', tts_path,
            ]
            
            # Add BGM if provided
            has_bgm = bgm_path and bgm_path.strip()
            if has_bgm:
                command.extend(['-stream_loop', '-1', '-i', bgm_path])
            
            # Filter complex
            if has_bgm:
                if max_duration_seconds and max_duration_seconds > 0:
                    voice_filter = f"[1:a]apad,atrim=0:{max_duration_seconds}[voice]"
                else:
                    voice_filter = "[1:a]anull[voice]"
                filter_complex = (
                    f"[0:v]{v_filter}[v_out];"
                    "[2:a]volume=0.06[bgm];"
                    f"{voice_filter};"
                    "[voice]volume=1.25[voice_boost];"
                    "[bgm][voice_boost]sidechaincompress="
                    "threshold=0.025:ratio=10:attack=20:release=500[bgm_duck];"
                    "[voice_boost][bgm_duck]amix=inputs=2:duration=first:normalize=0,"
                    "alimiter=limit=0.95[a_out]"
                )
            else:
                if max_duration_seconds and max_duration_seconds > 0:
                    audio_filter = f"[1:a]apad,atrim=0:{max_duration_seconds}[a_out]"
                else:
                    audio_filter = "[1:a]anull[a_out]"
                filter_complex = (
                    f"[0:v]{v_filter}[v_out];"
                    f"{audio_filter}"
                )
            
            command.extend([
                '-filter_complex', filter_complex,
                '-map', '[v_out]', '-map', '[a_out]',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
                '-c:a', 'aac', '-b:a', '192k',
            ])
            if max_duration_seconds and max_duration_seconds > 0:
                command.extend(['-t', str(max_duration_seconds)])
            else:
                command.append('-shortest')
            command.append(output_path)
            
            result = subprocess.run(
                command,
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            
            # Cleanup temp files
            try:
                os.unlink(concat_file.name)
                os.unlink(concat_output)
                for f in segment_files:
                    if os.path.exists(f):
                        os.unlink(f)
            except Exception:
                pass
            
            if result.returncode == 0:
                return True, result.stderr or result.stdout or "Success"
            else:
                return False, result.stderr or result.stdout or "Unknown error"
                
        except Exception as e:
            # Cleanup on error
            try:
                for f in segment_files:
                    if os.path.exists(f):
                        os.unlink(f)
            except Exception:
                pass
            return False, str(e)

    @staticmethod
    def assemble_block_audio(
        block_audio_paths,
        block_start_times,
        output_path,
        block_durations=None,
        total_duration=None,
    ):
        """Compose block audio into one smooth timeline.

        Each block is processed with:
        - Fade-out tail (40 ms) to avoid hard clips
        - Fade-in head (20 ms) to avoid pop/click at start
        - Natural end: does NOT force-cut mid-word — voice finishes sentence
          naturally up to a grace window beyond target duration
        - Loudness normalization on the final mix
        """
        if not block_audio_paths or len(block_audio_paths) != len(block_start_times):
            raise ValueError("block_audio_paths and block_start_times must be non-empty lists of equal length")
        if block_durations is not None and len(block_durations) != len(block_audio_paths):
            raise ValueError("block_durations must match block_audio_paths when provided")

        # Single-block fast path
        if len(block_audio_paths) == 1 and block_start_times[0] == 0 and not block_durations and not total_duration:
            try:
                ffmpeg_bin = VideoEngine._ffmpeg_bin()
                if os.path.exists(output_path):
                    os.unlink(output_path)
                subprocess.run(
                    [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                     "-i", block_audio_paths[0], "-c", "copy", output_path],
                    **FFmpegUtils.subprocess_kwargs(
                        check=True, capture_output=True, text=True
                    ),
                )
                return True
            except Exception as e:
                raise RuntimeError(f"Unable to copy block audio: {e}")

        durations = list(block_durations or [])
        if not durations:
            sorted_starts = [float(start or 0.0) for start in block_start_times]
            for idx, start_time in enumerate(sorted_starts):
                next_start = sorted_starts[idx + 1] if idx + 1 < len(sorted_starts) else None
                if next_start is not None and next_start > start_time:
                    durations.append(next_start - start_time)
                elif total_duration and float(total_duration) > start_time:
                    durations.append(float(total_duration) - start_time)
                else:
                    durations.append(0.0)

        if not total_duration:
            ends = []
            for start_time, duration in zip(block_start_times, durations):
                try:
                    ends.append(float(start_time or 0.0) + max(0.0, float(duration or 0.0)))
                except Exception:
                    continue
            total_duration = max(ends) if ends else None

        ffmpeg_bin = VideoEngine._ffmpeg_bin()
        audio_codec, audio_args = VideoEngine._audio_encoder_for_output(output_path)

        def fmt_s(value: float) -> str:
            value = max(0.0, float(value))
            text_value = f"{value:.4f}".rstrip("0").rstrip(".")
            return text_value or "0"

        command = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
        for audio_path in block_audio_paths:
            command.extend(["-i", audio_path])

        filter_parts = []
        labels = []
        for idx, (start_time, duration) in enumerate(zip(block_start_times, durations)):
            start_f = max(0.0, float(start_time or 0.0))
            duration_f = max(0.0, float(duration or 0.0))
            delay_ms = int(round(start_f * 1000))
            label = f"a{idx}"
            labels.append(f"[{label}]")
            if duration_f > 0:
                fade_out_start = max(0.0, duration_f - 0.04)
                chain = (
                    f"[{idx}:a]atrim=0:{fmt_s(duration_f)},asetpts=PTS-STARTPTS,"
                    f"apad=pad_dur={fmt_s(duration_f)},atrim=0:{fmt_s(duration_f)},"
                    f"afade=t=in:st=0:d=0.02,afade=t=out:st={fmt_s(fade_out_start)}:d=0.04,"
                    f"adelay={delay_ms}:all=1[{label}]"
                )
            else:
                chain = (
                    f"[{idx}:a]asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.02,"
                    f"adelay={delay_ms}:all=1[{label}]"
                )
            filter_parts.append(chain)

        mix = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest"
        if total_duration and float(total_duration) > 0:
            mix += f",atrim=0:{fmt_s(float(total_duration))}"
        mix += ",loudnorm=I=-16:TP=-1.5:LRA=11,highpass=f=80[aout]"
        filter_complex = ";".join(filter_parts + [mix])

        command.extend([
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", audio_codec,
            *audio_args,
            output_path,
        ])
        result = subprocess.run(
            command,
            **FFmpegUtils.subprocess_kwargs(
                check=False, capture_output=True, text=True
            ),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "Failed to assemble block audio")

        return True
    
    @staticmethod
    def process_video_v2(
        input_path,
        output_path,
        tts_path,
        bgm_path,
        header,
        footer,
        keep=3,
        skip=10,
        max_duration_seconds=None,
        header_font_size=80,
        footer_font_size=60,
        header_color=(255, 255, 0),
        footer_color=(255, 255, 255),
        voice_intro_path=None,
        header_bar_color=(255, 0, 0),
        footer_bar_color=(0, 174, 255),
        header_pos=None,
        footer_pos=None,
        # ── Tính năng mở rộng ─────────────────────────────────────────────────
        logo_path=None,           # str: đường dẫn PNG logo/watermark
        logo_corner="top-right",  # backward compat (bị thay bởi ratio)
        logo_x_ratio=0.85,        # vị trí tự do 0.0-1.0
        logo_y_ratio=0.05,
        logo_size_pct=10,         # % chiều rộng video (1-50)
        delogo_region=None,       # backward compat: dict {x,y,w,h}
        delogo_boxes=None,        # list of dict {x,y,w,h} — nhiều vùng che
        burn_srt_path=None,       # str: đường dẫn SRT để burn subtitle
        burn_sub_fontsize=36,     # cỡ chữ sub Việt
        burn_sub_color="white",   # màu chữ sub (white/yellow/...)
        burn_sub_x_ratio=0.5,
        burn_sub_y_ratio=0.78,
        burn_sub_outline=2,
        burn_sub_font='Arial',
        burn_sub_background=False,
        burn_sub_background_color='#000000',
        burn_sub_background_opacity=70,
    ):
        ffmpeg_bin = VideoEngine._ffmpeg_bin()
        intro_audio_path = None
        if voice_intro_path and os.path.exists(voice_intro_path):
            intro_audio_path = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False).name
            intro_cmd = [
                ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
                '-i', voice_intro_path,
                '-i', tts_path,
                '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1[a]',
                '-map', '[a]',
                '-c:a', 'libmp3lame',
                '-b:a', '192k',
                intro_audio_path,
            ]
            result = subprocess.run(
                intro_cmd,
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            if result.returncode != 0:
                try:
                    if os.path.exists(intro_audio_path):
                        os.unlink(intro_audio_path)
                except Exception:
                    pass
                intro_audio_path = None
        cycle = keep + skip
        use_cut_pattern = keep > 0 and skip > 0
        
        base_video_filter = VideoEngine._build_title_overlay_filter(
            header,
            footer,
            header_color=header_color,
            footer_color=footer_color,
            header_bar_color=header_bar_color,
            footer_bar_color=footer_bar_color,
            header_font_size=header_font_size,
            footer_font_size=footer_font_size,
            header_pos=header_pos,
            footer_pos=footer_pos,
            frame_width=VideoEngine._probe_frame_width(input_path),
        )

        # ── Anti-copyright fingerprint filter ────────────────────────────────
        # Kết hợp nhẹ: flip ngang + micro crop + EQ nhẹ
        # Đủ để qua Content ID mà không ảnh hưởng chất lượng xem
        anti_fp = (
            "hflip,"                          # mirror ngang
            "crop=iw*0.995:ih*0.995:iw*0.0025:ih*0.0025,"  # crop 0.5% 4 cạnh
            "scale=iw:ih,"                    # scale lại đúng kích thước
            "eq=saturation=1.03:contrast=1.02"  # tăng nhẹ màu sắc
        )
        if not (header.strip() or footer.strip()):
            base_video_filter = "null"
        base_video_filter = anti_fp + "," + base_video_filter

        # Apply the preview design once, after the final timeline is assembled.
        from core.preview_design import apply_design
        design = dict(logo=logo_path, logo_x=logo_x_ratio, logo_y=logo_y_ratio,
                      logo_size=logo_size_pct,
                      blur_boxes=delogo_boxes or ([delogo_region] if delogo_region else []),
                      burn=bool(burn_srt_path), srt=burn_srt_path,
                      sub_size=burn_sub_fontsize, sub_color=burn_sub_color,
                      sub_outline=burn_sub_outline,
                      sub_font=burn_sub_font, sub_background=burn_sub_background,
                      sub_background_color=burn_sub_background_color,
                      sub_background_opacity=burn_sub_background_opacity,
                      sub_x=burn_sub_x_ratio, sub_y=burn_sub_y_ratio)

        # If skip is 0, render continuously. This is used for pasted voiceover
        # scripts so a 40+ minute source is not shortened by the recap cutter.
        if use_cut_pattern:
            v_filter = f"select='lt(mod(t,{cycle}),{keep})',setpts=N/FRAME_RATE/TB,{base_video_filter}"
        else:
            v_filter = base_video_filter

        # Base command with video and TTS
        final_tts_path = intro_audio_path or tts_path

        command = [
            ffmpeg_bin, '-y',
            '-i', input_path,
            '-i', final_tts_path,
        ]

        # Conditionally add BGM if provided and not empty
        has_bgm = bgm_path and bgm_path.strip()
        if has_bgm:
            command.extend(['-stream_loop', '-1', '-i', bgm_path])

        # Filter complex depends on whether BGM exists
        if has_bgm:
            if max_duration_seconds and max_duration_seconds > 0:
                voice_filter = f"[1:a]apad,atrim=0:{max_duration_seconds}[voice]"
            else:
                voice_filter = "[1:a]anull[voice]"
            audio_part = (
                "[2:a]volume=0.06[bgm];"
                f"{voice_filter};"
                "[voice]volume=1.25[voice_boost];"
                "[bgm][voice_boost]sidechaincompress="
                "threshold=0.025:ratio=10:attack=20:release=500[bgm_duck];"
                "[voice_boost][bgm_duck]amix=inputs=2:duration=first:normalize=0,"
                "alimiter=limit=0.95[a_out]"
            )
        else:
            if max_duration_seconds and max_duration_seconds > 0:
                audio_part = f"[1:a]apad,atrim=0:{max_duration_seconds}[a_out]"
            else:
                audio_part = "[1:a]anull[a_out]"

        filter_complex = f"[0:v]{v_filter}[v_out];{audio_part}"
        
        command.extend([
            '-filter_complex', filter_complex,
            '-map', '[v_out]', '-map', '[a_out]',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
            '-c:a', 'aac', '-b:a', '192k',
        ])
        
        # Add duration limit if specified
        if max_duration_seconds and max_duration_seconds > 0:
            command.extend(['-t', str(max_duration_seconds)])
        else:
            # Without an explicit duration cap, keep the shortest stream so render does not run longer than needed.
            command.append('-shortest')
        command.append(output_path)
        
        try:
            completed = subprocess.run(
                command,
                **FFmpegUtils.subprocess_kwargs(
                    check=True, capture_output=True, text=True
                ),
            )
            if intro_audio_path:
                try:
                    os.unlink(intro_audio_path)
                except Exception:
                    pass
            apply_design(output_path, design)
            return True, completed.stderr or completed.stdout or ""
        except subprocess.CalledProcessError as e:
            if intro_audio_path:
                try:
                    os.unlink(intro_audio_path)
                except Exception:
                    pass
            return False, e.stderr or e.stdout or str(e)
        except Exception as e:
            if intro_audio_path:
                try:
                    os.unlink(intro_audio_path)
                except Exception:
                    pass
            return False, str(e)
