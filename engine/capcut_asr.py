"""Background speech recognition through CapCut's subtitle service.

Uses the capcut_tts_api library for upload and STT — the same library used by
the CapCut widget in the sibling tool, which already works correctly.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

try:
    from capcut_tts_api import CapCutClient
    from capcut_tts_api.models import DeviceConfig
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

import requests as std_requests

from utils.helpers import FFmpegUtils


ProgressCallback = Callable[[str, int], None]

# Device config file persisted alongside this module so each install keeps its
# own fingerprint (= separate quota).
_DEVICE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capcut_device.json")


def _load_device() -> DeviceConfig:
    """Load or create a persistent device fingerprint for this installation."""
    if not _HAS_SDK:
        return None
    if os.path.exists(_DEVICE_FILE):
        try:
            with open(_DEVICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("device_id") and data.get("iid"):
                return DeviceConfig.from_dict(data)
        except Exception:
            pass
    # Generate fresh fingerprint
    import uuid as _uuid
    did = str(_uuid.uuid4().int)[:19]
    iid = str(_uuid.uuid4().int)[:19]
    tdid = str(_uuid.uuid4().int)[:16]
    device_data = {"device_id": did, "iid": iid, "tdid": tdid}
    try:
        with open(_DEVICE_FILE, "w", encoding="utf-8") as f:
            json.dump(device_data, f, indent=2)
    except Exception:
        pass
    return DeviceConfig.from_dict(device_data)


@dataclass
class CapCutSubtitle:
    start_ms: int
    end_ms: int
    text: str


class CapCutDirectASR:
    """Create an SRT without opening CapCut or running local Whisper."""

    def __init__(
        self,
        progress_callback: Optional[ProgressCallback] = None,
        timeout: float = 45.0,
        chunk_seconds: float = 120.0,
        poll_interval: float = 2.0,
        poll_timeout: float = 180.0,
        language: str = "vi-VN",
        session=None,
        max_workers: int = 4,
    ) -> None:
        if not _HAS_SDK:
            raise ImportError(
                "capcut_tts_api is required. Run: pip install capcut-tts-api"
            )
        self.progress_callback = progress_callback
        self.chunk_seconds = max(30.0, float(chunk_seconds))
        self.poll_interval = max(0.5, float(poll_interval))
        self.poll_timeout = max(30.0, float(poll_timeout))
        self.language = str(language or "vi-VN").strip() or "vi-VN"

        # Build HTTP session (prefer curl_cffi for Chromium TLS fingerprint)
        if session is not None:
            http_session = session
        elif curl_requests is not None:
            http_session = curl_requests.Session(impersonate="chrome124")
        else:
            http_session = std_requests.Session()

        self.max_workers = max(1, int(max_workers))
        device = _load_device()
        # Mỗi worker cần session riêng để tránh race condition
        self._device = device
        self._make_session = lambda: (
            curl_requests.Session(impersonate="chrome124") if curl_requests is not None
            else std_requests.Session()
        )
        self._client = CapCutClient(device=device, session=http_session)

    def _progress(self, message: str, percent: int) -> None:
        if self.progress_callback:
            self.progress_callback(message, max(0, min(100, int(percent))))

    def _recognize_audio(self, audio_path: str, duration_ms: int) -> List[CapCutSubtitle]:
        """Upload audio chunk and run STT, returning subtitle list."""
        # Upload
        upload_result = self._client.upload_audio(audio_path)

        # Submit STT task
        stt_response = self._client.create_stt_task(
            audio_vid=upload_result.vid,
            audio_md5=upload_result.md5,
            duration_ms=duration_ms or upload_result.duration_ms or 10000,
            language=self.language,
            translation_language="vi-VN",
            use_translation=False,
        )

        tasks = (stt_response.get("data") or {}).get("tasks") or []
        if not tasks:
            raise RuntimeError("CapCut không tạo được tác vụ nhận dạng")
        task_id = tasks[0]["id"]
        token = tasks[0]["token"]

        # Poll for result
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            query_res = self._client.query_stt_task(task_id, token)
            query_tasks = (query_res.get("data") or {}).get("tasks") or []
            if query_tasks:
                status = str(query_tasks[0].get("status") or "").lower()
                if status == "success":
                    subtitle_result = self._client.extract_subtitles(query_res)
                    return [
                        CapCutSubtitle(
                            start_ms=u.start_time,
                            end_ms=u.end_time,
                            text=u.text,
                        )
                        for u in subtitle_result.utterances
                        if u.text and u.text.strip()
                    ]
                if status == "failed":
                    raise RuntimeError("CapCut báo tác vụ nhận dạng thất bại")
            time.sleep(self.poll_interval)
        raise TimeoutError("CapCut nhận dạng quá thời gian chờ")

    @staticmethod
    def _probe_duration(video_path: str) -> float:
        command = [
            FFmpegUtils.ffprobe_executable(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            **FFmpegUtils.subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Không đọc được thời lượng video")
        return max(0.1, float(result.stdout.strip()))

    @staticmethod
    def _extract_chunk(video_path: str, output_path: str, start: float, duration: float) -> None:
        command = [
            FFmpegUtils.ffmpeg_executable(),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", video_path,
            "-map", "0:a:0",           # chỉ lấy audio track đầu tiên, bỏ qua track hỏng
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
            output_path,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(120, int(duration * 2)),
            **FFmpegUtils.subprocess_kwargs(),
        )
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(result.stderr.strip() or "Không trích được audio cho CapCut")

    @staticmethod
    def _srt_time(milliseconds: int) -> str:
        milliseconds = max(0, int(milliseconds))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @classmethod
    def write_srt(cls, subtitles: Iterable[CapCutSubtitle], output_path: str) -> int:
        clean = sorted(subtitles, key=lambda item: (item.start_ms, item.end_ms))
        lines: List[str] = []
        for index, item in enumerate(clean, 1):
            lines.extend([
                str(index),
                f"{cls._srt_time(item.start_ms)} --> {cls._srt_time(item.end_ms)}",
                item.text.replace("\r", " ").replace("\n", " ").strip(),
                "",
            ])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        return len(clean)

    def _process_chunk(
        self, index: int, chunk_count: int, video_path: str, temp_dir: str, start: float, chunk_duration: float
    ) -> Tuple[int, List[CapCutSubtitle]]:
        """Trích + nhận dạng 1 chunk, trả về (index, subtitles). Mỗi call dùng client riêng."""
        n = index + 1
        audio_path = os.path.join(temp_dir, f"chunk_{n:03d}.mp3")

        self._progress(f"[{n}/{chunk_count}] Trích audio...", 0)
        self._extract_chunk(video_path, audio_path, start, chunk_duration)

        client = CapCutClient(device=self._device, session=self._make_session())
        duration_ms = int(round(chunk_duration * 1000))

        self._progress(f"[{n}/{chunk_count}] Upload audio lên CapCut...", 0)
        upload_result = client.upload_audio(audio_path)
        self._progress(f"[{n}/{chunk_count}] Upload xong → vid={upload_result.vid[:12]}...", 0)

        self._progress(f"[{n}/{chunk_count}] Gửi yêu cầu nhận dạng...", 0)
        stt_response = client.create_stt_task(
            audio_vid=upload_result.vid,
            audio_md5=upload_result.md5,
            duration_ms=duration_ms or upload_result.duration_ms or 10000,
            language=self.language,
            translation_language="vi-VN",
            use_translation=False,
        )
        tasks = (stt_response.get("data") or {}).get("tasks") or []
        if not tasks:
            self._progress(f"[{n}/{chunk_count}] Không tạo được task!", 0)
            return index, []
        task_id = tasks[0]["id"]
        token = tasks[0]["token"]
        self._progress(f"[{n}/{chunk_count}] Đang chờ kết quả (task={task_id[:8]}...)...", 0)

        deadline = time.monotonic() + self.poll_timeout
        poll_count = 0
        while time.monotonic() < deadline:
            query_res = client.query_stt_task(task_id, token)
            query_tasks = (query_res.get("data") or {}).get("tasks") or []
            if query_tasks:
                status = str(query_tasks[0].get("status") or "").lower()
                poll_count += 1
                self._progress(f"[{n}/{chunk_count}] Poll #{poll_count}: {status}", 0)
                if status in ("success", "succeed"):
                    subtitle_result = client.extract_subtitles(query_res)
                    subs = [
                        CapCutSubtitle(u.start_time, u.end_time, u.text)
                        for u in subtitle_result.utterances
                        if u.text and u.text.strip()
                    ]
                    self._progress(f"[{n}/{chunk_count}] ✓ {len(subs)} câu", 0)
                    return index, subs
                if status == "failed":
                    self._progress(f"[{n}/{chunk_count}] ✗ Task thất bại", 0)
                    return index, []
            time.sleep(self.poll_interval)
        self._progress(f"[{n}/{chunk_count}] ✗ Timeout", 0)
        return index, []

    def transcribe(self, video_path: str, output_srt: str) -> int:
        if not os.path.isfile(video_path):
            raise FileNotFoundError(video_path)
        duration = self._probe_duration(video_path)
        chunk_count = max(1, int((duration + self.chunk_seconds - 0.001) // self.chunk_seconds))
        self._progress(f"CapCut ASR: {chunk_count} đoạn, {self.max_workers} luồng song song", 2)

        chunks = [
            (i, i * self.chunk_seconds, min(self.chunk_seconds, duration - i * self.chunk_seconds))
            for i in range(chunk_count)
        ]

        results: dict[int, List[CapCutSubtitle]] = {}
        done_count = 0

        with tempfile.TemporaryDirectory(prefix="autorecap_capcut_asr_") as temp_dir:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {
                    pool.submit(self._process_chunk, idx, chunk_count, video_path, temp_dir, start, dur): idx
                    for idx, start, dur in chunks
                }
                for future in as_completed(futures):
                    idx, subs = future.result()
                    results[idx] = subs
                    done_count += 1
                    self._progress(
                        f"CapCut ASR: {done_count}/{chunk_count} đoạn xong",
                        int(done_count * 95 / chunk_count),
                    )

        all_subtitles: List[CapCutSubtitle] = []
        for idx, start, _ in sorted(chunks, key=lambda x: x[0]):
            offset = int(round(start * 1000))
            for item in results.get(idx, []):
                all_subtitles.append(
                    CapCutSubtitle(item.start_ms + offset, item.end_ms + offset, item.text)
                )

        count = self.write_srt(all_subtitles, output_srt)
        if count == 0:
            raise RuntimeError("CapCut không nhận diện được lời thoại")
        self._progress(f"CapCut ASR hoàn tất: {count} câu", 100)
        return count
