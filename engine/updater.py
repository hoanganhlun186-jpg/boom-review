"""
engine/updater.py — Kiểm tra phiên bản mới từ AnhStudio Server
Không tự download — user chủ động tải.
"""

import json, sys, threading, urllib.request, urllib.error
from pathlib import Path
from typing import Optional, Callable

SERVER_URL = "http://163.61.182.119:8000"
_APP_ID    = "boomreview"

# ── Đọc version hiện tại từ version.txt ──────────────────────────────────────
def _get_app_base() -> Path:
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def get_current_version() -> str:
    for ver_file in [
        _get_app_base() / "version.txt",
        Path(__file__).resolve().parent.parent / "version.txt",
    ]:
        try:
            if ver_file.exists():
                return ver_file.read_text(encoding="utf-8-sig").strip().split("\n")[0].strip()
        except Exception:
            continue
    return "1.0.0"


def _parse_version(ver: str):
    try:
        return tuple(int(x) for x in ver.strip().split("."))
    except Exception:
        return (0, 0, 0)


# ── Gọi API server lấy version mới nhất ──────────────────────────────────────
def check_for_update(timeout: int = 8) -> Optional[dict]:
    """
    Trả về None nếu đang dùng bản mới nhất hoặc lỗi.
    Trả về dict nếu có bản mới: {latest, current, download_url, release_notes}
    """
    try:
        url = f"{SERVER_URL}/api/version/{_APP_ID}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        latest  = str(data.get("version", "")).strip()
        current = get_current_version()

        if not latest:
            return None

        if _parse_version(latest) > _parse_version(current):
            return {
                "latest":        latest,
                "current":       current,
                "download_url":  str(data.get("download_url", "") or ""),
                "release_notes": str(data.get("release_notes", "") or ""),
            }
        return None

    except Exception:
        return None


def check_update_async(callback: Callable[[Optional[dict]], None]):
    """Kiểm tra update trong background thread, gọi callback khi xong."""
    def _run():
        result = check_for_update()
        try:
            callback(result)
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── Dialog thông báo update (PySide6) ────────────────────────────────────────
def show_update_dialog(parent, update_info: dict):
    """Hiện dialog có bản mới. Gọi từ main thread."""
    try:
        import webbrowser
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont

        dlg = QDialog(parent)
        dlg.setWindowTitle("Có phiên bản mới!")
        dlg.setFixedSize(420, 240)
        dlg.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(10)

        title = QLabel("🎉  Có phiên bản mới!")
        title.setFont(QFont("Segoe UI", 15, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        info = QLabel(
            f"Phiên bản hiện tại: v{update_info['current']}\n"
            f"Phiên bản mới nhất: v{update_info['latest']}"
        )
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("font-size: 13px;")
        layout.addWidget(info)

        if update_info.get("release_notes"):
            notes = QLabel(f"📋 {update_info['release_notes'][:150]}")
            notes.setWordWrap(True)
            notes.setAlignment(Qt.AlignCenter)
            notes.setStyleSheet("font-size: 11px; color: #888;")
            layout.addWidget(notes)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        btn_dl = QPushButton("⬇️  Tải về ngay")
        btn_dl.setFixedHeight(36)
        btn_dl.setStyleSheet("""
            QPushButton { background:#6366f1; color:white; border-radius:8px;
                          font-size:13px; font-weight:bold; }
            QPushButton:hover { background:#4f46e5; }
        """)

        btn_skip = QPushButton("Để sau")
        btn_skip.setFixedHeight(36)
        btn_skip.setStyleSheet("""
            QPushButton { background:transparent; color:#555; border:1px solid #ccc;
                          border-radius:8px; font-size:13px; }
            QPushButton:hover { background:#f0f0f0; }
        """)

        def _download():
            url = update_info.get("download_url", "")
            if url:
                webbrowser.open(url)
            dlg.accept()

        btn_dl.clicked.connect(_download)
        btn_skip.clicked.connect(dlg.reject)

        btn_row.addWidget(btn_dl)
        btn_row.addWidget(btn_skip)
        layout.addLayout(btn_row)

        dlg.exec()

    except Exception as e:
        print(f"[Updater] Dialog error: {e}")
