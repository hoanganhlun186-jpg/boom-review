import os
import subprocess
import json
import re
from typing import Optional, Tuple
from engine.video_cutter import VideoCutter
from engine.srt_processor import SRTParser
from engine.script_generator import ScriptGenerator


class VideoProcessingWorkflow:
    """
    Quy trình xử lý video hoàn chỉnh:
    1. Tạo SRT từ video (tùy chọn)
    2. Cắt video theo mẫu (nếu cần)
    3. Tính toán thời lượng video thực tế
    4. Tạo script dựa trên thời lượng
    5. Tạo giọng nói theo script
    6. Render video với giọng nói + overlay text
    """
    
    def __init__(self, temp_dir: str = None, progress_callback = None):
        self.temp_dir = temp_dir or "."
        self.workflow_log = []
        self.progress_callback = progress_callback
        self.current_step = 0
        self.total_steps = 0
    
    def set_progress(self, message: str, percentage: int):
        """Cập nhật tiến trình"""
        self.log_step(f"[{percentage}%] {message}")
        if self.progress_callback:
            try:
                self.progress_callback(message, percentage)
            except:
                pass
    
    def log_step(self, message: str):
        """Ghi lại bước xử lý"""
        self.workflow_log.append(message)
        print(f"[WORKFLOW] {message}")
    
    def step_0_generate_srt(
        self,
        input_video: str,
        output_srt: str,
        use_whisper: bool = False,
        whisper_model: str = "base",
        source_language: str = None,
    ) -> Tuple[bool, int, str]:
        """
        Bước 0 (Tùy chọn): Tạo SRT từ video
        
        Returns:
            (success, subtitle_count, error_message)
        """
        self.set_progress("Nhận dạng SRT bằng CapCut...", 10)

        try:
            from engine.capcut_asr import CapCutDirectASR

            recognizer = CapCutDirectASR(
                progress_callback=lambda msg, pct: self.set_progress(
                    msg, 10 + int(pct * 0.4)
                )
            )
            subtitle_count = recognizer.transcribe(input_video, output_srt)
            self.log_step(f"✓ CapCut tạo xong: {subtitle_count} subtitle")
            return True, subtitle_count, ""
        except Exception as capcut_error:
            self.log_step(f"✗ CapCut ASR lỗi: {capcut_error}")

        # Whisper chỉ dành cho lời gọi cũ chủ động bật. Luồng mặc định dừng
        # để người dùng lấy SRT bằng CapCut Desktop, không chờ xử lý CPU lâu.
        if not use_whisper:
            return False, 0, (
                "CapCut chưa tạo được SRT. Hãy dùng MỞ CAPCUT TẠO SRT; "
                "Whisper đang tắt."
            )

        try:
            from engine.srt_generator import SRTGenerator

            self.set_progress("Fallback Whisper đã được bật thủ công...", 10)
            success, error = SRTGenerator.generate_srt_from_video(
                input_video,
                output_srt,
                use_whisper=True,
                whisper_model=whisper_model,
                source_language=source_language,
                progress_callback=lambda msg, pct: self.set_progress(
                    msg, 10 + (pct // 2)
                ),
            )
            if success:
                subtitles = SRTParser.parse_srt(output_srt)
                self.log_step(f"✓ SRT tạo xong: {len(subtitles)} subtitle")
                return True, len(subtitles), ""
            self.log_step(f"✗ Whisper lỗi: {error}")
            return False, 0, error
        except Exception as whisper_error:
            error_msg = str(whisper_error)
            self.log_step(f"✗ Whisper exception: {error_msg}")
            return False, 0, error_msg
    
    def step_1_cut_video(
        self,
        input_video: str,
        output_video: str,
        keep_seconds: int = 3,
        skip_seconds: int = 10,
        max_duration: Optional[float] = None,
        smart_cut: bool = False
    ) -> Tuple[bool, float, str]:
        """
        Bước 1: Cắt video theo mẫu keep/skip
        
        Returns:
            (success, actual_duration_seconds, error_message)
        """
        self.log_step(f"Bắt đầu cắt video: {os.path.basename(input_video)}")
        
        try:
            self.last_keep_segments = []
            if smart_cut:
                self.log_step("Dùng Smart Cut: phân cảnh + chuyển động + nhân vật chính")
                success, duration, error, segments = VideoCutter.cut_video_smart(
                    input_video,
                    output_video,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    max_duration_seconds=max_duration,
                    progress_callback=self.log_step,
                    return_segments=True,
                )
                self.last_keep_segments = segments
            else:
                success, duration, error = VideoCutter.cut_video_with_pattern(
                    input_video,
                    output_video,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    max_duration_seconds=max_duration
                )
                try:
                    source_duration = VideoCutter.get_video_duration(input_video)
                    segments = VideoCutter.get_keep_segments(source_duration, keep_seconds, skip_seconds)
                    self.last_keep_segments = VideoCutter._limit_segments_to_duration(segments, max_duration)
                except Exception:
                    self.last_keep_segments = []
            
            if success:
                self.log_step(f"✓ Video cắt xong, thời lượng: {duration:.1f}s ({duration/60:.1f} phút)")
                return True, duration, ""
            else:
                self.log_step(f"✗ Lỗi cắt video: {error}")
                return False, 0, error
        except Exception as e:
            error_msg = str(e)
            self.log_step(f"✗ Exception: {error_msg}")
            return False, 0, error_msg
    
    def step_2_prepare_script_config(
        self,
        movie_title: str,
        movie_description: str,
        video_duration: float,
        character_focus: Optional[str] = None
    ) -> dict:
        """
        Bước 2: Chuẩn bị cấu hình script dựa trên thời lượng video
        
        Returns:
            Script configuration dict
        """
        self.log_step(f"Chuẩn bị config script cho video {video_duration:.1f}s")
        
        config = ScriptGenerator.create_review_script_sections(
            movie_title=movie_title,
            movie_description=movie_description,
            video_duration=video_duration,
            character_focus=character_focus
        )
        
        self.log_step(f"✓ Config script: {config['total_estimated_words']} từ dự kiến")
        return config
    
    def step_3_validate_script_timing(
        self,
        intro_script: str,
        body_script: str,
        character_script: str,
        outro_script: str,
        script_config: dict,
        words_per_second: float = 2.5
    ) -> dict:
        """
        Bước 3: Kiểm tra xem script có phù hợp với thời lượng video không
        
        Returns:
            Timing report
        """
        self.log_step("Kiểm tra timing script")
        if body_script.strip() == "" and character_script.strip() == "" and outro_script.strip() == "" and ("[KEEP]" in intro_script or "[CUT]" in intro_script):
            sections = [part.strip() for part in re.split(r"\n\s*\n", intro_script) if part.strip()]
            if len(sections) >= 3:
                intro_text = re.sub(r"\[(KEEP|CUT)\]\s*", "", sections[0], flags=re.IGNORECASE).strip()
                body_text = re.sub(r"\[(KEEP|CUT)\]\s*", "", "\n\n".join(sections[1:-1]), flags=re.IGNORECASE).strip()
                outro_text = re.sub(r"\[(KEEP|CUT)\]\s*", "", sections[-1], flags=re.IGNORECASE).strip()

                intro_words = len(intro_text.split())
                body_words = len(body_text.split())
                outro_words = len(outro_text.split())
                character_words = 0

                intro_duration = intro_words / words_per_second if words_per_second > 0 else 0
                body_duration = body_words / words_per_second if words_per_second > 0 else 0
                character_duration = character_words / words_per_second if words_per_second > 0 else 0
                outro_duration = outro_words / words_per_second if words_per_second > 0 else 0

                timing = {
                    'intro': {
                        'words': intro_words,
                        'estimated_duration': intro_duration,
                        'target_duration': script_config['sections']['intro']['duration'],
                    },
                    'body': {
                        'words': body_words,
                        'estimated_duration': body_duration,
                        'target_duration': script_config['sections']['body_scenes']['duration'],
                    },
                    'character': {
                        'words': character_words,
                        'estimated_duration': character_duration,
                        'target_duration': script_config['sections']['character_analysis']['duration'],
                    },
                    'outro': {
                        'words': outro_words,
                        'estimated_duration': outro_duration,
                        'target_duration': script_config['sections']['outro']['duration'],
                    },
                    'total_duration': intro_duration + body_duration + character_duration + outro_duration,
                    'target_total_duration': script_config['total_duration'],
                }
            else:
                total_words = len(intro_script.split())
                estimated_duration = total_words / words_per_second if words_per_second > 0 else 0
                timing = {
                    'intro': {
                        'words': total_words,
                        'estimated_duration': estimated_duration,
                        'target_duration': script_config['total_duration'],
                    },
                    'body': {
                        'words': 0,
                        'estimated_duration': 0.0,
                        'target_duration': 0.0,
                    },
                    'character': {
                        'words': 0,
                        'estimated_duration': 0.0,
                        'target_duration': 0.0,
                    },
                    'outro': {
                        'words': 0,
                        'estimated_duration': 0.0,
                        'target_duration': 0.0,
                    },
                    'total_duration': estimated_duration,
                    'target_total_duration': script_config['total_duration'],
                }
        else:
            timing = ScriptGenerator.calculate_section_timing(
                script_config,
                intro_script,
                body_script,
                character_script,
                outro_script,
                words_per_second=words_per_second
            )
        
        target = timing['target_total_duration']
        actual = timing['total_duration']
        ratio = actual / target if target > 0 else 1.0
        
        self.log_step(f"  Intro: {timing['intro']['estimated_duration']:.1f}s (mục tiêu {timing['intro']['target_duration']:.1f}s)")
        self.log_step(f"  Body: {timing['body']['estimated_duration']:.1f}s (mục tiêu {timing['body']['target_duration']:.1f}s)")
        self.log_step(f"  Character: {timing['character']['estimated_duration']:.1f}s (mục tiêu {timing['character']['target_duration']:.1f}s)")
        self.log_step(f"  Outro: {timing['outro']['estimated_duration']:.1f}s (mục tiêu {timing['outro']['target_duration']:.1f}s)")
        self.log_step(f"  Tổng: {actual:.1f}s (mục tiêu {target:.1f}s, tỷ lệ {ratio:.2%})")
        
        if ratio < 0.85:
            self.log_step(f"⚠️ CẢNH BÁO: Script quá ngắn ({ratio:.0%}). Có thể cần thêm chi tiết hoặc khuyến nghị tăng giọng nói.")
        elif ratio > 1.15:
            self.log_step(f"⚠️ CẢNH BÁO: Script quá dài ({ratio:.0%}). Có thể cần giảm hoặc đẩy tốc độ giọng nói.")
        else:
            self.log_step(f"✓ Timing hợp lý")
        
        return timing
    
    def step_4_combine_scripts(
        self,
        intro: str,
        body: str,
        character: str,
        outro: str
    ) -> str:
        """
        Bước 4: Gộp các phần script lại thành một script duy nhất
        
        Returns:
            Complete script
        """
        self.log_step("Gộp các phần script")
        
        complete_script = f"""
{intro}

{body}

{character}

{outro}
""".strip()
        
        word_count = len(complete_script.split())
        self.log_step(f"✓ Script hoàn chỉnh: {word_count} từ")
        
        return complete_script
    
    def get_workflow_summary(self) -> str:
        """Lấy tóm tắt toàn bộ quy trình"""
        return "\n".join(self.workflow_log)
    
    @staticmethod
    def estimate_processing_time(
        video_duration: float,
        operation: str = "all"
    ) -> dict:
        """
        Ước tính thời gian xử lý
        
        Args:
            video_duration: Thời lượng video (giây)
            operation: 'cut', 'tts', 'render', 'all'
        
        Returns:
            Estimate dict
        """
        estimates = {
            'cut': video_duration / 60,  # ~1x speed
            'tts': (video_duration * 2.5) / 60 / 10,  # ~10x speed
            'render': video_duration / 30,  # ~30x speed
        }
        
        if operation == 'all':
            return {
                'total': sum(estimates.values()),
                'breakdown': estimates
            }
        else:
            return {'duration': estimates.get(operation, 0)}
