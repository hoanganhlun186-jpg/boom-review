"""Read-only job discovery: never create a job as a side effect of resume."""
import json
import os
from pathlib import Path

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None

def find_resume_job(roots, video):
    identity = os.path.normcase(os.path.abspath(video))
    matches = []
    visited = set()
    for base in roots:
        if not base or not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            if len(Path(root).relative_to(base).parts) >= 3:
                dirs[:] = []
            if '.autorecap_job.json' not in files or root in visited:
                continue
            visited.add(root)
            marker = read_json(Path(root)/'.autorecap_job.json') or {}
            source = marker.get('source_video') or marker.get('video_signature',{}).get('path')
            if not source or os.path.normcase(os.path.abspath(source)) != identity:
                continue
            state = read_json(Path(root)/'pipeline_state.json')
            if not isinstance(state,dict):
                continue
            if 'RENDER_FINAL' in state.get('completed_steps',[]):
                continue
            stamp = float(state.get('updated_at') or os.path.getmtime(Path(root)/'pipeline_state.json'))
            matches.append((bool(state.get('failed_step')), stamp, root))
    return max(matches)[2] if matches else ''
