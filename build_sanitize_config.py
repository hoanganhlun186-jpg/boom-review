from __future__ import annotations

import json
import sys
from pathlib import Path


SENSITIVE_FIELDS = {
    "gemini_api_key",
    "gemini_keys_file",
    "openrouter_api_key",
}

USER_PATH_FIELDS = {
    "video_source",
    "bgm_path",
    "srt_path",
    "source_srt_path",
    "capcut_srt_output_path",
    "cut_output_dir",
    "output_dir",
}

TEXT_FIELDS_TO_CLEAR = {
    "movie_description",
    "script",
    "review_script",
}


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def sanitize_config(config: dict) -> dict:
    clean = dict(config)

    for key in SENSITIVE_FIELDS | USER_PATH_FIELDS | TEXT_FIELDS_TO_CLEAR:
        clean[key] = ""

    # Keep product defaults that are safe for a fresh user install.
    clean.setdefault("review_style", "professional_youtube_movie_recap")
    clean.setdefault("language", "Tiếng Việt")
    clean.setdefault("voice", "")

    return clean


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: py -3.11 build_sanitize_config.py <dist_config_path>")
        return 1

    target = Path(sys.argv[1]).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    source = Path("config.json").resolve()
    config = _load_config(source)
    clean = sanitize_config(config)
    target.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] Da tao config release sach: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
