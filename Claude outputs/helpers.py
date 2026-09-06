"""Utilities for AutoRecapPro V2"""
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Optional


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
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        filename = filename.replace(' ', '_')
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
        return len(key) > 20


class FFmpegUtils:
    """Resolve FFmpeg binaries from PATH, env vars, or known local installs."""

    _cached_bin_dir: Optional[str] = None
    _bootstrapped: bool = False

    @staticmethod
    def _app_root() -> Path:
        """Return source root in dev, or exe folder in Nuitka build."""
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    @staticmethod
    def _safe_path(value) -> Optional[Path]:
        try:
            return Path(str(value).strip().strip('"')).expanduser()
        except Exception:
            return None

    @staticmethod
    def _safe_is_dir(path: Path) -> bool:
        try:
            return path.exists() and path.is_dir()
        except (OSError, PermissionError):
            return False

    @staticmethod
    def _safe_is_file(path: Path) -> bool:
        try:
            return path.exists() and path.is_file()
        except (OSError, PermissionError):
            return False

    @staticmethod
    def _safe_iterdir(path: Path):
        try:
            return list(path.iterdir())
        except (OSError, PermissionError):
            return []

    @staticmethod
    def _safe_resolve(path: Path) -> str:
        try:
            return str(path.resolve())
        except (OSError, PermissionError):
            return str(path)

    @classmethod
    def _candidate_roots(cls):
        app_root = cls._app_root()
        local_appdata = os.environ.get("LOCALAPPDATA")
        roots = [
            os.environ.get("FFMPEG_HOME"),
            os.environ.get("FFMPEG_BIN"),
            os.environ.get("FFMPEG_DIR"),
            app_root / "data" / "tools",
            app_root / "data" / "tools" / "bin",
            app_root / "ffmpeg",
            app_root / "ffmpeg" / "bin",
            r"D:\ffmpeg-8.0.1-essentials_build",
            r"D:\ffmpeg-8.0.1-essentials_build\bin",
        ]
        if local_appdata:
            roots.append(Path(local_appdata) / "CapCut" / "Apps")

        unique = []
        for root in roots:
            if not root:
                continue
            path = cls._safe_path(root)
            if path is None:
                continue
            if cls._safe_is_file(path):
                path = path.parent
            candidates = [path, path / "bin"]
            if path.name.lower() == "apps" and path.parent.name.lower() == "capcut":
                for child in cls._safe_iterdir(path):
                    candidates.extend([child, child / "bin"])
            for candidate in candidates:
                if cls._safe_is_dir(candidate):
                    normalized = cls._safe_resolve(candidate)
                    if normalized not in unique:
                        unique.append(normalized)
        return unique

    @classmethod
    def resolve_executable(cls, name: str) -> str:
        exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"
        found = shutil.which(exe_name) or shutil.which(name)
        if found:
            return found

        for root in cls._candidate_roots():
            candidate = Path(root) / exe_name
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except (OSError, PermissionError):
                continue

        for root in cls._candidate_roots():
            candidate = Path(root) / name
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except (OSError, PermissionError):
                continue

        for root in cls._candidate_roots():
            candidate = Path(root) / f"{name}.cmd"
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except (OSError, PermissionError):
                continue

        for root in cls._candidate_roots():
            candidate = Path(root) / f"{name}.bat"
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except (OSError, PermissionError):
                continue

        for root in cls._candidate_roots():
            candidate = Path(root) / exe_name
            try:
                if candidate.exists():
                    return str(candidate)
            except (OSError, PermissionError):
                continue

        return exe_name

    @classmethod
    def _resolve_optional(cls, name: str) -> Optional[str]:
        exe_name = name if name.lower().endswith(".exe") else f"{name}.exe"
        found = shutil.which(exe_name) or shutil.which(name)
        if found:
            return found
        for root in cls._candidate_roots():
            candidate = Path(root) / exe_name
            try:
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            except (OSError, PermissionError):
                continue
        return None

    @classmethod
    def ffplay_executable(cls) -> str:
        return cls.resolve_executable("ffplay")

    @classmethod
    def optional_ffmpeg_executable(cls) -> Optional[str]:
        return cls._resolve_optional("ffmpeg")

    @classmethod
    def optional_ffplay_executable(cls) -> Optional[str]:
        return cls._resolve_optional("ffplay")

    @classmethod
    def optional_ffprobe_executable(cls) -> Optional[str]:
        return cls._resolve_optional("ffprobe")

    @classmethod
    def ffmpeg_executable(cls) -> str:
        return cls.resolve_executable("ffmpeg")

    @classmethod
    def ffprobe_executable(cls) -> str:
        return cls.resolve_executable("ffprobe")

    @staticmethod
    def subprocess_kwargs(**extra):
        """Hide FFmpeg/ffprobe console windows on Windows subprocess calls."""
        kwargs = dict(extra)
        # Fix UnicodeDecodeError trên Windows khi tên file có tiếng Việt/Trung:
        # subprocess mặc định dùng cp1252, không decode được nhiều ký tự unicode.
        # Chỉ set encoding khi caller dùng text=True (không conflict với binary mode).
        if kwargs.get("text", False) or extra.get("text", False):
            kwargs.setdefault("encoding", "utf-8")
            kwargs.setdefault("errors", "replace")
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if flags:
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | flags
            try:
                startupinfo = kwargs.get("startupinfo") or subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                kwargs["startupinfo"] = startupinfo
            except Exception:
                pass
        return kwargs

    @classmethod
    def bootstrap_environment(cls) -> str:
        """Prepend a discovered FFmpeg bin dir to PATH so bare ffmpeg/ffprobe works too."""
        if cls._bootstrapped:
            return cls._cached_bin_dir or ""

        cls._bootstrapped = True
        ffmpeg_path = cls.ffmpeg_executable()
        try:
            ffmpeg_ok = ffmpeg_path.lower().endswith("ffmpeg.exe") and os.path.exists(ffmpeg_path)
        except (OSError, PermissionError):
            ffmpeg_ok = False
        if ffmpeg_ok:
            ffmpeg_file = Path(ffmpeg_path)
            try:
                bin_dir = str(ffmpeg_file.resolve().parent)
            except (OSError, PermissionError):
                bin_dir = str(ffmpeg_file.parent)
            cls._cached_bin_dir = bin_dir
            current_path = os.environ.get("PATH", "")
            path_parts = current_path.split(os.pathsep) if current_path else []
            if bin_dir not in path_parts:
                os.environ["PATH"] = bin_dir + (os.pathsep + current_path if current_path else "")
            os.environ.setdefault("FFMPEG_HOME", bin_dir)
            return bin_dir

        return ""


FFmpegUtils.bootstrap_environment()
