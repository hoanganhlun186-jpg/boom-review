"""Copy approved preview audio into assets included in customer builds."""
import json
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "outputs" / "pekka_85_giong"
    destination = root / "assets" / "pekka_samples"
    entries = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert len(entries) == 85 and len({entry["id"] for entry in entries}) == 85
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    for entry in entries:
        assert entry["status"] == "ok"
        audio = source / entry["file"]
        assert audio.is_file() and audio.stat().st_size > 100
        filename = entry["id"] + ".mp3"
        shutil.copy2(audio, destination / filename)
        manifest.append({"id": entry["id"], "name": entry["name"], "language": "vi",
                         "file": filename, "text": entry["text"], "seconds": entry["seconds"]})
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bundled {len(manifest)} preview voices; {sum(p.stat().st_size for p in destination.glob('*.mp3'))} bytes.")


if __name__ == "__main__":
    main()
