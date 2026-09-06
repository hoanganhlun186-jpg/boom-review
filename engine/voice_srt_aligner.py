"""Optional Whisper alignment for generated voice subtitles."""
from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict, List


class VoiceSrtAligner:
    VERSION = "voice_srt_whisper_align_v1"

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    @staticmethod
    def seconds_to_timecode(seconds: float) -> str:
        seconds = max(0.0, float(seconds or 0.0))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        whole = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            whole += 1
            millis -= 1000
        return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"

    @classmethod
    def write_srt(cls, segments: List[Dict[str, Any]], output_srt_path: str) -> None:
        lines: List[str] = []
        for index, segment in enumerate(segments, 1):
            text = " ".join(str(segment.get("text") or "").split())
            if not text:
                continue
            lines.append(str(index))
            lines.append(f"{cls.seconds_to_timecode(segment.get('start', 0.0))} --> {cls.seconds_to_timecode(segment.get('end', 0.0))}")
            lines.append(text)
            lines.append("")
        with open(output_srt_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines).strip() + "\n")

    @classmethod
    def align_audio(
        cls,
        audio_path: str,
        output_srt_path: str,
        language: str = "vi",
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> Dict[str, Any]:
        if not audio_path or not os.path.exists(audio_path):
            return {"version": cls.VERSION, "ok": False, "reason": "audio_missing", "audio_path": audio_path}
        if not cls.available():
            return {"version": cls.VERSION, "ok": False, "reason": "missing_faster_whisper"}

        try:
            from faster_whisper import WhisperModel

            model_name = model_name or os.environ.get("AUTORECAP_WHISPER_ALIGN_MODEL", "base")
            device = device or os.environ.get("AUTORECAP_WHISPER_ALIGN_DEVICE", "cpu")
            compute_type = compute_type or os.environ.get("AUTORECAP_WHISPER_ALIGN_COMPUTE", "int8")
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            raw_segments, info = model.transcribe(
                audio_path,
                language=language or "vi",
                vad_filter=True,
                word_timestamps=False,
            )
            segments: List[Dict[str, Any]] = []
            for segment in raw_segments:
                text = " ".join(str(getattr(segment, "text", "") or "").split())
                if text:
                    segments.append(
                        {
                            "start": float(getattr(segment, "start", 0.0) or 0.0),
                            "end": float(getattr(segment, "end", 0.0) or 0.0),
                            "text": text,
                        }
                    )
            if not segments:
                return {"version": cls.VERSION, "ok": False, "reason": "no_segments"}
            cls.write_srt(segments, output_srt_path)
            return {
                "version": cls.VERSION,
                "ok": True,
                "segment_count": len(segments),
                "output_srt_path": output_srt_path,
                "language": getattr(info, "language", language),
                "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
                "model_name": model_name,
                "device": device,
            }
        except Exception as exc:
            return {"version": cls.VERSION, "ok": False, "reason": "align_error", "error": str(exc)[:500]}
