"""
create_asset_zips.py — Tạo boom_tools.zip và boom_models.zip đúng cấu trúc
Chạy file này từ thư mục gốc app: D:\hongguo\AutoRecapPro_V2\AutoRecapPro_V2
"""
import os, zipfile
from pathlib import Path

APP_DIR = Path(__file__).parent

def zip_folder(folder: Path, zip_path: Path, base: Path, mode="w"):
    """Nén folder vào zip, giữ nguyên path tương đối từ base"""
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        count = 0
        for file in folder.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(base)
                zf.write(file, arcname)
                count += 1
                print(f"  + {arcname}")
        print(f"  => {count} files")

# ── boom_tools.zip ─────────────────────────────────────────────────────────────
tools_folder = APP_DIR / "data" / "tools"
tools_zip    = APP_DIR / "boom_tools.zip"

if not tools_folder.exists():
    print(f"[!] Không tìm thấy: {tools_folder}")
else:
    print(f"\n[1/2] Đang nén boom_tools.zip từ {tools_folder} ...")
    zip_folder(tools_folder, tools_zip, base=APP_DIR)
    print(f"  => Lưu tại: {tools_zip}  ({tools_zip.stat().st_size / 1024 / 1024:.1f} MB)")

# ── boom_models.zip — gồm ngochuyen/ VÀ espeak-ng-data/ ──────────────────────
models_zip = APP_DIR / "boom_models.zip"
folders = [
    APP_DIR / "models" / "ngochuyen",
    APP_DIR / "models" / "espeak-ng-data",
]

print(f"\n[2/2] Đang nén boom_models.zip ...")
first = True
for folder in folders:
    if not folder.exists():
        print(f"  [!] Bỏ qua (không tìm thấy): {folder}")
        continue
    print(f"  Thêm: {folder.relative_to(APP_DIR)} ...")
    zip_folder(folder, models_zip, base=APP_DIR, mode="w" if first else "a")
    first = False

if models_zip.exists():
    print(f"  => Lưu tại: {models_zip}  ({models_zip.stat().st_size / 1024 / 1024:.1f} MB)")

# Thêm sentinel check
SENTINELS = [
    APP_DIR / "models" / "ngochuyen",
    APP_DIR / "data" / "tools",
]
print("\n--- Kiểm tra sentinel ---")
for s in SENTINELS:
    print(f"  {'✅' if s.exists() else '❌'} {s.relative_to(APP_DIR)}")

print("\n✅ Xong! Upload 2 file zip lên GitHub Release 'assets-v1':")
print("   https://github.com/hoanganhlun186-jpg/boom-review/releases/tag/assets-v1")
print("   Edit → xóa file cũ → upload boom_tools.zip + boom_models.zip → Save")
input("\nNhấn Enter để đóng...")
