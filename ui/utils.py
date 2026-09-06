"""Utilities for AutoRecapPro V2"""
import os
import re
import unicodedata
from typing import Optional, List


class TextUtils:
    """Utilities for text processing and normalization"""
    
    @staticmethod
    def normalize_for_search(text: str) -> str:
        """Normalize text for comparison/search"""
        if not text:
            return ""
        value = unicodedata.normalize("NFKD", str(text))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return value.lower()
    
    @staticmethod
    def remove_special_chars(text: str) -> str:
        """Remove special characters from text"""
        return re.sub(r'[^\w\s]', '', text)
    
    @staticmethod
    def clean_filename(filename: str) -> str:
        """Clean filename for file system compatibility"""
        # Remove invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # Replace spaces with underscores
        filename = filename.replace(' ', '_')
        # Remove consecutive underscores
        filename = re.sub(r'_+', '_', filename)
        return filename.strip('_')


class FileUtils:
    """Utilities for file operations"""
    
    @staticmethod
    def get_file_size(path: str) -> int:
        """Get file size in bytes, returns 0 if not found"""
        try:
            return os.path.getsize(path) if os.path.exists(path) else 0
        except Exception:
            return 0
    
    @staticmethod
    def get_file_size_mb(path: str) -> float:
        """Get file size in MB"""
        return FileUtils.get_file_size(path) / (1024 * 1024)
    
    @staticmethod
    def ensure_directory(path: str) -> bool:
        """Ensure directory exists, create if needed"""
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception:
            return False
    
    @staticmethod
    def get_unique_filename(base_path: str, extension: str = "") -> str:
        """Generate unique filename by appending number if needed"""
        if not os.path.exists(base_path):
            return base_path
        
        base, ext = os.path.splitext(base_path)
        if extension:
            ext = extension if extension.startswith('.') else f'.{extension}'
        
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        
        return f"{base}_{counter}{ext}"
    
    @staticmethod
    def safe_remove(path: str) -> bool:
        """Safely remove file with error handling"""
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except Exception as e:
            print(f"Failed to remove {path}: {e}")
            return False


class TimeUtils:
    """Utilities for time/duration handling"""
    
    @staticmethod
    def seconds_to_hms(seconds: float) -> str:
        """Convert seconds to HH:MM:SS format"""
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    
    @staticmethod
    def seconds_to_ms(seconds: float) -> str:
        """Convert seconds to MM:SS format"""
        seconds = int(seconds)
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"
    
    @staticmethod
    def parse_time_string(time_str: str) -> float:
        """Parse time string (HH:MM:SS or MM:SS) to seconds"""
        try:
            parts = time_str.strip().split(':')
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            else:
                return float(time_str)
        except ValueError:
            return 0.0


class ValidationUtils:
    """Utilities for validation"""
    
    @staticmethod
    def is_valid_video(path: str) -> bool:
        """Check if file exists and has valid video extension"""
        valid_exts = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.flv', '.wmv'}
        return (
            os.path.exists(path) and 
            os.path.isfile(path) and
            os.path.splitext(path)[1].lower() in valid_exts
        )
    
    @staticmethod
    def is_valid_srt(path: str) -> bool:
        """Check if file exists and has .srt extension"""
        return (
            os.path.exists(path) and 
            os.path.isfile(path) and
            path.lower().endswith('.srt')
        )
    
    @staticmethod
    def is_valid_audio(path: str) -> bool:
        """Check if file exists and has valid audio extension"""
        valid_exts = {'.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg'}
        return (
            os.path.exists(path) and 
            os.path.isfile(path) and
            os.path.splitext(path)[1].lower() in valid_exts
        )
    
    @staticmethod
    def validate_api_key(key: str) -> bool:
        """Validate API key format"""
        if not key or not isinstance(key, str):
            return False
        key = key.strip()
        # Gemini API keys usually start with "AI" and are fairly long
        return len(key) > 20
