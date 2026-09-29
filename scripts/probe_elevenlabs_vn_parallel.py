"""Small opt-in live probe for 11labs.id.vn concurrency (uses paid quota)."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.elevenlabs_vn_tts import DEFAULT_VOICE_ID, fetch_voices, synthesize_11labs_vn


def main():
    key = getpass("API key 11LABS VN (không hiển thị): ").strip()
    if not key:
        raise SystemExit("Thiếu API key.")
    voices = fetch_voices(key)
    if not any(item.get("id") == DEFAULT_VOICE_ID for item in voices):
        raise SystemExit("Không tìm thấy giọng Nguyệt Nga Podcast trong tài khoản.")
    texts = ["Đây là đoạn thử một.", "Đây là đoạn thử hai.", "Đây là đoạn thử ba."]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="11labsvn_live_probe_") as folder:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(
                    synthesize_11labs_vn,
                    text,
                    Path(folder) / f"probe_{index}.mp3",
                    "11labsvn:" + DEFAULT_VOICE_ID,
                    "+0%",
                    key,
                ): index
                for index, text in enumerate(texts, 1)
            }
            results = []
            for future in as_completed(futures):
                index = futures[future]
                path = Path(future.result())
                results.append((index, path.stat().st_size))
        elapsed = time.monotonic() - started
    print(f"OK: {len(results)}/3 job song song, {elapsed:.1f}s")
    for index, size in sorted(results):
        print(f"  job {index}: audio hop le ({size} bytes)")


if __name__ == "__main__":
    main()
