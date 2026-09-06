import re
from typing import List, Tuple, Dict

class ScriptProcessor:
    """Parse script with [KEEP]/[CUT] markers and generate video cut timeline"""
    
    def __init__(self, script: str, keep_seconds: int = 3, skip_seconds: int = 10):
        self.script = script
        self.keep_seconds = keep_seconds
        self.skip_seconds = skip_seconds
        self.cycle = keep_seconds + skip_seconds
        self.segments = []
        self._parse_script()
    
    def _parse_script(self):
        """Parse script to extract segments with markers"""
        # Split by lines and identify KEEP/CUT markers
        lines = self.script.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check for markers
            if line.startswith('[KEEP]'):
                marker = 'KEEP'
                text = line[6:].strip()
            elif line.startswith('[CUT]'):
                marker = 'CUT'
                text = line[5:].strip()
            else:
                # Default: if no marker, determine from context
                marker = 'KEEP'
                text = line
            
            if text:
                self.segments.append({'marker': marker, 'text': text})
    
    def get_segments(self) -> List[Dict]:
        """Return list of segments with markers"""
        return self.segments
    
    def estimate_segment_duration(self, text: str) -> float:
        """Estimate duration of text segment based on word count (avg 2.5 words/sec)"""
        words = len(text.split())
        return words / 2.5  # Vietnamese average speech rate
    
    def generate_timeline(self) -> List[Tuple[float, float, str]]:
        """
        Generate timeline of video segments to keep based on script markers.
        Returns list of (start_time, end_time, marker) tuples in seconds.
        
        Timeline is built sequentially based on audio duration of each segment.
        """
        timeline = []
        current_time = 0.0
        
        for segment in self.segments:
            duration = self.estimate_segment_duration(segment['text'])
            marker = segment['marker']
            
            timeline.append((current_time, current_time + duration, marker))
            current_time += duration
        
        return timeline
    
    def generate_ffmpeg_select_filter(self) -> str:
        """
        Generate FFmpeg select filter to keep segments marked as [KEEP].
        
        Instead of simple "keep 3s, skip 10s" pattern, this creates:
        select='(keep_conditions)' to dynamically cut based on script.
        
        Returns: select filter expression for FFmpeg
        """
        timeline = self.generate_timeline()
        
        # Build conditions for each KEEP segment
        conditions = []
        for start, end, marker in timeline:
            if marker == 'KEEP':
                # t >= start AND t < end
                conditions.append(f"(t>={start:.2f}*and*t<{end:.2f})")
        
        if not conditions:
            # Fallback: if no KEEP markers, use default pattern
            cycle = self.keep_seconds + self.skip_seconds
            return f"select='lt(mod(t,{cycle}),{self.keep_seconds})'"
        
        # Combine conditions with OR
        combined = '*or*'.join(conditions)
        return f"select='{combined}'"
    
    def get_total_keep_duration(self) -> float:
        """Calculate total duration of KEEP segments"""
        timeline = self.generate_timeline()
        total = sum(end - start for start, end, marker in timeline if marker == 'KEEP')
        return total
    
    def get_total_script_duration(self) -> float:
        """Get total duration of entire script"""
        timeline = self.generate_timeline()
        if not timeline:
            return 0.0
        return timeline[-1][1]  # End time of last segment
