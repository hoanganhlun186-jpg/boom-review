"""
AutoRecapPro V2 - Piper TTS Offline (Ngọc Huyền + 12 giọng Việt)
==================================================================
Models nằm trong: app/models/ngochuyen/*.onnx (tự chứa, không cần VideoEditor)
Fallback: D:\\VideoEditor\\data\\tts\\models\\ngochuyen\\

Cài: pip install piper-tts (đã có sẵn)
"""

import os
import re
import subprocess
import sys
import logging
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("piper_tts")

# ── App base path — đúng cả khi chạy .py, Nuitka exe, PyInstaller exe ────────
def _get_app_base() -> Path:
    if getattr(sys, 'frozen', False):
        # PyInstaller hoặc Nuitka onefile — sys.executable là file exe
        return Path(sys.executable).parent
    # Chạy .py trực tiếp
    return Path(__file__).resolve().parent.parent

_APP_DIR = _get_app_base()


def _find_model_dir() -> Path:
    candidates = [
        _APP_DIR / "models" / "ngochuyen",
        Path(r"D:\VideoEditor\data\tts\models\ngochuyen"),
    ]
    for p in candidates:
        if p.exists() and any(p.glob("*.onnx")):
            return p
    return _APP_DIR / "models" / "ngochuyen"


def _find_espeak_data() -> Optional[Path]:
    candidates = [
        _APP_DIR / "models" / "espeak-ng-data",
        Path(r"D:\VideoEditor\VideoEditor.dist\piper\espeak-ng-data"),
        Path(r"D:\VideoEditor\VideoEditor.dist\espeak-ng-data"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


DEFAULT_MODEL_DIR = _find_model_dir()
ESPEAK_DATA_PATH  = _find_espeak_data()

VOICE_NAMES: Dict[str, str] = {
    "ngoc_huyen":            "Ngọc Huyền",
    "ban_mai":               "Ban Mai",
    "mai_phuong":            "Mai Phương",
    "ngoc_ngan":             "Ngọc Ngân",
    "phuong_trang":          "Phương Trang",
    "thanh_phuong_viettel":  "Thanh Phương Viettel",
    "duy_oryx":              "Duy Oryx",
    "manh_dung":             "Mạnh Dũng",
    "minh_khang":            "Minh Khang",
    "minh_quang":            "Minh Quang",
    "tai_an":                "Tài An",
    "chieu_thanh":           "Chiêu Thanh",
    "thien_tam":             "Thiện Tâm",
}

PIPER_VOICE_PREFIX = "piper:"


def voice_label(stem: str) -> str:
    return f"{PIPER_VOICE_PREFIX}{stem}"


def is_piper_voice(voice_id: str) -> bool:
    v = str(voice_id or "").strip().lower()
    return (
        v.startswith(PIPER_VOICE_PREFIX)
        or v.startswith("piper_")
        or v in VOICE_NAMES
        or "ngoc huy" in v
        or "ngọc huy" in v
    )


def parse_piper_voice(voice_id: str) -> str:
    v = str(voice_id or "").strip()
    for prefix in (PIPER_VOICE_PREFIX, "piper_"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
    v_lower = v.lower()
    if v_lower in VOICE_NAMES:
        return v_lower
    normalized = re.sub(r"[^a-z0-9]+", "_", v_lower).strip("_")
    for stem, pretty in VOICE_NAMES.items():
        if stem == normalized:
            return stem
        pretty_norm = re.sub(r"[^a-z0-9]+", "_", pretty.lower()).strip("_")
        if pretty_norm == normalized:
            return stem
    return "ngoc_huyen"


def list_piper_voices(model_dir: Optional[str] = None) -> list:
    d = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    if not d.exists():
        return []
    return [voice_label(f.stem) for f in sorted(d.glob("*.onnx"))
            if (d / f"{f.stem}.onnx.json").exists()]


def is_piper_available(model_dir: Optional[str] = None) -> bool:
    try:
        import piper  # noqa
    except ImportError:
        return False
    return bool(list_piper_voices(model_dir))


def synthesize_piper(
    text: str,
    output_path: str,
    voice: str = "ngoc_huyen",
    speed: float = 1.0,
    model_dir: Optional[str] = None,
) -> str:
    """Tạo audio WAV bằng Piper TTS offline.

    Dùng Python API trực tiếp (không subprocess) để nhanh hơn.
    Fallback sang subprocess nếu API lỗi.
    """
    if not text.strip():
        raise ValueError("Piper TTS: text trống")

    stem = parse_piper_voice(voice)
    d = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    model_path  = d / f"{stem}.onnx"
    config_path = d / f"{stem}.onnx.json"

    if not model_path.exists():
        model_path  = d / "ngoc_huyen.onnx"
        config_path = d / "ngoc_huyen.onnx.json"
        logger.warning("Piper: '%s' không có → ngoc_huyen", stem)

    if not model_path.exists():
        raise RuntimeError(
            f"Không tìm thấy Piper model tại: {d}\n"
            "Cần folder models/ngochuyen/ trong app."
        )

    # Set espeak data
    if ESPEAK_DATA_PATH and ESPEAK_DATA_PATH.exists():
        os.environ.setdefault("ESPEAK_DATA_PATH", str(ESPEAK_DATA_PATH))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    length_scale = max(0.45, min(2.4, 1.0 / max(0.25, speed)))

    # ── Method 1: Python API (synthesize_wav) ────────────────────
    try:
        from piper.voice import PiperVoice
        voice_obj = PiperVoice.load(
            str(model_path), config_path=str(config_path), use_cuda=False
        )

        syn_cfg = None
        try:
            from piper import SynthesisConfig
            syn_cfg = SynthesisConfig(length_scale=length_scale)
        except Exception:
            pass

        with wave.open(output_path, "wb") as wf:
            # synthesize_wav ghi trực tiếp vào Wave_write object
            if syn_cfg is not None:
                voice_obj.synthesize_wav(text, wf, syn_config=syn_cfg)
            else:
                voice_obj.synthesize_wav(text, wf)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.debug("Piper API OK: %s (%d bytes)", stem, os.path.getsize(output_path))
            return output_path

    except Exception as api_e:
        logger.warning("Piper API thất bại (%s) → subprocess", api_e)

    # ── Method 2: Subprocess fallback ────────────────────────────
    cmd = [
        sys.executable, "-m", "piper",
        "-m", str(model_path),
        "-c", str(config_path),
        "-f", output_path,
        "--length-scale", f"{length_scale:.3f}",
    ]
    env = os.environ.copy()
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    proc = subprocess.run(
        cmd, input=text, text=True, encoding="utf-8",
        capture_output=True, env=env,
        startupinfo=startupinfo,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        timeout=180,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[:400]
        raise RuntimeError(f"Piper subprocess lỗi ({stem}): {msg}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Piper không tạo được file: {output_path}")

    return output_path


async def text_to_speech_piper(
    text: str,
    output_path: str,
    voice: str = "piper:ngoc_huyen",
    speed: float = 1.0,
    model_dir: Optional[str] = None,
) -> str:
    """Async wrapper — tương thích với edge-tts API."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: synthesize_piper(text, output_path, voice, speed, model_dir)
    )
