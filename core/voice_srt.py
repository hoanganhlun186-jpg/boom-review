"""
Voice SRT - Synchronizes voice segments with subtitles and timing.
Creates SRT files that match the concatenated voiceover.
"""

import os
import json
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class SRTEntry:
    """Represents a single SRT subtitle entry."""
    index: int
    start_time: str
    end_time: str
    text: str
    
    def to_srt_string(self) -> str:
        """Convert to SRT format string."""
        return f"{self.index}\n{self.start_time} --> {self.end_time}\n{self.text}\n"


class VoiceSRTGenerator:
    """Generates SRT subtitle files synchronized with voice segments."""
    
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or "."
        self.srt_entries: List[SRTEntry] = []
        self.timing_map = {}
        # Ensure the output directory exists to avoid FileNotFoundError when writing SRT files
        if self.output_dir and not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
    
    @staticmethod
    def seconds_to_srt_time(seconds: float) -> str:
        """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    @staticmethod
    def srt_time_to_seconds(time_str: str) -> float:
        """Convert SRT timestamp to seconds."""
        parts = time_str.replace(',', '.').split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        
        return hours * 3600 + minutes * 60 + seconds
    
    def generate_srt_from_segments(self, segments_metadata: Dict, 
                                   output_file: Optional[str] = None) -> str:
        """
        Generate SRT file from voice segments.
        
        Args:
            segments_metadata: Dict containing segment information
            output_file: Output SRT file path
        
        Returns:
            Path to generated SRT file
        """
        output_file = output_file or os.path.join(self.output_dir, "voice_sync.srt")
        
        self.srt_entries = []
        current_time = 0.0
        
        segments = segments_metadata.get('segments', [])
        
        for segment_idx, segment in enumerate(segments, 1):
            text = segment.get('text', '')
            duration = segment.get('audio_duration', 0.0)
            
            if not text or duration <= 0:
                continue
            
            # Create SRT entry
            start_time = self.seconds_to_srt_time(current_time)
            end_time = self.seconds_to_srt_time(current_time + duration)
            
            entry = SRTEntry(
                index=segment_idx,
                start_time=start_time,
                end_time=end_time,
                text=text
            )
            
            self.srt_entries.append(entry)
            
            # Update timing map
            self.timing_map[segment_idx] = {
                'text': text,
                'start': current_time,
                'end': current_time + duration,
                'duration': duration,
            }
            
            # Move to next segment (add small silence gap)
            current_time += duration + 0.3  # 300ms gap
        
        # Write SRT file
        self._write_srt_file(output_file)
        
        return output_file
    
    def _write_srt_file(self, output_file: str):
        """Write SRT entries to file."""
        # Ensure parent directory exists to avoid FileNotFoundError
        dirpath = os.path.dirname(os.path.abspath(output_file))
        if dirpath and not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in self.srt_entries:
                f.write(entry.to_srt_string())
                f.write('\n')
    
    def split_text_for_srt(self, text: str, max_chars_per_line: int = 42,
                          max_lines: int = 2) -> str:
        """
        Split text into lines suitable for SRT display.
        
        Args:
            text: Text to split
            max_chars_per_line: Maximum characters per line
            max_lines: Maximum number of lines
        
        Returns:
            Formatted text with newlines
        """
        words = text.split()
        lines = []
        current_line = []
        current_length = 0
        
        for word in words:
            word_length = len(word) + 1  # +1 for space
            
            if current_length + word_length > max_chars_per_line:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                    current_length = word_length
                else:
                    # Word is too long, add it anyway
                    lines.append(word)
                    current_length = 0
            else:
                current_line.append(word)
                current_length += word_length
        
        if current_line:
            lines.append(' '.join(current_line))
        
        # Limit to max_lines
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        
        return '\n'.join(lines)
    
    def sync_with_video_timeline(self, video_duration: float, 
                                segments_metadata: Dict,
                                output_file: Optional[str] = None) -> str:
        """
        Synchronize SRT with video timeline.
        Adjusts timing to match video duration.
        
        Args:
            video_duration: Total video duration in seconds
            segments_metadata: Segment information
            output_file: Output SRT file path
        
        Returns:
            Path to synchronized SRT file
        """
        output_file = output_file or os.path.join(self.output_dir, "video_sync.srt")
        
        # First generate base SRT
        self.generate_srt_from_segments(segments_metadata)
        
        if not self.srt_entries:
            return output_file
        
        # Get total voice duration (convert SRT end_time strings to seconds)
        total_voice_duration = sum(
            self.srt_time_to_seconds(entry.end_time)
            for entry in self.srt_entries
        )
        
        # Calculate scale factor if needed
        if total_voice_duration > 0 and video_duration > 0:
            scale_factor = video_duration / total_voice_duration
        else:
            scale_factor = 1.0
        
        # Adjust timing based on scale factor
        adjusted_entries = []
        for entry in self.srt_entries:
            start_seconds = self.srt_time_to_seconds(entry.start_time)
            end_seconds = self.srt_time_to_seconds(entry.end_time)
            
            # Scale timing
            adjusted_start = start_seconds * scale_factor
            adjusted_end = end_seconds * scale_factor
            
            # Clamp to video duration
            adjusted_end = min(adjusted_end, video_duration)
            
            adjusted_entry = SRTEntry(
                index=entry.index,
                start_time=self.seconds_to_srt_time(adjusted_start),
                end_time=self.seconds_to_srt_time(adjusted_end),
                text=entry.text
            )
            adjusted_entries.append(adjusted_entry)
        
        self.srt_entries = adjusted_entries
        self._write_srt_file(output_file)
        
        return output_file
    
    def get_timing_info(self) -> Dict:
        """Get timing information from SRT entries."""
        return {
            'total_entries': len(self.srt_entries),
            'entries': self.timing_map,
        }