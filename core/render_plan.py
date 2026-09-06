"""
Render Plan - Orchestrates the complete video recap rendering pipeline.
Manages workflow from script to final video with voice, subtitles, and visuals.
"""

import os
import json
import asyncio
from typing import Dict, List, Optional, Callable
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class RenderStep:
    """Represents a single rendering step in the pipeline."""
    name: str
    status: str  # pending, running, completed, failed
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: float = 0.0
    output: Optional[str] = None
    error: Optional[str] = None


class RenderPlan:
    """Orchestrates the complete video rendering pipeline."""
    
    def __init__(self, project_name: str, output_dir: str):
        self.project_name = project_name
        self.output_dir = output_dir
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        
        self.steps: Dict[str, RenderStep] = {}
        self.config: Dict = {}
        self.progress_callback: Optional[Callable] = None
        self.start_time = None
        self.end_time = None
    
    def set_progress_callback(self, callback: Callable):
        """Set callback function for progress updates."""
        self.progress_callback = callback
    
    def add_step(self, name: str, description: str = ""):
        """Add a rendering step to the plan."""
        self.steps[name] = RenderStep(name=name, status="pending")
        if self.progress_callback:
            self.progress_callback(f"Added step: {name} {description}")
    
    def start_step(self, name: str):
        """Mark a step as started."""
        import time
        if name in self.steps:
            self.steps[name].status = "running"
            self.steps[name].start_time = time.time()
            if self.progress_callback:
                self.progress_callback(f"▶️  Starting: {name}")
    
    def complete_step(self, name: str, output: Optional[str] = None):
        """Mark a step as completed."""
        import time
        if name in self.steps:
            self.steps[name].status = "completed"
            self.steps[name].end_time = time.time()
            self.steps[name].output = output
            if self.steps[name].start_time:
                self.steps[name].duration = (
                    self.steps[name].end_time - self.steps[name].start_time
                )
            if self.progress_callback:
                self.progress_callback(
                    f"✅ Completed: {name} ({self.steps[name].duration:.2f}s)"
                )
    
    def fail_step(self, name: str, error: str):
        """Mark a step as failed."""
        import time
        if name in self.steps:
            self.steps[name].status = "failed"
            self.steps[name].end_time = time.time()
            self.steps[name].error = error
            if self.steps[name].start_time:
                self.steps[name].duration = (
                    self.steps[name].end_time - self.steps[name].start_time
                )
            if self.progress_callback:
                self.progress_callback(f"❌ Failed: {name} - {error}")
    
    def create_default_pipeline(self) -> List[str]:
        """Create a default video recap rendering pipeline."""
        pipeline_steps = [
            "ANALYZE_VIDEO",
            "EXTRACT_METADATA",
            "DETECT_SCENES",
            "PARSE_SCRIPT",
            "GENERATE_SEGMENTS",
            "CONCAT_AUDIO",
            "GENERATE_SRT",
            "SELECT_KEYFRAMES",
            "ASSEMBLE_VIDEO",
            "MIX_AUDIO",
            "FINALIZE",
        ]
        
        for step in pipeline_steps:
            self.add_step(step)
        
        return pipeline_steps
    
    def execute_pipeline(self, tasks: Dict[str, Callable]) -> bool:
        """
        Execute the rendering pipeline.
        
        Args:
            tasks: Dict mapping step names to callable functions
        
        Returns:
            True if successful, False otherwise
        """
        import time
        self.start_time = time.time()
        
        try:
            for step_name in self.steps.keys():
                if self.steps[step_name].status == "completed":
                    continue
                
                self.start_step(step_name)
                
                # Execute task if provided
                if step_name in tasks:
                    try:
                        task_func = tasks[step_name]
                        if asyncio.iscoroutinefunction(task_func):
                            # Handle async tasks
                            try:
                                loop = asyncio.get_event_loop()
                            except RuntimeError:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                            
                            result = loop.run_until_complete(task_func())
                        else:
                            # Handle sync tasks
                            result = task_func()
                        
                        self.complete_step(step_name, output=str(result))
                    except Exception as e:
                        error_msg = str(e)
                        self.fail_step(step_name, error_msg)
                        return False
                else:
                    # Skip step if no task provided
                    self.complete_step(step_name)
            
            self.end_time = time.time()
            return True
        except Exception as e:
            if self.progress_callback:
                self.progress_callback(f"Pipeline failed: {str(e)}")
            return False
    
    def get_progress(self) -> Dict:
        """Get current pipeline progress."""
        total = len(self.steps)
        completed = sum(1 for step in self.steps.values() if step.status == "completed")
        failed = sum(1 for step in self.steps.values() if step.status == "failed")
        
        progress_percent = (completed / total * 100) if total > 0 else 0
        
        return {
            'total_steps': total,
            'completed': completed,
            'failed': failed,
            'pending': total - completed - failed,
            'progress_percent': progress_percent,
            'steps': {
                name: {
                    'status': step.status,
                    'duration': step.duration,
                    'output': step.output,
                    'error': step.error,
                }
                for name, step in self.steps.items()
            }
        }
    
    def export_plan(self, output_file: Optional[str] = None) -> str:
        """Export render plan to JSON file."""
        output_file = output_file or os.path.join(
            self.output_dir, 
            f"render_plan_{datetime.now().isoformat()}.json"
        )
        
        plan_data = {
            'project_name': self.project_name,
            'created_at': datetime.now().isoformat(),
            'start_time': self.start_time,
            'end_time': self.end_time,
            'total_duration': (
                (self.end_time - self.start_time) 
                if self.start_time and self.end_time else 0
            ),
            'config': self.config,
            'progress': self.get_progress(),
            'steps': {
                name: asdict(step) 
                for name, step in self.steps.items()
            }
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(plan_data, f, indent=2, ensure_ascii=False)
        
        return output_file
    
    def get_summary(self) -> str:
        """Get a summary of the render plan."""
        progress = self.get_progress()
        
        summary = f"""
╔══════════════════════════════════════════╗
║  RENDER PLAN SUMMARY - {self.project_name}  ║
╚══════════════════════════════════════════╝

Progress: {progress['completed']}/{progress['total_steps']} steps completed ({progress['progress_percent']:.1f}%)
Status: {progress['failed']} failed, {progress['pending']} pending

Steps:
"""
        for name, step in self.steps.items():
            status_icon = {
                'completed': '✅',
                'failed': '❌',
                'running': '▶️ ',
                'pending': '⏳'
            }.get(step.status, '❓')
            
            summary += f"  {status_icon} {name:<25} ({step.duration:.2f}s)\n"
        
        if self.start_time and self.end_time:
            total_time = self.end_time - self.start_time
            summary += f"\nTotal Duration: {total_time:.2f} seconds\n"
        
        return summary