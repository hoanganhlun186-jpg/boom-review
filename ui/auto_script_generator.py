"""
Auto Script & SRT Generator
Tự động tạo kịch bản và SRT mà không cần SRT gốc
"""

import asyncio
from typing import Tuple, Dict, Optional
from core.ai_engine import AIEngine
from core.srt_processor import SRTParser
import os


class AutoScriptGenerator:
    """Tự động tạo kịch bản + SRT từ tên phim và mô tả"""
    
    def __init__(self, api_keys, language: str = "Vietnamese"):
        """
        Khởi tạo generator
        
        Args:
            api_keys: Gemini API keys
            language: Ngôn ngữ (Vietnamese/English)
        """
        self.ai = AIEngine(api_keys)
        self.language = language
        self.srt_parser = SRTParser()
    
    async def generate_full_package(
        self,
        movie_name: str,
        movie_description: str,
        script_type: str = "Mô-đun (Intro+Body+Outro)",
        target_duration: int = 300,
        keep_seconds: int = 3,
        skip_seconds: int = 10,
        progress_callback=None
    ) -> Dict:
        """
        Tạo toàn bộ kịch bản + SRT tự động
        
        Args:
            movie_name: Tên phim
            movie_description: Mô tả nội dung phim
            script_type: Kiểu kịch bản (Mô-đun hoặc Liên tục)
            target_duration: Thời lượng mục tiêu (giây)
            keep_seconds: Giữ bao nhiêu giây khi cắt
            skip_seconds: Bỏ bao nhiêu giây khi cắt
            progress_callback: Callback để báo tiến độ
            
        Returns:
            Dict chứa:
            - script: Kịch bản voiceover
            - srt_text: SRT tiếng Việt
            - subtitle_chunks: Danh sách chunk phụ đề
            - duration: Thời lượng ước tính
            - word_count: Số từ trong kịch bản
        """
        
        try:
            # Step 1: Tính thời lượng mục tiêu
            if progress_callback:
                progress_callback("📊 Tính toán thông số kịch bản...", 10)
            
            target_seconds = target_duration or 300
            target_words = int(target_seconds * 2.2)
            target_words = max(450, min(target_words, 4500))
            
            # Step 2: Tạo script prompt
            if progress_callback:
                progress_callback("🧠 AI đang tạo kịch bản...", 20)
            
            script_content = await self._generate_script(
                movie_name=movie_name,
                description=movie_description,
                script_type=script_type,
                target_words=target_words,
                target_duration=target_seconds
            )
            
            if not script_content:
                raise ValueError("AI không thể tạo kịch bản")
            
            # Step 3: Tạo SRT từ kịch bản
            if progress_callback:
                progress_callback("📝 AI đang tạo phụ đề (SRT)...", 50)
            
            srt_text, subtitle_chunks = await self._generate_srt_from_script(
                script=script_content,
                target_duration=target_seconds
            )
            
            if not srt_text:
                raise ValueError("Không thể tạo SRT từ kịch bản")
            
            if progress_callback:
                progress_callback("✅ Hoàn thành tạo kịch bản + SRT", 100)
            
            return {
                "success": True,
                "script": script_content,
                "srt_text": srt_text,
                "subtitle_chunks": subtitle_chunks,
                "duration": target_seconds,
                "word_count": len(script_content.split()),
                "language": self.language
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "script": "",
                "srt_text": "",
                "subtitle_chunks": []
            }
    
    async def _generate_script(
        self,
        movie_name: str,
        description: str,
        script_type: str,
        target_words: int,
        target_duration: int
    ) -> str:
        """
        Tạo kịch bản từ tên phim + mô tả
        
        Args:
            movie_name: Tên phim
            description: Mô tả nội dung
            script_type: Mô-đun hoặc Liên tục
            target_words: Số từ mục tiêu
            target_duration: Thời lượng mục tiêu
            
        Returns:
            Kịch bản voiceover
        """
        
        is_modular = "Mô-đun" in script_type
        language = "Vietnamese"
        
        if is_modular:
            prompt = self._build_modular_script_prompt(
                movie_name, description, target_words, target_duration
            )
        else:
            prompt = self._build_continuous_script_prompt(
                movie_name, description, target_words, target_duration
            )
        
        # Gọi AI để tạo kịch bản
        try:
            result = await asyncio.to_thread(
                self.ai.generate_script,
                movie_name=movie_name,
                description=description,
                language=language,
                target_words=target_words,
                custom_prompt=prompt
            )
            
            if isinstance(result, tuple):
                success, script = result
                return script if success else ""
            return result or ""
            
        except Exception as e:
            print(f"❌ Lỗi tạo kịch bản: {str(e)}")
            return ""
    
    async def _generate_srt_from_script(
        self,
        script: str,
        target_duration: int
    ) -> Tuple[str, list]:
        """
        Tạo SRT từ kịch bản
        
        Args:
            script: Kịch bản voiceover
            target_duration: Thời lượng mục tiêu
            
        Returns:
            Tuple(srt_text, subtitle_chunks)
        """
        
        try:
            # Split script thành chunks
            chunks = self._split_script_to_chunks(script, target_duration)
            
            # Tạo SRT
            srt_lines = []
            for i, (start, end, text) in enumerate(chunks, 1):
                srt_lines.append(str(i))
                srt_lines.append(f"{self._format_time(start)} --> {self._format_time(end)}")
                srt_lines.append(text)
                srt_lines.append("")
            
            srt_text = "\n".join(srt_lines)
            return srt_text, chunks
            
        except Exception as e:
            print(f"❌ Lỗi tạo SRT: {str(e)}")
            return "", []
    
    def _build_modular_script_prompt(
        self,
        movie_name: str,
        description: str,
        target_words: int,
        target_duration: int
    ) -> str:
        """Tạo prompt cho kịch bản mô-đun"""
        
        return f"""
Hãy tạo kịch bản voiceover review phim '{movie_name}' bằng tiếng Việt.

NỘI DUNG PHIM:
{description}

YÊU CẦU:
- Cấu trúc: Intro (giới thiệu) → Body (nội dung chính) → Outro (kết luận)
- Tương ứng với ~{target_words} từ (~{target_duration} giây)
- Giọng điệu: Thú vị, hấp dẫn, dễ hiểu
- Phù hợp để đọc voiceover
- Có nhịp, có phân tích, không chỉ liệt kê
- Hợp để lên subtitle tiếng Việt
- Không dùng markdown, không tiêu đề phụ, không ký hiệu đặc biệt

Chỉ trả về kịch bản voiceover, không cần ghi chú hoặc giải thích:
"""
    
    def _build_continuous_script_prompt(
        self,
        movie_name: str,
        description: str,
        target_words: int,
        target_duration: int
    ) -> str:
        """Tạo prompt cho kịch bản liên tục"""
        
        return f"""
Hãy tạo kịch bản voiceover review phim '{movie_name}' bằng tiếng Việt theo cách liên tục.

NỘI DUNG PHIM:
{description}

YÊU CẦU:
- Cấu trúc: Liên tục từ đầu đến cuối (không chia mô-đun)
- Tương ứng với ~{target_words} từ (~{target_duration} giây)
- Giọng điệu: Tự nhiên, mượt mà, có sự phân tích sâu
- Phù hợp để đọc voiceover
- Có câu chuyện, có nhịp, không quá nhanh
- Hợp để lên subtitle tiếng Việt
- Không dùng markdown, không tiêu đề phụ, không ký hiệu đặc biệt

Chỉ trả về kịch bản voiceover, không cần ghi chú hoặc giải thích:
"""
    
    def _split_script_to_chunks(
        self,
        script: str,
        target_duration: int
    ) -> list:
        """
        Chia kịch bản thành chunk dựa vào thời lượng
        
        Args:
            script: Kịch bản đầy đủ
            target_duration: Thời lượng mục tiêu
            
        Returns:
            List[(start_ms, end_ms, text)]
        """
        
        # Chia thành câu
        sentences = self._split_sentences(script)
        
        if not sentences:
            return []
        
        # Tính thời gian cho mỗi câu
        words_per_second = 2.2  # Tốc độ đọc tiếng Việt
        total_words = len(script.split())
        
        chunks = []
        current_time = 0
        
        for sentence in sentences:
            words = len(sentence.split())
            duration = int(words / words_per_second)
            
            start_ms = current_time * 1000
            end_ms = (current_time + duration) * 1000
            
            chunks.append((start_ms, end_ms, sentence.strip()))
            current_time += duration
        
        return chunks
    
    def _split_sentences(self, text: str) -> list:
        """Chia text thành các câu"""
        
        # Split by . ! ? ; với xử lý unicode
        import re
        sentences = re.split(r'[.!?;]\s+', text.strip())
        return [s.strip() for s in sentences if s.strip()]
    
    @staticmethod
    def _format_time(milliseconds: int) -> str:
        """
        Format ms thành HH:MM:SS,mmm
        
        Args:
            milliseconds: Thời gian (ms)
            
        Returns:
            String định dạng SRT
        """
        
        total_seconds = milliseconds // 1000
        ms = milliseconds % 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"
    
    def save_script_to_file(self, script: str, output_dir: str) -> str:
        """
        Lưu kịch bản vào file
        
        Args:
            script: Nội dung kịch bản
            output_dir: Thư mục lưu
            
        Returns:
            Đường dẫn file
        """
        
        try:
            filename = os.path.join(output_dir, "auto_generated_script.txt")
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(script)
            return filename
        except Exception as e:
            print(f"❌ Lỗi lưu kịch bản: {str(e)}")
            return ""
    
    def save_srt_to_file(self, srt_text: str, output_dir: str) -> str:
        """
        Lưu SRT vào file
        
        Args:
            srt_text: Nội dung SRT
            output_dir: Thư mục lưu
            
        Returns:
            Đường dẫn file
        """
        
        try:
            filename = os.path.join(output_dir, "auto_generated_subtitle_vi.srt")
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(srt_text)
            return filename
        except Exception as e:
            print(f"❌ Lỗi lưu SRT: {str(e)}")
            return ""


# Thread-safe wrapper
class AsyncAutoScriptGenerator:
    """Wrapper cho async operation với thread safety"""
    
    def __init__(self, api_keys, language: str = "Vietnamese"):
        self.generator = AutoScriptGenerator(api_keys, language)
    
    def generate_full_package_sync(
        self,
        movie_name: str,
        movie_description: str,
        script_type: str = "Mô-đun (Intro+Body+Outro)",
        target_duration: int = 300,
        keep_seconds: int = 3,
        skip_seconds: int = 10,
        progress_callback=None
    ) -> Dict:
        """
        Synchronous wrapper cho async function
        
        Args: (same as async version)
        
        Returns:
            Dict with results
        """
        
        try:
            result = asyncio.run(
                self.generator.generate_full_package(
                    movie_name=movie_name,
                    movie_description=movie_description,
                    script_type=script_type,
                    target_duration=target_duration,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    progress_callback=progress_callback
                )
            )
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "script": "",
                "srt_text": "",
                "subtitle_chunks": []
            }
