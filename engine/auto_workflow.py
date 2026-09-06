"""
Auto Workflow Handler
- When SRT is generated → automatically trigger Gemini translation + script generation
- Chains SRT generation → AI translation → Script generation seamlessly
"""

import os
import threading
from typing import Optional, Callable, Tuple, List
from engine.srt_processor import SRTParser
from engine.video_calculator import VideoCalculator
from engine.video_cutter import VideoCutter
from engine.ai_engine import AIEngine
from engine.premium_pipeline import PremiumReviewPipeline


class AutoWorkflowHandler:
    """Automatically chain SRT generation → Translation → Script generation"""
    
    def __init__(self, progress_callback: Optional[Callable] = None):
        """
        Initialize auto workflow handler
        
        Args:
            progress_callback: Function(message) to report progress
        """
        self.progress_callback = progress_callback
    
    def log(self, message: str):
        """Log message with callback"""
        print(f"[AutoWorkflow] {message}")
        if self.progress_callback:
            try:
                self.progress_callback(message)
            except:
                pass
    
    def read_srt_file(self, srt_path: str) -> Tuple[bool, str, str]:
        """
        Read and parse SRT file
        
        Returns:
            (success, srt_content, error_message)
        """
        try:
            if not os.path.exists(srt_path):
                return False, "", f"SRT file not found: {srt_path}"
            
            with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            return True, content, ""
        except Exception as e:
            return False, "", str(e)
    
    def extract_srt_text(self, srt_content: str) -> str:
        """Extract only the text from SRT (no timecodes)"""
        try:
            # Parse SRT content manually
            texts = []
            blocks = srt_content.strip().split('\n\n')
            for block in blocks:
                lines = block.strip().split('\n')
                if len(lines) >= 3:
                    # Skip index and timecode lines
                    text = '\n'.join(lines[2:]).strip()
                    if text:
                        texts.append(text)
            return " ".join(texts)
        except:
            return srt_content
    
    def translate_and_generate_script(
        self,
        srt_path: str,
        gemini_keys: List[str],
        movie_name: str,
        movie_description: str,
        tts_language: str = "Vietnamese",
        target_words: int = 900,
        keep_seconds: int = 3,
        skip_seconds: int = 10,
        source_duration_seconds: Optional[float] = None,
        on_progress: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, str, str, str]:
        """
        Translate SRT content and generate script using Gemini
        
        Args:
            srt_path: Path to generated SRT file
            gemini_keys: List of Gemini API keys
            movie_name: Movie name
            movie_description: Movie description
            tts_language: Language for TTS ("Vietnamese" or "English")
            target_words: Target word count for script
            on_progress: Progress callback
        
        Returns:
            (success, script_text, srt_translated, error_message)
        """
        def progress(msg):
            self.log(msg)
            if on_progress:
                on_progress(msg)
        
        try:
            # Read SRT file
            success, srt_content, error = self.read_srt_file(srt_path)
            if not success:
                return False, "", "", error
            
            progress(f"✅ Đã đọc file phụ đề: {srt_path}")
            progress(f"📄 Nội dung phụ đề: {len(srt_content)} ký tự")

            # Extract text from SRT
            srt_text = self.extract_srt_text(srt_content)
            progress(f"📝 Đã tách văn bản: {len(srt_text)} ký tự")

            # Initialize AI engine
            progress("🤖 Đang khởi tạo Gemini AI...")
            ai = AIEngine(gemini_keys)

            # Generate review package (translation + script)
            progress("🧠 Gemini đang viết kịch bản thuyết minh, vui lòng chờ...")

            keep_segments = VideoCutter.get_keep_segments(source_duration_seconds or 0, keep_seconds, skip_seconds)
            raw_render_blocks = VideoCalculator.get_render_blocks(keep_segments)
            book_map = PremiumReviewPipeline.build_book_map(raw_render_blocks, srt_content)
            beat_plan = PremiumReviewPipeline.build_beat_plan(book_map)
            book_context = PremiumReviewPipeline.build_book_context(book_map, beat_plan)
            
            package = ai.generate_review_package(
                movie_name=movie_name,
                movie_description=movie_description,
                subtitle_context=srt_text,
                timed_subtitles=srt_content,
                language=tts_language,
                target_words=target_words,
                character_focus=movie_name,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
                render_blocks=book_map,
                block_context=book_context,
            )
            
            if not package:
                return False, "", "", "Gemini không trả về kết quả — kiểm tra API key và thử lại"

            package["book_map"] = book_map
            package["beat_plan"] = beat_plan
            package["sync_report"] = PremiumReviewPipeline.validate_sync(
                package.get("script_blocks"),
                book_map,
                source_duration_seconds * (keep_seconds / max(1, keep_seconds + skip_seconds)) if source_duration_seconds else None,
            )
            
            # Extract script and SRT
            script_text = AIEngine._clean_review_script_text(package.get("script", "").strip())
            
            # Build SRT from subtitle chunks
            srt_translated = self._build_translated_srt(
                package.get("subtitle_chunks", []),
                srt_content
            )
            
            if not script_text:
                return False, "", "", "Gemini không tạo được kịch bản — thử lại hoặc kiểm tra prompt"
            
            if not srt_translated:
                # Fallback: split script into subtitle chunks
                srt_translated = self._build_srt_from_script(script_text)
            
            progress("✅ Đã tạo xong kịch bản và phụ đề thuyết minh!")
            return True, script_text, srt_translated, ""

        except Exception as e:
            error_msg = str(e)
            progress(f"❌ Lỗi: {error_msg}")
            return False, "", "", error_msg
    
    def _build_translated_srt(self, subtitle_chunks: List[dict], original_srt: str) -> str:
        """
        Build translated SRT from subtitle chunks
        
        Args:
            subtitle_chunks: List of subtitle data from Gemini
            original_srt: Original SRT content (for timing reference)
        
        Returns:
            SRT content
        """
        if not subtitle_chunks:
            return ""
        
        try:
            srt_lines = []
            for idx, chunk in enumerate(subtitle_chunks, 1):
                if isinstance(chunk, dict):
                    start_time = chunk.get('start_time', f"00:00:{idx*2:02d},000")
                    end_time = chunk.get('end_time', f"00:00:{idx*2+2:02d},000")
                    text = chunk.get('text', '')
                    
                    if text:
                        srt_lines.append(str(idx))
                        srt_lines.append(f"{start_time} --> {end_time}")
                        srt_lines.append(text)
                        srt_lines.append("")
                
                elif isinstance(chunk, str):
                    # Simple text chunk
                    srt_lines.append(str(idx))
                    srt_lines.append(f"00:00:{idx*2:02d},000 --> 00:00:{idx*2+2:02d},000")
                    srt_lines.append(chunk)
                    srt_lines.append("")
            
            return "\n".join(srt_lines)
        except:
            return ""
    
    def _build_srt_from_script(self, script_text: str) -> str:
        """
        Build SRT from script text (split into chunks)
        
        Args:
            script_text: Script content
        
        Returns:
            SRT content
        """
        try:
            # Split by sentences
            sentences = [s.strip() for s in script_text.split('.') if s.strip()]
            
            srt_lines = []
            for idx, sentence in enumerate(sentences, 1):
                start_sec = idx * 3  # Assume 3 seconds per subtitle
                end_sec = start_sec + 3
                
                start_time = self._seconds_to_srt_time(start_sec)
                end_time = self._seconds_to_srt_time(end_sec)
                
                srt_lines.append(str(idx))
                srt_lines.append(f"{start_time} --> {end_time}")
                srt_lines.append(sentence + ".")
                srt_lines.append("")
            
            return "\n".join(srt_lines)
        except:
            return ""
    
    @staticmethod
    def _seconds_to_srt_time(seconds: float) -> str:
        """Convert seconds to SRT time format HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def auto_translate_srt_and_generate_script(
        self,
        srt_path: str,
        gemini_keys: List[str],
        movie_name: str,
        movie_description: str,
        output_script_path: str,
        output_srt_path: str,
        tts_language: str = "Vietnamese",
        target_words: int = 900,
        keep_seconds: int = 3,
        skip_seconds: int = 10,
        source_duration_seconds: Optional[float] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[bool, str, str], None]] = None
    ):
        """
        Complete workflow in background thread:
        1. Read generated SRT
        2. Translate with Gemini
        3. Generate script
        4. Save both files
        
        Args:
            srt_path: Path to generated SRT
            gemini_keys: Gemini API keys
            movie_name: Movie name
            movie_description: Movie description
            output_script_path: Path to save script
            output_srt_path: Path to save translated SRT
            tts_language: Language for TTS
            target_words: Target word count
            on_progress: Progress callback
            on_complete: Completion callback (success, script_path, srt_path)
        """
        def worker():
            try:
                # Translate and generate
                success, script, srt_translated, error = self.translate_and_generate_script(
                    srt_path=srt_path,
                    gemini_keys=gemini_keys,
                    movie_name=movie_name,
                    movie_description=movie_description,
                    tts_language=tts_language,
                    target_words=target_words,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    source_duration_seconds=source_duration_seconds,
                    on_progress=on_progress
                )
                
                if not success:
                    if on_complete:
                        on_complete(False, "", error)
                    return
                
                # Save files
                try:
                    os.makedirs(os.path.dirname(output_script_path), exist_ok=True)
                    with open(output_script_path, 'w', encoding='utf-8') as f:
                        f.write(script)
                    self.log(f"✅ Đã lưu kịch bản: {output_script_path}")
                except Exception as e:
                    self.log(f"⚠️ Không lưu được kịch bản: {str(e)}")

                try:
                    os.makedirs(os.path.dirname(output_srt_path), exist_ok=True)
                    with open(output_srt_path, 'w', encoding='utf-8') as f:
                        f.write(srt_translated)
                    self.log(f"✅ Đã lưu phụ đề thuyết minh: {output_srt_path}")
                except Exception as e:
                    self.log(f"⚠️ Không lưu được phụ đề: {str(e)}")
                
                if on_complete:
                    on_complete(True, output_script_path, output_srt_path)
            
            except Exception as e:
                self.log(f"❌ Lỗi xử lý: {str(e)}")
                if on_complete:
                    on_complete(False, "", str(e))
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return thread
