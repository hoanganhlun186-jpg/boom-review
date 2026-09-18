"""Create a resumable Vietnamese voice gallery; credentials stay in memory."""
import argparse
import getpass
import html
import json
import re
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.pekka_tts import fetch_voices, voice_language_matches, synthesize_pekka
from core.voice_segments import _probe_audio_duration


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/pekka_85_giong")
    args = parser.parse_args()
    key = getpass.getpass("Pekka key (hidden): ").strip().replace("\\_", "_")
    phrase = "đây là giọng đọc thử nghiệm"
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    voices = [v for v in fetch_voices(key) if voice_language_matches(v, "vi")]
    unique = {str(v["id"]): v for v in voices if re.fullmatch(r"[A-Za-z0-9_-]+", str(v.get("id", "")))}
    print(f"VIETNAMESE_VOICES {len(unique)}", flush=True)
    results = []
    for index, (vid, voice) in enumerate(unique.items(), 1):
        name = str(voice.get("name") or vid)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:65] or "voice"
        filename = f"{index:02d}_{safe_name}_{vid}.mp3"
        output = folder / filename
        entry = {"name": name, "id": vid, "file": filename, "text": phrase}
        try:
            duration = _probe_audio_duration(str(output)) if output.exists() else 0
            if duration <= 0:
                synthesize_pekka(phrase, output, "pekka:" + vid, api_key=key)
                duration = _probe_audio_duration(str(output))
            if duration <= 0:
                raise RuntimeError("Không đo được thời lượng âm thanh")
            entry.update(status="ok", seconds=round(duration, 3))
            print(f"[{index}/{len(unique)}] OK {name}: {duration:.2f}s", flush=True)
        except Exception as exc:
            error = str(exc).replace(key, "[REDACTED]")
            entry.update(status="failed", error=error)
            print(f"[{index}/{len(unique)}] FAILED {name}: {error}", flush=True)
        results.append(entry)
        (folder / "manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(1.5)
    cards = []
    for entry in results:
        title = html.escape(entry["name"])
        body = (f'<audio controls preload="none" src="{html.escape(entry["file"], quote=True)}"></audio>'
                if entry["status"] == "ok" else '<p>Chưa tạo được mẫu.</p>')
        cards.append(f'<article><h2>{title}</h2>{body}</article>')
    page = '''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nghe thử giọng Pekka</title><style>body{font:16px system-ui;background:#111827;color:#eef2ff;margin:32px auto;max-width:1100px;padding:0 16px}h1{font-size:28px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}article{background:#1f2937;border-radius:12px;padding:16px}h2{font-size:18px}audio{width:100%}input{padding:12px;width:90%;margin:12px 0 24px;border-radius:8px}</style>
<h1>Giọng Pekka tiếng Việt</h1><p>Câu mẫu: “đây là giọng đọc thử nghiệm” · Tốc độ 1×</p>
<input id="search" placeholder="Tìm tên giọng…"><main>''' + "".join(cards) + '''</main>
<script>document.querySelector('#search').oninput=e=>document.querySelectorAll('article').forEach(a=>a.hidden=!a.querySelector('h2').textContent.toLowerCase().includes(e.target.value.toLowerCase()));document.addEventListener('play',e=>{if(e.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true)</script></html>'''
    (folder / "nghe_thu.html").write_text(page, encoding="utf-8")
    archive = folder.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ["nghe_thu.html", "manifest.json"] + [r["file"] for r in results if r["status"] == "ok"]:
            bundle.write(folder / name, arcname=folder.name + "/" + name)
    print("COMPLETE", json.dumps({"ok": sum(r["status"] == "ok" for r in results), "total": len(results), "zip": str(archive)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
