"""Persistent pipeline state for AutoRecapPro runs."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


EMPTY_STATE: Dict[str, Any] = {
    "version": 1,
    "current_step": "",
    "failed_step": "",
    "last_error": "",
    "completed_steps": [],
    "step_outputs": {},
    "step_errors": {},
    "updated_at": 0.0,
}


class PipelineStateManager:
    """Small JSON-backed state file for resume/debug visibility."""

    FILE_NAME = "pipeline_state.json"

    def __init__(self, output_dir: str, enabled: Optional[bool] = None):
        self.output_dir = Path(output_dir)
        if enabled is None:
            value = str(os.environ.get("AUTORECAP_PIPELINE_STATE", "1") or "1").strip().lower()
            enabled = value not in {"0", "false", "no", "off"}
        self.enabled = bool(enabled)

    @property
    def path(self) -> Path:
        return self.output_dir / self.FILE_NAME

    @classmethod
    def load(cls, output_dir: str) -> Dict[str, Any]:
        return cls(output_dir)._load()

    def _load(self) -> Dict[str, Any]:
        state = dict(EMPTY_STATE)
        if not self.path.exists():
            return state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state.update(raw)
        except Exception:
            state["last_error"] = "pipeline_state.json corrupt; reset in memory"
        if not isinstance(state.get("completed_steps"), list):
            state["completed_steps"] = []
        if not isinstance(state.get("step_outputs"), dict):
            state["step_outputs"] = {}
        if not isinstance(state.get("step_errors"), dict):
            state["step_errors"] = {}
        return state

    def _write(self, state: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            state["updated_at"] = time.time()
            self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def mark_running(self, step: str) -> None:
        state = self._load()
        state["current_step"] = str(step or "")
        state["failed_step"] = ""
        state["last_error"] = ""
        self._write(state)

    def mark_done(self, step: str, outputs: Any = None) -> None:
        state = self._load()
        step = str(step or "")
        completed = list(state.get("completed_steps") or [])
        if step and step not in completed:
            completed.append(step)
        state["completed_steps"] = completed
        state["current_step"] = ""
        state["failed_step"] = ""
        state["last_error"] = ""
        if outputs is not None:
            state.setdefault("step_outputs", {})[step] = outputs
        state.setdefault("step_errors", {}).pop(step, None)
        self._write(state)

    def mark_failed(self, step: str, error: Any) -> None:
        state = self._load()
        step = str(step or "")
        message = str(error or "")
        state["current_step"] = ""
        state["failed_step"] = step
        state["last_error"] = message
        state.setdefault("step_errors", {})[step] = message
        self._write(state)

    def is_done(self, step: str) -> bool:
        state = self._load()
        return str(step or "") in set(state.get("completed_steps") or [])

    def invalidate_steps(self, steps: Any) -> None:
        """Remove stale resume markers after a pipeline input changes."""
        invalid = {str(step or "") for step in (steps or []) if str(step or "")}
        if not invalid:
            return
        state = self._load()
        state["completed_steps"] = [
            step for step in (state.get("completed_steps") or []) if step not in invalid
        ]
        for key in ("step_outputs", "step_errors"):
            values = state.get(key) or {}
            if isinstance(values, dict):
                for step in invalid:
                    values.pop(step, None)
                state[key] = values
        if state.get("current_step") in invalid:
            state["current_step"] = ""
        if state.get("failed_step") in invalid:
            state["failed_step"] = ""
            state["last_error"] = ""
        self._write(state)
