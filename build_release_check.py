import json
import re
import sys
from pathlib import Path


APP_SOURCE_DIRS = {"engine", "core", "ui", "utils"}
APP_SOURCE_FILES = {"main.py", "main_build_bootstrap.py", "config.py"}
TEXT_SUFFIXES = {".json", ".txt", ".ini", ".cfg", ".log", ".md", ".yml", ".yaml"}
KEY_PATTERNS = {
    "Gemini API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "OpenRouter API key": re.compile(r"sk-or(?:-v1)?-[0-9A-Za-z_-]{20,}"),
}
SENSITIVE_CONFIG_FIELDS = {
    "gemini_api_key",
    "openrouter_api_key",
    "gemini_keys_file",
}
PRIVATE_CONFIG_FIELDS = {
    "movie_description",
    "script",
    "review_script",
    "video_source",
    "bgm_path",
    "srt_path",
    "source_srt_path",
    "capcut_srt_output_path",
    "cut_output_dir",
    "output_dir",
}
PROMPT_LEAK_PATTERNS = {
    "review/prompt text": re.compile(
        r"KỊCH BẢN CHI TIẾT REVIEW PHIM|Voiceover\\s*\\(VO\\)|You are a Vietnamese movie recap",
        re.IGNORECASE,
    ),
}
BINARY_LEAK_PATTERNS = {
    "Gemini API key": re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    "OpenRouter API key": re.compile(rb"sk-or(?:-v1)?-[0-9A-Za-z_-]{20,}"),
    "review/prompt text": re.compile(rb"You are a Vietnamese movie recap|Voiceover\s*\(VO\)"),
    "Supabase setup text": re.compile(rb"supabase\.com|Tao tai khoan https://supabase\.com"),
}


def _is_app_source(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = rel.parts
    if path.name in APP_SOURCE_FILES:
        return True
    return len(parts) >= 2 and parts[0] in APP_SOURCE_DIRS and path.suffix == ".py"


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    root = root.resolve()
    if not root.exists():
        print(f"[ERROR] Khong tim thay thu muc output: {root}")
        return 1

    version_file = root / "version.txt"
    try:
        release_version = version_file.read_text(encoding="utf-8-sig").strip()
    except Exception:
        release_version = ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", release_version):
        print(f"[ERROR] Build output thieu version.txt hop le: {version_file}")
        return 1

    ca_bundle = root / "cacert.pem"
    if not ca_bundle.is_file() or ca_bundle.stat().st_size < 100_000:
        print(f"[ERROR] Build output thieu CA bundle cho HTTPS: {ca_bundle}")
        return 1

    leaked = []
    for path in root.rglob("*.py"):
        try:
            if _is_app_source(path, root):
                leaked.append(path)
        except ValueError:
            continue

    if leaked:
        print("[ERROR] Build output bi lo source app:")
        for path in leaked[:50]:
            print("   ", path)
        if len(leaked) > 50:
            print(f"   ... va {len(leaked) - 50} file khac")
        return 1

    secret_hits = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            blob = path.read_bytes()
        except Exception:
            blob = b""
        for label, pattern in BINARY_LEAK_PATTERNS.items():
            if blob and pattern.search(blob):
                secret_hits.append((path, label))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            continue
        for label, pattern in KEY_PATTERNS.items():
            if pattern.search(text):
                secret_hits.append((path, label))
        for label, pattern in PROMPT_LEAK_PATTERNS.items():
            if pattern.search(text):
                secret_hits.append((path, label))
        if path.name.lower() == "config.json":
            try:
                data = json.loads(text)
            except Exception:
                data = {}
            if isinstance(data, dict):
                for field in SENSITIVE_CONFIG_FIELDS:
                    value = str(data.get(field, "") or "").strip()
                    if value:
                        secret_hits.append((path, f"config.{field} khong rong"))
                for field in PRIVATE_CONFIG_FIELDS:
                    value = str(data.get(field, "") or "").strip()
                    if value:
                        secret_hits.append((path, f"config.{field} chua duoc lam sach"))

    if secret_hits:
        print("[ERROR] Build output con lo API key/prompt/config rieng:")
        for path, label in secret_hits[:50]:
            print(f"    {label}: {path}")
        if len(secret_hits) > 50:
            print(f"    ... va {len(secret_hits) - 50} loi khac")
        return 1

    print("[OK] Khong phat hien source app .py trong output")
    print("[OK] Khong phat hien API key/prompt rieng trong output")
    print(f"[OK] Release version: {release_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
