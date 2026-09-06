import subprocess
import os
import json
import tempfile
from typing import Optional, Tuple, List
from utils.helpers import FFmpegUtils
from engine.srt_processor import SRTParser


class SRTGenerator:
    """
    Tự động tạo SRT từ video bằng speech-to-text
    Hỗ trợ cả OpenAI Whisper và các công cụ khác
    """
    
    @staticmethod
    def extract_audio_from_video(video_path: str, output_audio: str = None) -> Tuple[bool, str]:
        """
        Trích xuất audio từ video
        
        Args:
            video_path: Đường dẫn video
            output_audio: Đường dẫn file audio output (nếu None, sẽ dùng temp file)
        
        Returns:
            (success: bool, audio_path: str)
        """
        try:
            if output_audio is None:
                output_audio = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name

            ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
            
            cmd = [
                ffmpeg_bin, '-hide_banner', '-loglevel', 'error',
                '-i', video_path,
                '-vn', '-acodec', 'pcm_s16le', '-ar', '16000',
                '-ac', '1',
                output_audio
            ]
            
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=False
                ),
            )
            
            if result.returncode == 0 and os.path.exists(output_audio):
                return True, output_audio
            else:
                return False, result.stderr or "Không thể trích xuất audio"
        
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def generate_srt_from_audio_with_whisper(
        audio_path: str,
        srt_output_path: str,
        model: str = "base",
        source_language: str = None
    ) -> Tuple[bool, str]:
        """
        Tạo SRT từ audio bằng OpenAI Whisper
        Requires: pip install openai-whisper
        
        Args:
            audio_path: Đường dẫn file audio
            srt_output_path: Đường dẫn file SRT output
            model: Kích thước model ('tiny', 'base', 'small', 'medium', 'large')
        
        Returns:
            (success: bool, error_message: str)
        """
        faster_error = ""
        try:
            from faster_whisper import WhisperModel

            output_dir = os.path.dirname(os.path.abspath(srt_output_path))
            os.makedirs(output_dir, exist_ok=True)
            whisper_model = WhisperModel(model, device="cpu", compute_type="int8")
            segments, _ = whisper_model.transcribe(
                audio_path,
                language=source_language or None,
                vad_filter=True,
            )

            written = 0
            with open(srt_output_path, "w", encoding="utf-8") as output_file:
                for segment in segments:
                    text = str(segment.text or "").strip()
                    if not text:
                        continue
                    written += 1
                    output_file.write(f"{written}\n")
                    output_file.write(
                        f"{SRTParser.seconds_to_timecode(segment.start)} --> "
                        f"{SRTParser.seconds_to_timecode(segment.end)}\n"
                    )
                    output_file.write(f"{text}\n\n")
            if written:
                return True, ""
            return False, "Whisper không nhận diện được lời thoại trong video"
        except ImportError:
            pass
        except Exception as e:
            faster_error = str(e)

        try:
            cmd = SRTGenerator._build_whisper_cli_command(
                audio_path,
                srt_output_path,
                model,
                source_language=source_language,
            )
            
            result = subprocess.run(
                cmd,
                **FFmpegUtils.subprocess_kwargs(
                    capture_output=True, text=True, check=False
                ),
            )
            
            if result.returncode == 0:
                whisper_output = os.path.join(
                    os.path.dirname(os.path.abspath(srt_output_path)),
                    os.path.splitext(os.path.basename(audio_path))[0] + ".srt",
                )
                if os.path.exists(whisper_output):
                    if os.path.abspath(whisper_output) != os.path.abspath(srt_output_path):
                        os.replace(whisper_output, srt_output_path)
                    return True, ""
                else:
                    return False, "Whisper tạo SRT nhưng không tìm thấy file"
            else:
                detail = result.stderr or "Lỗi Whisper"
                if faster_error:
                    detail = f"Lỗi faster-whisper: {faster_error}. Lỗi Whisper CLI: {detail}"
                return False, detail
        
        except FileNotFoundError:
            return False, "Whisper không được cài đặt. Chạy: pip install openai-whisper"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _build_whisper_cli_command(
        audio_path: str,
        srt_output_path: str,
        model: str = "base",
        source_language: str = None,
    ) -> List[str]:
        command = [
            "whisper",
            audio_path,
            "--model",
            model,
            "--output_format",
            "srt",
            "--output_dir",
            os.path.dirname(os.path.abspath(srt_output_path)),
        ]
        if source_language and source_language.lower() not in {"auto", "automatic"}:
            command.extend(["--language", source_language])
        return command
    
    @staticmethod
    def generate_srt_from_video(
        video_path: str,
        srt_output_path: str,
        use_whisper: bool = True,
        whisper_model: str = "base",
        source_language: str = None,
        progress_callback = None
    ) -> Tuple[bool, str]:
        """
        Hoàn toàn tự động: Video -> Audio -> SRT
        
        Args:
            video_path: Đường dẫn video
            srt_output_path: Đường dẫn file SRT output
            use_whisper: Sử dụng Whisper (True) hay công cụ khác
            whisper_model: Kích thước model
            progress_callback: Hàm callback để báo cáo tiến trình
        
        Returns:
            (success: bool, error_message: str)
        """
        try:
            # Step 1: Extract audio
            if progress_callback:
                progress_callback("Bước 1/3: Trích xuất audio từ video...", 33)
            
            audio_path = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
            success, audio_result = SRTGenerator.extract_audio_from_video(video_path, audio_path)
            
            if not success:
                return False, f"Lỗi trích xuất audio: {audio_result}"
            
            # Step 2: Generate SRT from audio
            if progress_callback:
                progress_callback("Bước 2/3: Chuyển âm thanh thành text (Whisper)...", 66)
            
            if use_whisper:
                success, error = SRTGenerator.generate_srt_from_audio_with_whisper(
                    audio_path,
                    srt_output_path,
                    model=whisper_model,
                    source_language=source_language,
                )
            else:
                success = False
                error = "Chỉ hỗ trợ Whisper hiện tại"
            
            if success:
                if progress_callback:
                    progress_callback("Bước 3/3: Hoàn thành!", 100)
                return True, ""
            else:
                return False, error
        
        except Exception as e:
            return False, str(e)
        finally:
            # Cleanup temp audio file
            try:
                if os.path.exists(audio_path):
                    os.unlink(audio_path)
            except:
                pass
    
    @staticmethod
    def merge_close_subtitles(
        srt_path: str,
        output_path: str,
        time_threshold: float = 0.5
    ) -> Tuple[bool, str]:
        """
        Gộp các subtitle gần nhau (trong 0.5s) để tránh fragment quá nhiều
        
        Args:
            srt_path: Đường dẫn file SRT gốc
            output_path: Đường dẫn file SRT output
            time_threshold: Ngưỡng thời gian (giây)
        
        Returns:
            (success: bool, error_message: str)
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            
            if not subtitles:
                return False, "Không thể parse SRT"
            
            merged = []
            current_group = [subtitles[0]]
            
            for i in range(1, len(subtitles)):
                current_sub = subtitles[i]
                prev_end = current_group[-1]['end_seconds']
                curr_start = current_sub['start_seconds']
                
                gap = curr_start - prev_end
                
                if gap <= time_threshold:
                    # Gộp vào group hiện tại
                    current_group.append(current_sub)
                else:
                    # Hoàn thành group và bắt đầu group mới
                    merged_text = ' '.join([sub['text'] for sub in current_group])
                    merged.append({
                        'start': current_group[0]['start'],
                        'end': current_group[-1]['end'],
                        'text': merged_text
                    })
                    current_group = [current_sub]
            
            # Add last group
            if current_group:
                merged_text = ' '.join([sub['text'] for sub in current_group])
                merged.append({
                    'start': current_group[0]['start'],
                    'end': current_group[-1]['end'],
                    'text': merged_text
                })
            
            # Write to output
            with open(output_path, 'w', encoding='utf-8') as f:
                for idx, sub in enumerate(merged, 1):
                    f.write(f"{idx}\n")
                    f.write(f"{sub['start']} --> {sub['end']}\n")
                    f.write(f"{sub['text']}\n\n")
            
            return True, ""
        
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def clean_srt_text(srt_path: str, output_path: str) -> Tuple[bool, str]:
        """
        Làm sạch text trong SRT:
        - Loại bỏ dấu ngoặc vuông [...]
        - Loại bỏ ký tự đặc biệt
        - Chuẩn hóa khoảng trắng
        
        Args:
            srt_path: Đường dẫn file SRT gốc
            output_path: Đường dẫn file SRT output
        
        Returns:
            (success: bool, error_message: str)
        """
        try:
            import re
            
            subtitles = SRTParser.parse_srt(srt_path)
            
            cleaned = []
            for sub in subtitles:
                text = sub['text']
                # Remove music notes and sound effects
                text = re.sub(r'\[.*?\]', '', text)
                text = re.sub(r'\(.*?\)', '', text)
                # Normalize whitespace
                text = ' '.join(text.split())
                
                cleaned.append({
                    'index': sub['index'],
                    'start': sub['start'],
                    'end': sub['end'],
                    'text': text.strip()
                })
            
            # Write to output
            with open(output_path, 'w', encoding='utf-8') as f:
                for sub in cleaned:
                    if sub['text']:  # Skip empty
                        f.write(f"{sub['index']}\n")
                        f.write(f"{sub['start']} --> {sub['end']}\n")
                        f.write(f"{sub['text']}\n\n")
            
            return True, ""
        
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def estimate_processing_time(video_duration_seconds: float, model: str = "base") -> dict:
        """
        Ước tính thời gian xử lý tạo SRT
        
        Args:
            video_duration_seconds: Thời lượng video (giây)
            model: Whisper model size
        
        Returns:
            {'extraction': float, 'transcription': float, 'total': float} (minutes)
        """
        # Extraction: ~1x speed
        extraction_time = video_duration_seconds / 60
        
        # Transcription depends on model
        model_speeds = {
            'tiny': 3,      # ~3x faster
            'base': 2,      # ~2x faster
            'small': 1,     # ~1x speed
            'medium': 0.5,  # ~0.5x speed
            'large': 0.25   # ~0.25x speed
        }
        
        multiplier = model_speeds.get(model, 1)
        transcription_time = (video_duration_seconds / 60) / multiplier
        
        return {
            'extraction': extraction_time,
            'transcription': transcription_time,
            'total': extraction_time + transcription_time
        }
