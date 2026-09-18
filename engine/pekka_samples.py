"""Pre-generated voice previews shipped with the app; no API key required."""
import json
from pathlib import Path
import sys


def samples_directory():
    root = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) or "__compiled__" in globals()
            else Path(__file__).resolve().parent.parent)
    return root / "assets" / "pekka_samples"


def bundled_voices():
    try:
        data = json.loads((samples_directory() / "manifest.json").read_text(encoding="utf-8"))
        return [row for row in data if isinstance(row, dict) and row.get("id") and row.get("name")]
    except (OSError, ValueError, TypeError):
        return []


def bundled_sample_path(voice):
    if not str(voice).startswith("pekka:"):
        return None
    voice_id = str(voice).removeprefix("pekka:")
    for row in bundled_voices():
        if row["id"] != voice_id:
            continue
        filename = str(row.get("file") or "")
        if not filename or Path(filename).name != filename:
            return None
        path = samples_directory() / filename
        if path.is_file() and path.stat().st_size > 100:
            return str(path)
    return None
