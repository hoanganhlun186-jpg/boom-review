import shutil
import sys
from pathlib import Path


APP_DIR = Path(r"D:\hongguo\AutoRecapPro_V2\AutoRecapPro_V2")


def _copy_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        print(f"[SKIP] Khong co: {src}")
        return
    if dst.exists():
        shutil.rmtree(dst)
    print(f"[COPY] {src} -> {dst}")
    shutil.copytree(src, dst)


def main() -> int:
    dist_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else APP_DIR / "dist_standalone" / "main.dist"
    dist_dir = dist_dir.resolve()
    if not dist_dir.exists():
        print(f"[ERROR] Chua co thu muc build: {dist_dir}")
        return 1

    _copy_dir(APP_DIR / "models", dist_dir / "models")
    _copy_dir(APP_DIR / "assets", dist_dir / "assets")

    tools_src = APP_DIR / "data" / "tools"
    if tools_src.exists() and any(tools_src.iterdir()):
        _copy_dir(tools_src, dist_dir / "data" / "tools")
    else:
        print("[SKIP] data/tools trong hoac khong ton tai")

    try:
        import certifi
        ca_source = Path(certifi.where())
        ca_target = dist_dir / "cacert.pem"
        if not ca_source.is_file() or ca_source.stat().st_size < 100_000:
            raise FileNotFoundError(f"CA bundle khong hop le: {ca_source}")
        shutil.copy2(ca_source, ca_target)
        print(f"[COPY] CA bundle -> {ca_target}")
    except Exception as exc:
        print(f"[ERROR] Khong copy duoc CA bundle: {exc}")
        return 1

    # Xoa capcut_device.json khoi dist — moi khach phai tu tao fingerprint rieng
    capcut_device = dist_dir / "capcut_device.json"
    if capcut_device.exists():
        capcut_device.unlink()
        print("[CLEAN] Da xoa capcut_device.json khoi dist (moi khach tu tao)")

    print("[OK] Da copy tai nguyen ngoai compile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
