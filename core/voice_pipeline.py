"""
Voice Pipeline - Complete integration of all voice processing modules.
Coordinates VOICE_SEGMENTS, VOICE_CONCAT, VOICE_SRT, and RENDER_PLAN.
"""

import os
import json
from typing import Dict, Optional, List
from pathlib import Path

from core.voice_segments import VoiceSegmentsGenerator
from core.voice_concat import VoiceConcatenator
from core.voice_srt import VoiceSRTGenerator
from core.render_plan import RenderPlan


class VoiceProcessingPipeline:
    """Complete voice processing pipeline for video recap generation."""
    
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
    
    def process_script(self, script: str, voice: str = "vi-VN-HoaiMyNeural") -> bool:
        """
        Process script through the complete pipeline.
        
        Args:
            script: Input script with markers
            voice: Voice ID for TTS
        
        Returns:
            True if successful
        """
        try:
            # Create render plan
            self.render_plan.create_default_pipeline()
            
            # Step 1: Generate voice segments
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
                
                self.render_plan.complete_step("GENERATE_SEGMENTS", segments_meta_file)
            except Exception as e:
                self.render_plan.fail_step("GENERATE_SEGMENTS", str(e))
                return False
            
            # Step 2: Concatenate audio
            self.render_plan.start_step("CONCAT_AUDIO")
            try:
                # Get audio paths from segments
                segment_paths = [seg.audio_path for seg in self.segments if seg.audio_path]
                
                self.concatenated_audio_path = self.concatenator.concatenate_segments(
                    segment_paths,
                    silence_duration=0.3,
                    output_path=os.path.join(
                        self.concatenator.output_dir,
                        "concatenated_voice.wav"
                    )
                )
                
                self.render_plan.complete_step("CONCAT_AUDIO", self.concatenated_audio_path)
            except Exception as e:
                self.render_plan.fail_step("CONCAT_AUDIO", str(e))
                return False
            
            # Step 3: Generate SRT subtitles
            self.render_plan.start_step("GENERATE_SRT")
            try:
                # Load segments metadata
                segments_meta = self.segments_generator.segments  # Already loaded
                segments_meta_dict = {
                    'segments': [seg.to_dict() for seg in self.segments]
                }
                
                self.srt_file_path = self.srt_generator.generate_srt_from_segments(
                    segments_meta_dict,
                    output_file=os.path.join(
                        self.srt_generator.output_dir,
                        "voice_sync.srt"
                    )
                )
                
                self.render_plan.complete_step("GENERATE_SRT", self.srt_file_path)
            except Exception as e:
                self.render_plan.fail_step("GENERATE_SRT", str(e))
                return False
            
            # Step 4: Export metadata
            self.render_plan.start_step("FINALIZE")
            try:
                self.metadata = {
                    'project_name': self.project_name,
                    'total_segments': len(self.segments),
                    'voice': voice,
                    'segments_directory': self.segments_generator.output_dir,
                    'concatenated_audio': self.concatenated_audio_path,
                    'srt_file': self.srt_file_path,
                    'total_duration': self.concatenator.total_duration,
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
    
    def get_output_files(self) -> Dict[str, str]:
        """Get all output files from the pipeline."""
        return {
            'concatenated_audio': self.concatenated_audio_path,
            'srt_file': self.srt_file_path,
            'render_plan': os.path.join(self.project_dir, "render_plan.json"),
            'metadata': os.path.join(self.project_dir, "pipeline_metadata.json"),
            'segments_directory': self.segments_generator.output_dir,
        }
    
    def get_summary(self) -> str:
        """Get pipeline execution summary."""
        summary = self.render_plan.get_summary()
        
        if self.metadata:
            summary += f"""
📊 PIPELINE METADATA:
  Total Segments: {self.metadata.get('total_segments', 0)}
  Total Duration: {self.metadata.get('total_duration', 0):.2f} seconds
  Voice: {self.metadata.get('voice', 'Unknown')}
  
📁 OUTPUT FILES:
  Audio: {os.path.basename(self.concatenated_audio_path or 'N/A')}
  Subtitles: {os.path.basename(self.srt_file_path or 'N/A')}
"""
        
        return summary