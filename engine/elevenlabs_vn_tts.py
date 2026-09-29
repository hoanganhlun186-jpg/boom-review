"""Adapter for the third-party 11labs.id.vn asynchronous TTS API.

Voice ids are namespaced with ``11labsvn:`` so they can never be confused
with official ElevenLabs ids or another provider used by the application.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from utils.helpers import FFmpegUtils


BASE_URL = "https://11labs.id.vn/api/external/v1"
VOICE_PREFIX = "11labsvn:"
DEFAULT_VOICE_ID = "n_hanoi_female_nguyetnga2_book_vc"
DEFAULT_VOICE_NAME = "Nguyệt Nga Podcast"
MAX_TEXT_CHARS = 10_000
_state_locks: dict[str, threading.Lock] = {}
_state_locks_guard = threading.Lock()


def _lock_for(identity: str) -> threading.Lock:
    with _state_locks_guard:
        return _state_locks.setdefault(identity, threading.Lock())


def get_api_key() -> str:
    from ui.config_manager import ConfigManager

    key = str(
        ConfigManager().get("elevenlabs_vn_api_key", "")
        or os.environ.get("ELEVENLABS_VN_API_KEY", "")
    ).strip()
    if not key:
        raise ValueError("Chưa nhập API key 11LABS VN ở phần giọng đọc.")
    return key


def _message(response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("message") or "").strip()
    except Exception:
        pass
    return ""


def _check_response(response, action: str = "gọi API") -> None:
    if 200 <= int(response.status_code) < 300:
        return
    messages = {
        400: "Dữ liệu gửi lên 11LABS VN không hợp lệ.",
        401: "API key 11LABS VN không hợp lệ hoặc đã hết hạn.",
        403: "Tài khoản 11LABS VN đã hết quota hoặc không có quyền truy cập.",
        404: "Job 11LABS VN không tồn tại.",
        429: "11LABS VN đang giới hạn số yêu cầu. Hãy giảm số luồng và thử lại.",
        503: "11LABS VN đang bận; hãy thử lại sau.",
    }
    detail = _message(response)
    base = messages.get(int(response.status_code), f"11LABS VN trả HTTP {response.status_code} khi {action}.")
    # Avoid reflecting arbitrary server bodies; include only a short public error.
    if detail and len(detail) <= 160 and "PL_" not in detail:
        base = f"{base} ({detail})"
    raise RuntimeError(base)


def _voice_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("voices", "items", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _voice_items(value)
            if nested:
                return nested
    return []


def fetch_voices(api_key: str, provider: str = "all") -> list[dict]:
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("Chưa nhập API key 11LABS VN.")
    try:
        response = requests.get(
            BASE_URL + "/voices.php",
            headers={"x-api-key": key},
            params={"provider": provider},
            timeout=(15, 45),
        )
        _check_response(response, "tải danh sách giọng")
        voices = _voice_items(response.json())
    except requests.RequestException:
        raise RuntimeError("Không kết nối được API 11LABS VN. Kiểm tra mạng và thử lại.") from None
    result = []
    seen = set()
    for item in voices:
        voice_id = str(item.get("voice_id") or item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id) or voice_id in seen:
            continue
        seen.add(voice_id)
        result.append({
            **item,
            "id": voice_id,
            "name": str(item.get("name") or item.get("voice_name") or voice_id).strip(),
            "provider": str(item.get("provider") or item.get("category") or "").strip(),
        })
    return result


def voice_options(voices: list[dict] | None = None) -> list[str]:
    items = voices or [{"id": DEFAULT_VOICE_ID, "name": DEFAULT_VOICE_NAME, "provider": "vbee"}]
    labels = []
    for item in items:
        voice_id = str(item.get("id") or item.get("voice_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
            continue
        name = str(item.get("name") or item.get("voice_name") or voice_id).strip()
        provider = str(item.get("provider") or item.get("category") or "").strip()
        suffix = f" ({provider})" if provider else ""
        labels.append(f"11LABS VN · {name}{suffix} - {VOICE_PREFIX}{voice_id}")
    return labels


def _speed_from_rate(rate) -> float:
    try:
        value = 1.0 + float(str(rate or "+0%").replace("%", "").strip()) / 100.0
    except (TypeError, ValueError):
        value = 1.0
    return max(0.7, min(1.7, value))


def _safe_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _download_audio(url: str, target: Path) -> None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("11LABS VN trả URL âm thanh không hợp lệ.")
    try:
        response = requests.get(url, timeout=(15, 180))
        _check_response(response, "tải audio")
    except requests.RequestException:
        raise RuntimeError("Không tải được audio 11LABS VN; có thể tiếp tục lại mà không tạo job mới.") from None
    if not response.content:
        raise RuntimeError("11LABS VN trả file âm thanh trống.")
    staged = target.with_suffix(target.suffix + ".tmp")
    staged.write_bytes(response.content)
    os.replace(staged, target)


def _wait_for_job(key: str, job_id: str, timeout: float, poll_interval: float) -> str:
    deadline = time.monotonic() + max(30.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            response = requests.get(
                BASE_URL + "/status.php",
                headers={"x-api-key": key},
                params={"job_id": job_id},
                timeout=(15, 45),
            )
            _check_response(response, "kiểm tra trạng thái")
            payload = response.json()
        except requests.RequestException:
            time.sleep(max(1.0, poll_interval))
            continue
        status = str(payload.get("status") or "").strip().lower()
        if status == "completed":
            url = payload.get("download_url") or payload.get("audio_url")
            if not url and isinstance(payload.get("job"), dict):
                url = payload["job"].get("download_url") or payload["job"].get("audio_url")
            if not url:
                raise RuntimeError("Job 11LABS VN hoàn thành nhưng thiếu URL âm thanh.")
            return str(url)
        if status == "failed" or "failed" in status:
            raise RuntimeError("Job TTS 11LABS VN xử lý thất bại; không tự gửi lại để tránh trừ quota hai lần.")
        time.sleep(max(1.0, poll_interval))
    raise TimeoutError("Job 11LABS VN chưa xong trong thời gian chờ; trạng thái đã được lưu để tiếp tục sau.")


def _concat_audio(parts: list[Path], target: Path) -> None:
    if len(parts) == 1 and target.suffix.lower() == ".mp3":
        shutil.copy2(parts[0], target)
        return
    ffmpeg = FFmpegUtils.ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="11labsvn_concat_", dir=str(target.parent)) as folder:
        manifest = Path(folder) / "parts.txt"
        manifest.write_text(
            "".join("file '" + part.as_posix().replace("'", "'\\''") + "'\n" for part in parts),
            encoding="utf-8",
        )
        staged = Path(folder) / ("result" + target.suffix.lower())
        codec = ["-c:a", "pcm_s16le"] if target.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "192k"]
        result = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(manifest), "-vn", "-ar", "44100", "-ac", "1", *codec, str(staged)],
            **FFmpegUtils.subprocess_kwargs(capture_output=True, timeout=240),
        )
        if result.returncode or not staged.exists() or staged.stat().st_size < 100:
            raise RuntimeError("Không giải mã/ghép được audio 11LABS VN.")
        os.replace(staged, target)


def synthesize_11labs_vn(
    text,
    output_path,
    voice,
    rate="+0%",
    api_key=None,
    progress_callback=None,
    timeout=600,
    poll_interval=3,
):
    """Create an MP3/WAV atomically and resume polling/downloading existing jobs."""
    key = str(api_key).strip() if api_key is not None else get_api_key()
    if not key:
        raise ValueError("Chưa nhập API key 11LABS VN.")
    voice_id = str(voice or "").removeprefix(VOICE_PREFIX).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        raise ValueError("Voice ID 11LABS VN không hợp lệ.")
    clean_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean_text:
        raise ValueError("Nội dung tạo voice 11LABS VN đang trống.")
    chunks = [clean_text[i:i + MAX_TEXT_CHARS] for i in range(0, len(clean_text), MAX_TEXT_CHARS)]
    target = Path(output_path).resolve()
    if target.suffix.lower() not in (".mp3", ".wav"):
        raise ValueError("11LABS VN hỗ trợ đầu ra .mp3 hoặc .wav.")
    target.parent.mkdir(parents=True, exist_ok=True)
    speed = _speed_from_rate(rate)
    cache_root = target.parent / ".11labsvn_resume"
    cache_root.mkdir(parents=True, exist_ok=True)
    part_paths = []

    for index, chunk in enumerate(chunks):
        identity = hashlib.sha256(
            json.dumps([chunk, voice_id, speed, hashlib.sha256(key.encode()).hexdigest()[:16]], ensure_ascii=False).encode()
        ).hexdigest()
        cache = cache_root / identity
        cache.mkdir(parents=True, exist_ok=True)
        state_path = cache / "request.json"
        part = cache / "audio.part"
        with _lock_for(identity):
            state = _safe_json(state_path)
            if not part.exists() or part.stat().st_size < 100:
                job_id = str(state.get("job_id") or "").strip()
                audio_url = str(state.get("download_url") or "").strip()
                if not job_id and state.get("submitted"):
                    raise RuntimeError(
                        "Lần gửi 11LABS VN trước chưa nhận được job_id. Đã chặn gửi lại để tránh trừ quota hai lần."
                    )
                if not job_id:
                    _write_json_atomic(state_path, {"submitted": True})
                    try:
                        response = requests.post(
                            BASE_URL + "/tts.php",
                            headers={"x-api-key": key, "Content-Type": "application/json"},
                            json={
                                "text": chunk,
                                "voice_id": voice_id,
                                "voice_settings": {
                                    "speed": speed,
                                    "language": "Vietnamese",
                                    "normalization": True,
                                    "expressive_optimize": False,
                                },
                            },
                            timeout=(15, 90),
                        )
                        if response.status_code in (400, 401, 403, 404, 429, 503):
                            state_path.unlink(missing_ok=True)
                        _check_response(response, "tạo job")
                        payload = response.json()
                        job_id = str(payload.get("job_id") or "").strip()
                        if not job_id:
                            raise RuntimeError("11LABS VN không trả job_id.")
                        _write_json_atomic(state_path, {"submitted": True, "job_id": job_id})
                    except requests.RequestException:
                        raise RuntimeError(
                            "Mất kết nối khi gửi 11LABS VN. Đã chặn tự gửi lại để tránh trừ quota hai lần."
                        ) from None
                if not audio_url:
                    audio_url = _wait_for_job(key, job_id, timeout, poll_interval)
                    _write_json_atomic(
                        state_path,
                        {"submitted": True, "job_id": job_id, "download_url": audio_url},
                    )
                _download_audio(audio_url, part)
            part_paths.append(part)
        if progress_callback:
            progress_callback(int((index + 1) * 100 / len(chunks)), index + 1, len(chunks))

    with tempfile.TemporaryDirectory(prefix="11labsvn_", dir=str(target.parent)) as folder:
        staged = Path(folder) / ("result" + target.suffix.lower())
        _concat_audio([Path(path) for path in part_paths], staged)
        if not staged.exists() or staged.stat().st_size < 100:
            raise RuntimeError("11LABS VN không tạo được audio hợp lệ.")
        os.replace(staged, target)
    return str(target)
