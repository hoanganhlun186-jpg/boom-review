"""
Enhanced Voice Pipeline - Syncs with cut video and handles short scripts.
Integrates with AutoRecapPro's video cutting workflow.
"""

import os
import json
from typing import Dict, Optional, List, Tuple
from pathlib import Path

from core.voice_segments import VoiceSegmentsGenerator
from core.voice_concat import VoiceConcatenator
from core.voice_srt import VoiceSRTGenerator
from core.render_plan import RenderPlan


class EnhancedVoiceProcessingPipeline:
    """
    Enhanced voice pipeline that syncs with cut video segments.
    Handles script-to-video duration matching.
    """
    
    def __init__(self, project_dir: str, project_name: str = "AutoRecap"):
        self.project_dir = project_dir
        self.project_name = project_name
        Path(project_dir).mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.segments_generator = VoiceSegmentsGenerator(
            output_dir=os.path.join(project_dir, "segments")
        )
        self.concatenator = VoiceConcatenator(
            output_dir=os.path.join(project_dir, "concat")
        )
        self.srt_generator = VoiceSRTGenerator(
            output_dir=os.path.join(project_dir, "subtitles")
        )
        self.render_plan = RenderPlan(project_name, project_dir)
        
        # Results storage
        self.segments = []
        self.concatenated_audio_path = None
        self.srt_file_path = None
        self.metadata = {}
        self.cut_video_duration = None
        self.book_map = None
    
    def process_script_with_video_sync(
        self, 
        script: str, 
        cut_video_duration: float,
        voice: str = "vi-VN-HoaiMyNeural",
        book_map: Optional[List[Dict]] = None,
        render_blocks: Optional[List[Dict]] = None,
        min_voice_duration: Optional[float] = None
    ) -> bool:
        """
        Process script with cut video synchronization.
        
        Args:
            script: Input script with markers
            cut_video_duration: Duration of the cut video in seconds
            voice: Voice ID for TTS
            book_map: Video segment mapping (from VideoCutter)
            render_blocks: Render block timing info
            min_voice_duration: Minimum voice duration (defaults to cut_video_duration * 0.8)
        
        Returns:
            True if successful
        """
        try:
            # Coerce and normalize cut_video_duration into float seconds.
            # Accept formats: float seconds, integer seconds, milliseconds (large int),
            # or string "SS", "MM:SS", "HH:MM:SS".
            raw_cut = cut_video_duration
            parsed_cut = None
            try:
                if isinstance(raw_cut, str):
                    s = raw_cut.strip()
                    if ':' in s:
                        parts = [float(p) for p in s.split(':')]
                        if len(parts) == 3:
                            parsed_cut = parts[0]*3600 + parts[1]*60 + parts[2]
                        elif len(parts) == 2:
                            parsed_cut = parts[0]*60 + parts[1]
                        else:
                            parsed_cut = float(parts[0])
                    else:
                        parsed_cut = float(s)
                else:
                    parsed_cut = float(raw_cut)
                # If value looks like milliseconds (very large), convert to seconds
                if parsed_cut > 1e5:
                    parsed_cut = parsed_cut / 1000.0
            except Exception:
                parsed_cut = None
                if self.render_plan.progress_callback:
                    self.render_plan.progress_callback(
                        f"⚠️ Không thể parse cut_video_duration: {raw_cut}. Sẽ cố gắng dùng giá trị thô."
                    )
                try:
                    parsed_cut = float(raw_cut)
                except Exception:
                    parsed_cut = 0.0

            cut_video_duration = float(parsed_cut)
            self.cut_video_duration = cut_video_duration
            if self.render_plan.progress_callback:
                self.render_plan.progress_callback(
                    f"ℹ️ Parsed cut_video_duration -> {cut_video_duration:.2f}s (raw={raw_cut})"
                )
            self.book_map = book_map or []
            min_voice_duration = min_voice_duration or (cut_video_duration * 0.8)
            
            # Create render plan
            self.render_plan.create_default_pipeline()
            self.render_plan.config = {
                'cut_video_duration': cut_video_duration,
                'min_voice_duration': min_voice_duration,
                'book_map_segments': len(self.book_map),
            }
            
            # Step 1: Analyze script and estimate duration
            self.render_plan.start_step("PARSE_SCRIPT")
            try:
                parsed_segments = self.segments_generator.parse_script_for_segments(script)
                
                # Estimate voice duration
                estimated_words = sum(len(seg['text'].split()) for seg in parsed_segments)
                estimated_duration = estimated_words / 2.5  # ~2.5 words per second
                
                self.render_plan.config['estimated_words'] = estimated_words
                self.render_plan.config['estimated_duration'] = estimated_duration
                
                # Check if script is too short
                if estimated_duration < min_voice_duration:
                    shortage = min_voice_duration - estimated_duration
                    if self.render_plan.progress_callback:
                        self.render_plan.progress_callback(
                            f"⚠️  Script ngắn ({estimated_duration:.1f}s), cần thêm {shortage:.1f}s để khớp video"
                        )
                    
                    # Add padding suggestion
                    script = self._pad_script_for_duration(
                        script, parsed_segments, shortage
                    )
                    parsed_segments = self.segments_generator.parse_script_for_segments(script)
                
                self.render_plan.complete_step("PARSE_SCRIPT", f"{len(parsed_segments)} segments")
            except Exception as e:
                self.render_plan.fail_step("PARSE_SCRIPT", str(e))
                return False
            
            # Step 2: Generate voice segments
            self.render_plan.start_step("GENERATE_SEGMENTS")
            try:
                self.segments = self.segments_generator.generate_segments_sync(
                    script, voice=voice
                )
                
                # Export segments metadata
                segments_meta_file = os.path.join(
                    self.segments_generator.output_dir,
                    "segments_metadata.json"
                )
                self.segments_generator.export_segments_metadata(segments_meta_file)
                
                # Calculate total voice duration
                total_voice_duration = sum(seg.audio_duration for seg in self.segments)
                self.render_plan.config['actual_voice_duration'] = total_voice_duration
                
                self.render_plan.complete_step("GENERATE_SEGMENTS", segments_meta_file)
            except Exception as e:
                self.render_plan.fail_step("GENERATE_SEGMENTS", str(e))
                return False
            
            # Step 3: Concatenate audio with dynamic silence adjustment
            self.render_plan.start_step("CONCAT_AUDIO")
            try:
                segment_paths = [seg.audio_path for seg in self.segments if seg.audio_path]
                
                # Calculate silence duration to match video
                total_voice_only = sum(seg.audio_duration for seg in self.segments)
                num_gaps = len(self.segments) - 1
                
                if num_gaps > 0 and total_voice_only < cut_video_duration:
                    # Distribute remaining time as silence
                    target_silence = (cut_video_duration - total_voice_only) / num_gaps
                    silence_duration = min(max(0.2, target_silence), 1.5)  # Clamp between 0.2-1.5s
                else:
                    silence_duration = 0.3
                
                self.render_plan.config['silence_duration'] = silence_duration
                
                self.concatenated_audio_path = self.concatenator.concatenate_segments(
                    segment_paths,
                    silence_duration=silence_duration,
                    output_path=os.path.join(
                        self.concatenator.output_dir,
                        "concatenated_voice.wav"
                    )
                )
                
                final_duration = self.concatenator.total_duration
                duration_diff = abs(final_duration - cut_video_duration)
                
                if duration_diff > 5.0:  # More than 5 seconds difference
                    if self.render_plan.progress_callback:
                        self.render_plan.progress_callback(
                            f"⚠️  Voice ({final_duration:.1f}s) khác video ({cut_video_duration:.1f}s) {duration_diff:.1f}s"
                        )
                
                self.render_plan.complete_step("CONCAT_AUDIO", self.concatenated_audio_path)
            except Exception as e:
                self.render_plan.fail_step("CONCAT_AUDIO", str(e))
                return False
            
            # Step 4: Generate SRT synced with video timeline
            self.render_plan.start_step("GENERATE_SRT")
            try:
                segments_meta_dict = {
                    'segments': [seg.to_dict() for seg in self.segments]
                }
                
                # Generate SRT synced with actual video duration
                self.srt_file_path = self.srt_generator.sync_with_video_timeline(
                    video_duration=cut_video_duration,
                    segments_metadata=segments_meta_dict,
                    output_file=os.path.join(
                        self.srt_generator.output_dir,
                        "voice_video_sync.srt"
                    )
                )
                
                # Also generate voice-only SRT (without video sync)
                voice_only_srt = self.srt_generator.generate_srt_from_segments(
                    segments_meta_dict,
                    output_file=os.path.join(
                        self.srt_generator.output_dir,
                        "voice_natural.srt"
                    )
                )
                
                self.render_plan.complete_step("GENERATE_SRT", self.srt_file_path)
            except Exception as e:
                self.render_plan.fail_step("GENERATE_SRT", str(e))
                return False
            
            # Step 5: Map voice to video segments (book_map)
            if self.book_map:
                self.render_plan.start_step("SELECT_KEYFRAMES")
                try:
                    voice_to_video_map = self._map_voice_to_video_segments()
                    
                    # Export mapping
                    mapping_file = os.path.join(self.project_dir, "voice_video_mapping.json")
                    with open(mapping_file, 'w', encoding='utf-8') as f:
                        json.dump(voice_to_video_map, f, indent=2, ensure_ascii=False)
                    
                    self.render_plan.complete_step("SELECT_KEYFRAMES", mapping_file)
                except Exception as e:
                    self.render_plan.fail_step("SELECT_KEYFRAMES", str(e))
            
            # Step 6: Export metadata
            self.render_plan.start_step("FINALIZE")
            try:
                self.metadata = {
                    'project_name': self.project_name,
                    'total_segments': len(self.segments),
                    'voice': voice,
                    'cut_video_duration': cut_video_duration,
                    'actual_voice_duration': self.concatenator.total_duration,
                    'duration_match': abs(self.concatenator.total_duration - cut_video_duration) < 5.0,
                    'segments_directory': self.segments_generator.output_dir,
                    'concatenated_audio': self.concatenated_audio_path,
                    'srt_file': self.srt_file_path,
                    'silence_adjustment': self.render_plan.config.get('silence_duration', 0.3),
                }
                
                # Export render plan
                plan_file = self.render_plan.export_plan(
                    os.path.join(self.project_dir, "render_plan.json")
                )
                
                # Export metadata
                metadata_file = os.path.join(self.project_dir, "pipeline_metadata.json")
                with open(metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(self.metadata, f, indent=2, ensure_ascii=False)
                
                self.render_plan.complete_step("FINALIZE", metadata_file)
            except Exception as e:
                self.render_plan.fail_step("FINALIZE", str(e))
                return False
            
            return True
        
        except Exception as e:
            if self.render_plan.progress_callback:
                self.render_plan.progress_callback(f"Pipeline error: {str(e)}")
            return False
    
    def _pad_script_for_duration(
        self, 
        script: str, 
        parsed_segments: List[Dict], 
        shortage_seconds: float
    ) -> str:
        """
        Add padding to script when it's too short.
        Uses natural transitions and recaps.
        """
        # Calculate needed words
        needed_words = int(shortage_seconds * 2.5)
        
        # Add transitional phrases
        padding_phrases = [
            "\n\n[CUT] Để hiểu rõ hơn về câu chuyện này, chúng ta cần xem xét từng chi tiết quan trọng.",
            "\n\n[CUT] Mỗi khoảnh khắc trong phim đều ẩn chứa ý nghĩa sâu xa.",
            "\n\n[CUT] Những tình tiết này kết nối với nhau tạo nên bức tranh hoàn chỉnh.",
            "\n\n[CUT] Đây là những gì làm nên sức hấp dẫn của bộ phim.",
        ]
        
        # Add contextual padding based on needed words
        if needed_words < 30:
            # Short padding
            script += padding_phrases[0]
        elif needed_words < 60:
            # Medium padding
            script += padding_phrases[0] + padding_phrases[1]
        else:
            # Longer padding
            script += padding_phrases[0] + padding_phrases[1] + padding_phrases[2]
        
        return script
    
    def _map_voice_to_video_segments(self) -> Dict:
        """Map voice segments to video book_map segments."""
        if not self.book_map or not self.segments:
            return {}
        
        mapping = {
            'voice_segments': len(self.segments),
            'video_books': len(self.book_map),
            'mappings': []
        }
        
        # Simple mapping: distribute voice segments across video books
        for i, segment in enumerate(self.segments):
            # Find corresponding video book
            book_idx = min(i, len(self.book_map) - 1)
            
            mapping['mappings'].append({
                'voice_segment_id': segment.segment_id,
                'voice_text': segment.text[:50] + '...' if len(segment.text) > 50 else segment.text,
                'voice_duration': segment.audio_duration,
                'video_book_index': book_idx,
                'marker': 'KEEP' if 'KEEP' in segment.text.upper() else 'CUT',
            })
        
        return mapping
    
    def get_output_files(self) -> Dict[str, str]:
        """Get all output files from the pipeline."""
        return {
            'concatenated_audio': self.concatenated_audio_path,
            'srt_file': self.srt_file_path,
            'render_plan': os.path.join(self.project_dir, "render_plan.json"),
            'metadata': os.path.join(self.project_dir, "pipeline_metadata.json"),
            'segments_directory': self.segments_generator.output_dir,
            'voice_video_mapping': os.path.join(self.project_dir, "voice_video_mapping.json"),
        }
    
    def get_summary(self) -> str:
        """Get pipeline execution summary with video sync info."""
        summary = self.render_plan.get_summary()
        
        if self.metadata:
            match_status = "✅ Khớp" if self.metadata.get('duration_match') else "⚠️  Chênh lệch"
            
            summary += f"""
📊 VOICE-VIDEO SYNC:
  Cut Video Duration: {self.metadata.get('cut_video_duration', 0):.2f}s
  Voice Duration: {self.metadata.get('actual_voice_duration', 0):.2f}s
  Sync Status: {match_status}
  Silence Adjustment: {self.metadata.get('silence_adjustment', 0):.2f}s
  
📁 OUTPUT FILES:
  Audio: {os.path.basename(self.concatenated_audio_path or 'N/A')}
  Subtitles: {os.path.basename(self.srt_file_path or 'N/A')}
  Segments: {self.metadata.get('total_segments', 0)}
"""
        
        return summary