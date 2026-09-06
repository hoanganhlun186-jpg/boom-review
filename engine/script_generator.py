import re
from typing import List, Tuple, Optional


class ScriptGenerator:
    """
    Tạo kịch bản review phim dựa trên thời lượng video thực tế
    """
    
    @staticmethod
    def estimate_script_words_for_duration(
        duration_seconds: float,
        words_per_second: float = 2.5
    ) -> int:
        """
        Ước tính số từ cần thiết cho thời lượng video
        
        Args:
            duration_seconds: Thời lượng video (giây)
            words_per_second: Tốc độ nói (từ/giây). 2.5 là tốc độ bình thường
        
        Returns:
            Số từ nên có trong script
        """
        return int(duration_seconds * words_per_second)
    
    @staticmethod
    def create_review_script_sections(
        movie_title: str,
        movie_description: str,
        video_duration: float,
        character_focus: Optional[str] = None,
        plot_focus: Optional[str] = None,
    ) -> dict:
        """
        Tạo cấu trúc kịch bản review phim theo thời lượng video
        
        Args:
            movie_title: Tên phim
            movie_description: Tóm tắt nội dung phim
            video_duration: Thời lượng video (giây)
            character_focus: Nhân vật chính cần tập trung
            plot_focus: Cốt chuyện chính cần nhấn mạnh
        
        Returns:
            {
                'intro': {...},
                'body_scenes': [...],
                'character_deep_dive': {...},
                'plot_analysis': {...},
                'outro': {...},
                'total_estimated_words': int,
                'section_word_counts': {}
            }
        """
        
        # Allocate time: Intro (10%), Body (60%), Analysis (20%), Outro (10%)
        intro_duration = video_duration * 0.10
        body_duration = video_duration * 0.60
        character_duration = video_duration * 0.15
        outro_duration = video_duration * 0.15
        
        total_words = ScriptGenerator.estimate_script_words_for_duration(video_duration)
        
        intro_words = int(total_words * 0.10)
        body_words = int(total_words * 0.60)
        character_words = int(total_words * 0.15)
        outro_words = int(total_words * 0.15)
        
        return {
            'movie_title': movie_title,
            'movie_description': movie_description,
            'total_duration': video_duration,
            'total_estimated_words': total_words,
            'sections': {
                'intro': {
                    'duration': intro_duration,
                    'target_words': intro_words,
                    'purpose': 'Giới thiệu phim, kích thích hứng thú'
                },
                'body_scenes': {
                    'duration': body_duration,
                    'target_words': body_words,
                    'purpose': 'Tóm tắt nội dung chính, các scene quan trọng'
                },
                'character_analysis': {
                    'duration': character_duration,
                    'target_words': character_words,
                    'purpose': 'Phân tích nhân vật',
                    'focus': character_focus
                },
                'outro': {
                    'duration': outro_duration,
                    'target_words': outro_words,
                    'purpose': 'Kết luận, gợi ý xem phim'
                }
            },
            'metadata': {
                'character_focus': character_focus,
                'plot_focus': plot_focus,
                'generation_timestamp': None
            }
        }
    
    @staticmethod
    def calculate_section_timing(
        script_sections: dict,
        intro_script: str,
        body_script: str,
        character_script: str,
        outro_script: str,
        words_per_second: float = 2.5
    ) -> dict:
        """
        Tính toán thời gian cho từng phần script dựa trên số từ
        
        Returns:
            {
                'intro': {'words': int, 'estimated_duration': float},
                'body': {...},
                'character': {...},
                'outro': {...},
                'total_duration': float
            }
        """
        def count_words(text):
            return len(text.split())
        
        intro_word_count = count_words(intro_script)
        body_word_count = count_words(body_script)
        character_word_count = count_words(character_script)
        outro_word_count = count_words(outro_script)
        
        intro_duration = intro_word_count / words_per_second
        body_duration = body_word_count / words_per_second
        character_duration = character_word_count / words_per_second
        outro_duration = outro_word_count / words_per_second
        
        total_duration = intro_duration + body_duration + character_duration + outro_duration
        
        return {
            'intro': {
                'words': intro_word_count,
                'estimated_duration': intro_duration,
                'target_duration': script_sections['sections']['intro']['duration']
            },
            'body': {
                'words': body_word_count,
                'estimated_duration': body_duration,
                'target_duration': script_sections['sections']['body_scenes']['duration']
            },
            'character': {
                'words': character_word_count,
                'estimated_duration': character_duration,
                'target_duration': script_sections['sections']['character_analysis']['duration']
            },
            'outro': {
                'words': outro_word_count,
                'estimated_duration': outro_duration,
                'target_duration': script_sections['sections']['outro']['duration']
            },
            'total_duration': total_duration,
            'target_total_duration': script_sections['total_duration']
        }
    
    @staticmethod
    def adjust_script_for_timing(
        script: str,
        target_word_count: int,
        min_reduction: float = 0.8,
        max_expansion: float = 1.2
    ) -> str:
        """
        Điều chỉnh script để phù hợp với word count mục tiêu
        
        Args:
            script: Script gốc
            target_word_count: Số từ mục tiêu
            min_reduction: Tỷ lệ giảm tối thiểu (80%)
            max_expansion: Tỷ lệ tăng tối đa (120%)
        
        Returns:
            Script đã điều chỉnh
        """
        current_words = len(script.split())
        
        if current_words == 0:
            return script
        
        ratio = target_word_count / current_words
        
        # Nếu cần giảm quá nhiều, cắt ngắn
        if ratio < min_reduction:
            # Cắt từng câu/đoạn
            sentences = re.split(r'(?<=[.!?])\s+', script)
            words_per_sentence = current_words / len(sentences)
            target_sentences = int(target_word_count / words_per_sentence)
            adjusted = ' '.join(sentences[:max(1, target_sentences)])
            return adjusted
        
        # Nếu cần tăng quá nhiều, chỉ trả về gốc
        if ratio > max_expansion:
            return script
        
        # Nếu trong khoảng hợp lệ, trả về gốc
        return script
    
    @staticmethod
    def create_detailed_review_template(
        duration_seconds: float
    ) -> str:
        """
        Tạo template cho review phim chi tiết
        """
        intro_duration = duration_seconds * 0.10
        body_duration = duration_seconds * 0.60
        character_duration = duration_seconds * 0.15
        outro_duration = duration_seconds * 0.15
        
        template = f"""
SCRIPT TEMPLATE - REVIEW PHIM
==============================
Tổng thời lượng: {duration_seconds:.1f} giây

[INTRO: {intro_duration:.1f}s]
- Mở đầu thu hút (gợi cảm xúc, bí ẩn)
- Giới thiệu tên phim và nội dung chính
- Gợi ý tại sao nên xem

[BODY/PLOT SUMMARY: {body_duration:.1f}s]
- Tóm tắt cốt chuyện chính
- Nhấn mạnh các twist/bất ngờ
- Mô tả các scene quan trọng
- Liên hệ với nhân vật chính

[CHARACTER DEEP DIVE: {character_duration:.1f}s]
- Phân tích nhân vật chính
- Độ sâu của nhân vật
- Những hành động/quyết định quan trọng
- Cách nhân vật phát triển/thay đổi

[OUTRO: {outro_duration:.1f}s]
- Kết luận về chất lượng phim
- Đánh giá tổng thể
- Khuyến nghị xem
- Lời kêu gọi (like, subscribe, comment)
"""
        return template
