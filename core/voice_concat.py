"""
Voice Concat - Concatenates individual voice segments into a single audio track.
Handles audio normalization, silence insertion, and transitions.
"""

import os
import sys
import subprocess
import tempfile
from typing import List, Optional, Dict
from pathlib import Path
import json

# ──────────────────────────────────────────────────────────────────
# FFmpeg: dùng FFmpegUtils (tự tìm từ thư mục app, env var, PATH)
# ──────────────────────────────────────────────────────────────────

from utils.helpers import FFmpegUtils
FFmpegUtils.bootstrap_environment()


def _subprocess_hidden_kwargs() -> dict:
    return FFmpegUtils.subprocess_kwargs()


def _ffmpeg_executable() -> Optional[str]:
    return FFmpegUtils.optional_ffmpeg_executable()


# ──────────────────────────────────────────────────────────────────
# Import pydub sau khi ffmpeg đã có trong PATH
# ──────────────────────────────────────────────────────────────────

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except Exception:
    PYDUB_AVAILABLE = False
    AudioSegment = None


def _concat_wavs_ffmpeg(chunk_files: List[str], output_path: str) -> None:
    """Concatenate multiple WAV files using ffmpeg subprocess (no pydub)."""
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        if os.name != "nt" and PYDUB_AVAILABLE and AudioSegment is not None:
            combined = AudioSegment.empty()
            for f in chunk_files:
                segment = AudioSegment.from_wav(f)
                combined += segment
            combined.export(output_path, format="wav")
            return
        raise RuntimeError(
            "Cannot concatenate audio: ffmpeg not found.\n"
            "Install ffmpeg from https://ffmpeg.org/download.html"
        )

    concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    try:
        for cf in chunk_files:
            norm = cf.replace('\\', '/')
            concat_file.write(f"file '{norm}'\n")
        concat_file.close()

        cmd = [
            ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0',
            '-i', concat_file.name,
            '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1',
            output_path,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_hidden_kwargs())
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed:\n{stderr.decode('utf-8', errors='ignore')}"
            )
    finally:
        try:
            os.unlink(concat_file.name)
        except Exception:
            pass


class VoiceConcatenator:
    """Concatenates multiple voice segment audio files into one continuous track."""
    
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or "."
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self.segments_info = []
        self.concatenated_audio = None
        self.total_duration = 0.0
    
    def load_segments_metadata(self, metadata_file: str) -> Dict:
        """Load segment metadata from JSON file."""
        with open(metadata_file, 'r', encoding='utf-8') as f:
            self.segments_info = json.load(f)
        return self.segments_info
    
    def concatenate_segments(self, segment_paths: List[str], 
                           silence_duration: float = 0.3,
                           normalize: bool = True,
                           output_path: Optional[str] = None) -> str:
        """
        Concatenate audio segments into a single file.
        
        Args:
            segment_paths: List of audio file paths to concatenate
            silence_duration: Duration of silence between segments (seconds)
            normalize: Whether to normalize audio levels
            output_path: Output file path (default: concatenated_voice.wav)
        
        Returns:
            Path to the concatenated audio file
        """
        if not segment_paths:
            raise ValueError("No segment paths provided")
        
        output_path = output_path or os.path.join(self.output_dir, "concatenated_voice.wav")
        
        # ── Fast path: use pydub if available ──
        if os.name != "nt" and PYDUB_AVAILABLE and AudioSegment is not None:
            combined = AudioSegment.empty()
            silence = AudioSegment.silent(duration=int(silence_duration * 1000))
            
            for i, segment_path in enumerate(segment_paths):
                if not os.path.exists(segment_path):
                    raise FileNotFoundError(f"Segment not found: {segment_path}")
                
                segment = AudioSegment.from_file(segment_path)
                
                if i > 0:
                    combined += silence
                
                combined += segment
            
            if normalize:
                combined = self._normalize_audio(combined)
            
            combined.export(output_path, format="wav", bitrate="192k")
            self.concatenated_audio = combined
            self.total_duration = len(combined) / 1000.0
            return output_path
        
        # ── Fallback: use ffmpeg subprocess directly ──
        ffmpeg = _ffmpeg_executable()
        if not ffmpeg:
            raise RuntimeError(
                "No audio concatenation backend available.\n"
                "Install pydub: pip install pydub\n"
                "AND install ffmpeg from https://ffmpeg.org/download.html"
            )
        
        # Create a tiny silence WAV using ffmpeg
        silence_path = os.path.join(self.output_dir, "_silence_tmp.wav")
        subprocess.run([
            ffmpeg, '-y', '-f', 'lavfi', '-i',
            f'anullsrc=r=44100:cl=mono:d={silence_duration}',
            '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1',
            silence_path,
        ], capture_output=True, **_subprocess_hidden_kwargs())
        
        # Build a concat file list with silence between segments
        concat_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        )
        try:
            for i, sp in enumerate(segment_paths):
                if not os.path.exists(sp):
                    raise FileNotFoundError(f"Segment not found: {sp}")
                norm_sp = sp.replace('\\', '/')
                concat_file.write(f"file '{norm_sp}'\n")
                if i < len(segment_paths) - 1:
                    norm_sil = silence_path.replace('\\', '/')
                    concat_file.write(f"file '{norm_sil}'\n")
            concat_file.close()
            
            cmd = [
                ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'concat', '-safe', '0',
                '-i', concat_file.name,
                '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1',
                output_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, **_subprocess_hidden_kwargs())
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg concat failed:\n{proc.stderr.decode('utf-8', errors='ignore')}"
                )
        finally:
            try:
                os.unlink(concat_file.name)
            except Exception:
                pass
            try:
                os.unlink(silence_path)
            except Exception:
                pass
        
        # Estimate total duration (rough)
        self.total_duration = len(segment_paths) * 5.0  # rough estimate
        return output_path
    
    @staticmethod
    def _normalize_audio(audio: 'AudioSegment', target_db: float = -20.0) -> 'AudioSegment':
        """Normalize audio to target dB level."""
        if not PYDUB_AVAILABLE or AudioSegment is None:
            # pydub not available, skip normalization
            return audio
        
        try:
            # Calculate current loudness
            loudness = audio.dBFS
            
            # Calculate gain needed
            gain = target_db - loudness
            
            # Apply gain (avoid clipping)
            if gain > 0:
                # Boost carefully
                normalized = audio.apply_gain(min(gain, 6))  # Max 6dB boost
            else:
                # Reduce safely
                normalized = audio.apply_gain(gain)
            
            return normalized
        except Exception as e:
            # If normalization fails, return original
            return audio
    
    def concat_from_metadata(self, metadata_file: str, 
                            silence_duration: float = 0.3,
                            output_path: Optional[str] = None) -> str:
        """
        Concatenate segments using metadata file.
        
        Args:
            metadata_file: Path to segments metadata JSON file
            silence_duration: Duration of silence between segments
            output_path: Output audio file path
        
        Returns:
            Path to concatenated audio
        """
        metadata = self.load_segments_metadata(metadata_file)
        
        # Extract audio paths from segments
        segment_paths = []
        for segment in metadata.get('segments', []):
            if segment.get('audio_path'):
                segment_paths.append(segment['audio_path'])
        
        if not segment_paths:
            raise ValueError("No audio paths found in metadata")
        
        return self.concatenate_segments(
            segment_paths, 
            silence_duration=silence_duration,
            output_path=output_path
        )
    
    def get_concatenation_info(self) -> Dict:
        """Get information about the concatenated audio."""
        return {
            'total_duration': self.total_duration,
            'total_segments': len(self.segments_info),
            'output_path': getattr(self, '_last_output_path', None),
            'segments': self.segments_info,
        }
    
    def export_concatenation_metadata(self, output_file: str):
        """Export concatenation metadata."""
        import json
        metadata = self.get_concatenation_info()
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
