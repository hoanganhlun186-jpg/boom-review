"""Pekka voice adapter (API contract from render_dub_feature.py)."""
import os
import hashlib
import json
import time
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

BASE_URL = "https://voice.getpekka.com"
DEFAULT_VOICES = {
    "vi": [("Thư Review", "orBfJ4Q68FyVbckjJgDvkj"),
           ("Phật Pháp", "8u97ewbLyV5dwePspwJY1w"),
           ("Ngọc Huyền", "mhsL3CPLxmLYdSTKp3GANz"),
           ("Minh Anh", "cZgBA3YXc4tD8QiLJDvr4z"),
           ("Quang Anh", "24oEtXGic7NhDjXzmDbDvt"),
           ("Adam 3", "5r2MVjMfzwsSDzTpaLjbY9"),
           ("Chi Chi", "nqak8C85bsAG5mihyunRkj"),
           ("Sarah", "jQhKABCZ2B7L4zncWcNb4Q"),
           ("Quỳnh Giao", "97zRSQPtS6Fg3KEKekxssu")],
    "en": [("Jessica", "7idd8r5DBSfrZ4zsvbG25J"),
           ("Theo", "5ZsmEgM69V3DNJy6V1WP84"),
           ("Mark", "qFeSMpoHP3ZhoDwXbe1354"),
           ("Alex", "hUJaV4ijMC3oLYQEHygPJt")],
}


def voice_options(language="vi", voices=None):
    pairs = DEFAULT_VOICES.get(language, []) if voices is None else voices
    return [f"Pekka · {name} - pekka:{vid}" for name, vid in pairs]


def get_api_key():
    from ui.config_manager import ConfigManager
    key = str(ConfigManager().get("pekka_api_key", "") or
              os.environ.get("PEKKA_API_KEY", "")).strip()
    if not key:
        raise ValueError("Chưa nhập API key Pekka ở phần giọng đọc.")
    return key


def _check_response(response):
    if response.status_code == 200:
        return
    messages = {401: "API key Pekka không hợp lệ.",
                403: "API key Pekka không có quyền truy cập.",
                402: "Tài khoản Pekka không đủ credit.",
                429: "Pekka đang giới hạn yêu cầu. Vui lòng thử lại sau."}
    # Never include response bodies: they can contain credentials or signed URLs.
    raise RuntimeError(messages.get(response.status_code,
                       f"Pekka trả lỗi HTTP {response.status_code}."))


def fetch_voices(api_key):
    if not api_key.strip():
        raise ValueError("Chưa nhập API key Pekka.")
    items = []
    try:
        for page in range(1, 51):
            response = requests.get(BASE_URL + "/api/v1/voices",
                                    headers={"Authorization": f"Bearer {api_key.strip()}"},
                                    params={"page": page, "limit": 50}, timeout=30)
            _check_response(response)
            data = response.json()
            items.extend(data.get("items", []))
            if not data.get("hasNext"):
                return items
    except requests.RequestException:
        raise RuntimeError("Không kết nối được API Pekka. Kiểm tra mạng và thử lại.") from None
    raise RuntimeError("Danh sách Pekka vượt 50 trang; chưa tải đủ giọng.")


def voice_language_matches(voice, language):
    aliases = {"vi": {"vi", "vie", "vn", "vietnamese", "tiếng việt"},
               "en": {"en", "eng", "english", "tiếng anh"}}
    for field in ("tags", "languages", "language", "languageCode", "locale"):
        values = voice.get(field) or []
        if not isinstance(values, (list, tuple)):
            values = [values]
        for value in values:
            if isinstance(value, dict):
                value = value.get("code") or value.get("name") or ""
            value = re.sub(r"^[^\w]+", "", str(value).lower().replace("_", "-")).strip()
            if value in aliases.get(language, {language}) or re.match(rf"^{language}-[a-z]{{2}}(?:-|$)", value):
                return True
    return str(voice.get("id", "")).lower().startswith(language + "-")


def split_text(text, limit=1000):
    remaining = re.sub(r"\s+", " ", text).strip()
    while remaining:
        end = min(len(remaining), limit)
        if len(remaining) > limit:
            boundary = remaining.rfind(" ", 0, limit + 1)
            if boundary > 0:
                end = boundary
        yield remaining[:end]
        remaining = remaining[end:].lstrip()


def synthesize_pekka(text, output_path, voice, rate="+0%", api_key=None,
                     progress_callback=None):
    """Create validated MP3/WAV atomically; never silently change provider."""
    from utils.helpers import FFmpegUtils
    key = api_key.strip() if api_key is not None else get_api_key()
    if not key:
        raise ValueError("Chưa nhập API key Pekka.")
    voice_id = str(voice).removeprefix("pekka:").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        raise ValueError("Voice ID Pekka không hợp lệ.")
    chunks = list(split_text(text))
    if not chunks:
        raise ValueError("Nội dung tạo voice Pekka đang trống.")
    speed = max(0.5, min(2.0, 1.0 + float(str(rate or 0).replace("%", "")) / 100))
    target = Path(output_path).resolve()
    if target.suffix.lower() not in (".wav", ".mp3"):
        raise ValueError("Pekka hỗ trợ file đầu ra .mp3 hoặc .wav.")
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = FFmpegUtils.ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="pekka_", dir=str(target.parent)) as folder:
        files = []
        for index, chunk in enumerate(chunks):
            identity = hashlib.sha256(json.dumps([chunk, voice_id, speed], ensure_ascii=False).encode()).hexdigest()
            cache = target.parent / '.pekka_resume' / identity
            cache.mkdir(parents=True, exist_ok=True)
            state_path = cache / 'request.json'
            part = cache / 'audio.part'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            if part.exists() and part.stat().st_size:
                files.append(part)
                continue
            url = state.get('url')
            if not url and state.get('submitted'):
                raise RuntimeError('Yêu cầu Pekka trước chưa xác định kết quả. Đã dừng gửi lại để tránh trừ credit lần nữa; kiểm tra lịch sử Pekka.')
            try:
                if not url:
                    state_path.write_text(json.dumps({'submitted': True}), encoding='utf-8')
                    response = requests.post(BASE_URL + "/api/v1/tts/sync",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"text": chunk, "voiceId": voice_id, "speed": speed}, timeout=(15, 180))
                    if response.status_code in (401, 402, 403, 429):
                        state_path.unlink(missing_ok=True)
                    _check_response(response)
                    url = response.json().get("url")
                if not isinstance(url, str) or not url.strip():
                    raise RuntimeError("Pekka không trả URL âm thanh.")
                url = urljoin(BASE_URL + "/", url)
                if urlparse(url).scheme != "https":
                    raise RuntimeError("URL âm thanh Pekka không hợp lệ.")
                staged_state = cache / 'request.tmp'
                staged_state.write_text(json.dumps({'submitted': True, 'url': url}), encoding='utf-8')
                os.replace(staged_state, state_path)
                for attempt in range(3):
                    try:
                        audio = requests.get(url, timeout=(15, 120))
                        _check_response(audio)
                        if not audio.content:
                            raise RuntimeError("Pekka trả file âm thanh trống.")
                        break
                    except (requests.RequestException, RuntimeError):
                        if attempt == 2:
                            raise RuntimeError('Tải audio Pekka thất bại sau 3 lần. Đã lưu link để tiếp tục tải, không tạo lại voice.') from None
                        time.sleep(2 * (attempt + 1))
            except requests.RequestException:
                raise RuntimeError("Không tải được âm thanh Pekka. Kiểm tra mạng và thử lại.") from None
            staged_part = cache / 'audio.tmp'
            staged_part.write_bytes(audio.content)
            os.replace(staged_part, part)
            files.append(part)
            if progress_callback:
                progress_callback(int((index + 1) * 100 / len(chunks)), index + 1, len(chunks))
        manifest = Path(folder) / "parts.txt"
        manifest.write_text("".join("file '" + p.as_posix().replace("'", "'\\''") + "'\n" for p in files), encoding="utf-8")
        staged = Path(folder) / ("result" + target.suffix.lower())
        codec = ["-c:a", "pcm_s16le"] if target.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "192k"]
        result = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(manifest), "-vn", "-ar", "44100",
            "-ac", "1", *codec, str(staged)],
            **FFmpegUtils.subprocess_kwargs(capture_output=True, timeout=180))
        if result.returncode or not staged.exists() or staged.stat().st_size < 100:
            for part in files:
                part.unlink(missing_ok=True)
            raise RuntimeError("Không giải mã/ghép được âm thanh Pekka.")
        os.replace(staged, target)
    return str(target)
