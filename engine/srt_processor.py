import re
from typing import List, Tuple, Optional


class SRTParser:
    """
    Parse SRT subtitle files để trích xuất timeline thông tin
    """
    
    @staticmethod
    def parse_srt(file_path: str) -> List[dict]:
        """
        Parse SRT file
        
        Returns:
            List[{
                'index': int,
                'start': str (HH:MM:SS,mmm),
                'end': str,
                'text': str,
                'start_seconds': float,
                'end_seconds': float
            }]
        """
        subtitles = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            content = (
                content.replace('\r\r\n', '\n')
                .replace('\r\n', '\n')
                .replace('\r', '\n')
            )

            lines = [line.strip() for line in content.split('\n')]
            cursor = 0

            def _next_nonblank(start_pos: int) -> int:
                pos = start_pos
                while pos < len(lines) and not lines[pos]:
                    pos += 1
                return pos

            def _is_index_line(value: str) -> bool:
                return value.strip().lstrip('\ufeff').isdigit()

            def _starts_next_cue(pos: int) -> bool:
                pos = _next_nonblank(pos)
                if pos >= len(lines) or not _is_index_line(lines[pos]):
                    return False
                time_pos = _next_nonblank(pos + 1)
                return time_pos < len(lines) and '-->' in lines[time_pos]

            while cursor < len(lines):
                cursor = _next_nonblank(cursor)
                if cursor >= len(lines):
                    break
                if not _is_index_line(lines[cursor]):
                    cursor += 1
                    continue

                try:
                    index = int(lines[cursor].lstrip('\ufeff'))
                    cursor = _next_nonblank(cursor + 1)
                    if cursor >= len(lines) or '-->' not in lines[cursor]:
                        continue

                    times = re.split(r'\s*-->\s*', lines[cursor], maxsplit=1)
                    if len(times) != 2:
                        continue
                    start = times[0].strip()
                    end = times[1].strip()
                    cursor += 1

                    text_lines = []
                    while cursor < len(lines):
                        if not lines[cursor]:
                            if _starts_next_cue(cursor + 1):
                                cursor = _next_nonblank(cursor + 1)
                                break
                            cursor += 1
                            continue
                        if _starts_next_cue(cursor):
                            break
                        text_lines.append(lines[cursor])
                        cursor += 1

                    text = '\n'.join(text_lines).strip()
                    if not text:
                        continue

                    # Convert to seconds
                    start_sec = SRTParser.timecode_to_seconds(start)
                    end_sec = SRTParser.timecode_to_seconds(end)

                    subtitles.append({
                        'index': index,
                        'start': start,
                        'end': end,
                        'text': text,
                        'start_seconds': start_sec,
                        'end_seconds': end_sec
                    })
                except Exception as e:
                    cursor += 1
                    continue
            
            return subtitles
        except Exception as e:
            raise Exception(f"Lỗi parse SRT: {str(e)}")
    
    @staticmethod
    def timecode_to_seconds(timecode: str) -> float:
        """
        Convert SRT timecode (HH:MM:SS,mmm) to seconds
        """
        match = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)', timecode)
        if not match:
            raise ValueError(f"Invalid timecode: {timecode}")
        
        hours, minutes, seconds, milliseconds = map(int, match.groups())
        total_seconds = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
        return total_seconds
    
    @staticmethod
    def seconds_to_timecode(seconds: float) -> str:
        """
        Convert seconds to SRT timecode (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    @staticmethod
    def extract_scenes_from_subtitles(
        subtitles: List[dict],
        scene_threshold: float = 5.0
    ) -> List[Tuple[float, float, str]]:
        """
        Trích xuất các scene/đoạn phim từ subtitles dựa trên khoảng cách thời gian
        
        Args:
            subtitles: List subtitle objects
            scene_threshold: Khoảng cách tối thiểu giữa các subtitle để coi là scene khác (giây)
        
        Returns:
            List[(start_time, end_time, scene_label)]
        """
        if not subtitles:
            return []
        
        scenes = []
        scene_start = subtitles[0]['start_seconds']
        
        for i in range(len(subtitles) - 1):
            current = subtitles[i]
            next_sub = subtitles[i + 1]
            
            gap = next_sub['start_seconds'] - current['end_seconds']
            
            if gap > scene_threshold:
                # Kết thúc scene hiện tại
                scene_end = current['end_seconds']
                scene_label = f"Scene {len(scenes) + 1}"
                scenes.append((scene_start, scene_end, scene_label))
                
                # Bắt đầu scene mới
                scene_start = next_sub['start_seconds']
        
        # Thêm scene cuối cùng
        if subtitles:
            scenes.append((scene_start, subtitles[-1]['end_seconds'], f"Scene {len(scenes) + 1}"))
        
        return scenes
    
    @staticmethod
    def create_timeline_from_cut_pattern(
        video_duration: float,
        keep_seconds: int = 3,
        skip_seconds: int = 10
    ) -> List[Tuple[float, float, str]]:
        """
        Tạo timeline dựa trên mẫu keep/skip
        
        Returns:
            List[(start_time, end_time, marker)]
            Marker có thể là 'KEEP' hoặc 'SKIP'
        """
        timeline = []
        current_time = 0
        cycle = keep_seconds + skip_seconds
        marker_index = 0
        
        while current_time < video_duration:
            # Calculate position in cycle
            pos_in_cycle = current_time % cycle
            
            if pos_in_cycle < keep_seconds:
                # KEEP phase
                end_time = min(current_time + (keep_seconds - pos_in_cycle), video_duration)
                marker = 'KEEP'
            else:
                # SKIP phase
                end_time = min(current_time + (cycle - pos_in_cycle), video_duration)
                marker = 'SKIP'
            
            timeline.append((current_time, end_time, marker))
            current_time = end_time
            marker_index += 1
        
        return timeline
    
    @staticmethod
    def merge_subtitle_chunks(
        subtitles: List[dict],
        chunk_duration: float = 10.0
    ) -> List[dict]:
        """
        Nhóm các subtitle lại thành các chunk để dễ dàng tạo script
        
        Returns:
            List[{
                'chunk_index': int,
                'start_seconds': float,
                'end_seconds': float,
                'text': str (combined),
                'subtitle_count': int
            }]
        """
        chunks = []
        
        if not subtitles:
            return chunks
        
        chunk_start = subtitles[0]['start_seconds']
        chunk_texts = []
        chunk_start_idx = 0
        
        for i, sub in enumerate(subtitles):
            chunk_texts.append(sub['text'])
            
            # Check if we should end this chunk
            if sub['end_seconds'] - chunk_start >= chunk_duration or i == len(subtitles) - 1:
                chunk_end = sub['end_seconds']
                combined_text = ' '.join(chunk_texts)
                
                chunks.append({
                    'chunk_index': len(chunks),
                    'start_seconds': chunk_start,
                    'end_seconds': chunk_end,
                    'text': combined_text,
                    'subtitle_count': len(chunk_texts)
                })
                
                # Reset for next chunk
                chunk_start = sub['end_seconds'] if i < len(subtitles) - 1 else chunk_end
                chunk_texts = []
        
        return chunks
