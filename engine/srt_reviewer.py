import json
from typing import Optional, Tuple, List
from engine.srt_processor import SRTParser


class SRTReviewer:
    """
    Review SRT từ Gemini AI:
    - Kiểm tra chính tả
    - Cải thiện dòng chảy text
    - Tạo kịch bản review phim từ SRT
    """
    
    def __init__(self, ai_engine):
        self.ai_engine = ai_engine
    
    def analyze_srt_chunks(
        self,
        srt_path: str,
        movie_title: str,
        chunk_duration: float = 10.0
    ) -> Tuple[bool, dict, str]:
        """
        Phân tích SRT thành chunks và tạo summary
        
        Args:
            srt_path: Đường dẫn file SRT
            movie_title: Tên phim
            chunk_duration: Thời lượng mỗi chunk (giây)
        
        Returns:
            (success, analysis_dict, error_message)
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            chunks = SRTParser.merge_subtitle_chunks(subtitles, chunk_duration)
            
            analysis = {
                'movie_title': movie_title,
                'total_subtitles': len(subtitles),
                'total_chunks': len(chunks),
                'total_duration': subtitles[-1]['end_seconds'] if subtitles else 0,
                'chunks': chunks,
                'key_moments': []
            }
            
            # Identify key moments (chunks with important keywords)
            keywords = ['twist', 'reveal', 'death', 'transform', 'end', 'epilogue', 'climax']
            for chunk in chunks:
                text_lower = chunk['text'].lower()
                if any(kw in text_lower for kw in keywords):
                    analysis['key_moments'].append({
                        'time': chunk['start_seconds'],
                        'text': chunk['text'][:100] + "..."
                    })
            
            return True, analysis, ""
        
        except Exception as e:
            return False, {}, str(e)
    
    def generate_script_from_srt(
        self,
        srt_path: str,
        movie_title: str,
        movie_description: str,
        target_word_count: int = 800,
        section_type: str = "full"
    ) -> Tuple[bool, str, str]:
        """
        Dùng Gemini để tạo kịch bản review từ SRT
        
        Args:
            srt_path: Đường dẫn file SRT
            movie_title: Tên phim
            movie_description: Mô tả phim
            target_word_count: Số từ mục tiêu
            section_type: 'full', 'intro', 'plot', 'character', 'outro'
        
        Returns:
            (success, script, error_message)
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            
            if not subtitles:
                return False, "", "Không có subtitle trong SRT"
            
            # Lấy sample text từ SRT
            sample_text = " ".join([sub['text'] for sub in subtitles[:10]])
            
            # Tạo prompt cho Gemini
            if section_type == "full":
                prompt = f"""
Bạn là một chuyên gia tạo kịch bản review phim chuyên nghiệp.
Dựa vào SRT subtitle từ phim "{movie_title}", hãy tạo kịch bản review phim hoàn chỉnh.

Mô tả phim: {movie_description}

SRT Subtitles (sample):
{sample_text}

Yêu cầu:
- Viết kịch bản khoảng {target_word_count} từ
- Cấu trúc: Intro (gợi cảm xúc) -> Plot summary -> Character analysis -> Outro (gợi ý xem)
- Dùng tiếng Việt tự nhiên, hấp dẫn
- Bám sát nội dung từ subtitle

Kịch bản:
"""
            elif section_type == "intro":
                prompt = f"""
Tạo phần Intro (~100 từ) cho video review phim "{movie_title}".
Yêu cầu: Gợi cảm xúc, hấp dẫn, làm khán giả muốn xem phim.

Intro:
"""
            elif section_type == "plot":
                prompt = f"""
Dựa vào subtitle: {sample_text}

Tạo phần Plot Summary (~300 từ) về bộ phim "{movie_title}".
Yêu cầu: Tóm tắt cốt chuyện, nhấn mạnh twist/bất ngờ.

Plot Summary:
"""
            elif section_type == "character":
                prompt = f"""
Dựa vào subtitle: {sample_text}

Tạo phần Character Analysis (~200 từ) về nhân vật chính trong "{movie_title}".
Yêu cầu: Phân tích nhân vật, hành động, sự phát triển.

Character Analysis:
"""
            elif section_type == "outro":
                prompt = f"""
Tạo phần Outro (~100 từ) cho video review phim "{movie_title}".
Yêu cầu: Kết luận, đánh giá, gợi ý xem, lời kêu gọi (like, subscribe).

Outro:
"""
            else:
                return False, "", f"Section type '{section_type}' không hợp lệ"
            
            # Gọi Gemini
            script = self.ai_engine.generate_script(
                movie_name=movie_title,
                description=movie_description,
                word_count=target_word_count,
                section_type=section_type,
                custom_prompt=prompt if section_type == "full" else None
            )
            
            return True, script, ""
        
        except Exception as e:
            return False, "", str(e)
    
    def review_and_enhance_srt(
        self,
        srt_path: str,
        output_path: str,
        movie_title: str
    ) -> Tuple[bool, str]:
        """
        Review SRT từ Gemini: Kiểm tra chính tả, cải thiện flow
        
        Args:
            srt_path: Đường dẫn file SRT gốc
            output_path: Đường dẫn file SRT được review
            movie_title: Tên phim
        
        Returns:
            (success, error_message)
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            
            if not subtitles:
                return False, "Không có subtitle"
            
            # Lấy toàn bộ text từ SRT
            full_text = "\n".join([sub['text'] for sub in subtitles])
            
            prompt = f"""
Review SRT subtitle từ phim "{movie_title}":

{full_text[:2000]}  (Đoạn đầu...)

Yêu cầu:
1. Kiểm tra chính tả & ngữ pháp
2. Cải thiện flow, dòng chảy tự nhiên
3. Xóa những phần không liên quan (âm thanh background, v.v.)
4. Trả lại subtitle đã được cải thiện (giữ định dạng SRT)

Trả lại kết quả dưới dạng:
[Subtitle đã review]
---
[Ghi chú cải thiện]
"""
            
            # Gọi Gemini API (phải implement trong ai_engine)
            # Đây là phần cần kết nối với generate_script hoặc API Gemini trực tiếp
            
            return True, ""
        
        except Exception as e:
            return False, str(e)
    
    def extract_key_scenes_from_srt(
        self,
        srt_path: str,
        movie_title: str,
        num_scenes: int = 5
    ) -> Tuple[bool, List[dict], str]:
        """
        Trích xuất các scene quan trọng từ SRT
        
        Args:
            srt_path: Đường dẫn file SRT
            movie_title: Tên phim
            num_scenes: Số scene cần trích
        
        Returns:
            (success, scenes_list, error_message)
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            
            # Tạo chunks
            chunks = SRTParser.merge_subtitle_chunks(subtitles, chunk_duration=15.0)
            
            # Score mỗi chunk dựa trên keyword
            important_keywords = {
                'twist': 10, 'reveal': 10, 'death': 9, 'transform': 8,
                'climax': 9, 'ending': 8, 'epilogue': 7, 'final': 7,
                'amazing': 6, 'incredible': 6, 'shocked': 7, 'surprised': 6
            }
            
            scored_chunks = []
            for chunk in chunks:
                score = 0
                text_lower = chunk['text'].lower()
                for keyword, value in important_keywords.items():
                    if keyword in text_lower:
                        score += value
                
                scored_chunks.append({
                    'text': chunk['text'],
                    'time': chunk['start_seconds'],
                    'duration': chunk['end_seconds'] - chunk['start_seconds'],
                    'score': score
                })
            
            # Lấy top N scenes
            scored_chunks.sort(key=lambda x: x['score'], reverse=True)
            top_scenes = scored_chunks[:num_scenes]
            
            # Sort by time
            top_scenes.sort(key=lambda x: x['time'])
            
            return True, top_scenes, ""
        
        except Exception as e:
            return False, [], str(e)
    
    @staticmethod
    def srt_to_script_outline(srt_path: str) -> str:
        """
        Chuyển SRT thành script outline (không dùng AI)
        
        Returns:
            Outline string
        """
        try:
            subtitles = SRTParser.parse_srt(srt_path)
            chunks = SRTParser.merge_subtitle_chunks(subtitles, chunk_duration=20.0)
            
            outline = "SCRIPT OUTLINE từ SRT\n"
            outline += "=" * 50 + "\n\n"
            
            for idx, chunk in enumerate(chunks, 1):
                time_min = int(chunk['start_seconds']) // 60
                time_sec = int(chunk['start_seconds']) % 60
                outline += f"[{time_min}:{time_sec:02d}] {chunk['text'][:80]}...\n"
            
            return outline
        except:
            return ""
