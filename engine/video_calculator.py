"""Calculate video output duration based on cutting parameters"""
import cv2

class VideoCalculator:
    """Calculate expected output video duration after cutting"""
    
    @staticmethod
    def get_video_duration(video_path):
        """Get video duration in seconds"""
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return None
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            
            if fps and frame_count:
                return frame_count / fps
            return None
        except Exception:
            return None
    
    @staticmethod
    def calculate_output_duration(input_video_path, keep_seconds, skip_seconds):
        """
        Calculate output video duration after cutting with keep/skip pattern.
        
        Example: input=60s, keep=3, skip=10
        - Cycle = 13s
        - Keep ratio = 3/13 = 23%
        - Output = 60 * 0.23 = ~13.8s
        
        Args:
            input_video_path: Path to input video
            keep_seconds: Seconds to keep in each cycle
            skip_seconds: Seconds to skip in each cycle
            
        Returns:
            (input_duration, output_duration, keep_ratio) or (None, None, None)
        """
        input_duration = VideoCalculator.get_video_duration(input_video_path)
        if input_duration is None:
            return None, None, None
        
        cycle = keep_seconds + skip_seconds
        if cycle <= 0:
            return None, None, None
        
        keep_ratio = keep_seconds / cycle
        output_duration = input_duration * keep_ratio
        
        return input_duration, output_duration, keep_ratio
    
    @staticmethod
    def format_duration(seconds):
        """Format seconds to MM:SS format"""
        if seconds is None:
            return "N/A"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"
    
    @staticmethod
    def estimate_script_words(output_duration):
        """Estimate number of words script should have for given duration.
        
        Vietnamese TTS: ~2.5-2.7 words per second
        """
        if output_duration is None:
            return None
        words_per_sec = 2.5
        return int(output_duration * words_per_sec)

    @staticmethod
    def _infer_scene_role(index, total, segment):
        """Infer narrative role for a kept segment based on its position in the cut video."""
        total = max(1, int(total or 1))
        index = max(0, int(index or 0))
        if total == 1:
            return "intro_setup"

        progress = index / max(1, total - 1)
        reason = str(segment.get("reason", "") or "").lower()
        score = float(segment.get("score", 0) or 0)

        if index == 0:
            return "intro_gioi_thieu"
        if progress < 0.25:
            if "dialog" in reason or "face" in reason:
                return "setup_nhan_vat"
            return "setup"
        if progress < 0.6:
            if "action" in reason or score >= 60:
                return "build_up_cang_thang"
            return "build_up"
        if progress < 0.85:
            if "action" in reason or score >= 70:
                return "climax"
            return "conflict"
        return "ending_hau_vi"

    @staticmethod
    def get_render_blocks(segments):
        """Create render blocks for AI based on the kept segments.

        Each block is mapped to its final position in the cut video.
        """
        blocks = []
        current_output_start = 0.0
        total_segments = len(segments)
        for i, seg in enumerate(segments):
            duration = max(0.0, float(seg.get('end', 0)) - float(seg.get('start', 0)))
            scene_role = VideoCalculator._infer_scene_role(i, total_segments, seg)
            block = {
                'block_id': i + 1,
                'start_in_final_video': round(current_output_start, 3),
                'duration': round(duration, 3),
                'end_in_final_video': round(current_output_start + duration, 3),
                'srt_range': f"{round(current_output_start, 3)}s - {round(current_output_start + duration, 3)}s",
                'original_srt_range': f"{seg.get('start', 0)}s - {seg.get('end', 0)}s",
                'original_start': float(seg.get('start', 0)),
                'original_end': float(seg.get('end', 0)),
                'cut_reason': seg.get('reason', ''),
                'smart_score': seg.get('score', 0),
                'scene_role': scene_role,
                'scene_role_label': {
                    'intro_gioi_thieu': 'Mở cảnh / giới thiệu',
                    'setup_nhan_vat': 'Giới thiệu nhân vật',
                    'setup': 'Setup / xây dựng',
                    'build_up': 'Đẩy mạch phim',
                    'build_up_cang_thang': 'Đẩy xung đột',
                    'conflict': 'Xung đột / phát triển',
                    'climax': 'Cao trào',
                    'ending_hau_vi': 'Kết / hậu vị',
                    'intro_setup': 'Mở cảnh / giới thiệu',
                }.get(scene_role, scene_role),
            }
            for key in (
                'scene_ids',
                'scene_id',
                'dialogue_text',
                'visual_anchor',
                'semantic_reason',
                'clip_window',
                'source',
                'subtitle_count',
                'importance_score',
                'story_act',
                'story_goal',
            ):
                if key in seg:
                    block[key] = seg.get(key)
            if seg.get('dialogue_text') and not block.get('srt_anchor'):
                block['srt_anchor'] = seg.get('dialogue_text')
            if seg.get('visual_anchor') and not block.get('visual_hint'):
                block['visual_hint'] = seg.get('visual_anchor')
            blocks.append(block)
            current_output_start += duration
        return blocks
