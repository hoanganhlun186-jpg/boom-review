# -*- coding: utf-8 -*-
"""
asset_downloader.py — Tự động tải models & tools từ GitHub Releases
Chạy lần đầu nếu thiếu file, có progress bar PySide6.
"""
import os, sys, zipfile, shutil, threading, requests

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QApplication
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont

# ============================================================
# CẤU HÌNH — URL file zip trên GitHub Releases
# ============================================================
GITHUB_RELEASE_BASE = "https://github.com/hoanganhlun186-jpg/boom-review/releases/download/assets-v1"

ASSETS = [
    {
        "name":     "Models giọng đọc (NgocHuyen)",
        "url":      f"{GITHUB_RELEASE_BASE}/boom_models.zip",
        "zip_name": "boom_models.zip",
        "dest_dir": "models",          # giải nén vào <app_dir>/models/
    },
    {
        "name":     "Công cụ & CUDA DLLs",
        "url":      f"{GITHUB_RELEASE_BASE}/boom_tools.zip",
        "zip_name": "boom_tools.zip",
        "dest_dir": "data",            # giải nén vào <app_dir>/data/
    },
]

# File sentinel: nếu tồn tại thì coi như đã download đủ
SENTINEL_FILES = [
    os.path.join("models", "ngochuyen"),   # thư mục models/ngochuyen/
    os.path.join("data",   "tools"),       # thư mục data/tools/
]

# ============================================================
# Helper: thư mục gốc của app (cạnh file exe hoặc main.py)
# ============================================================
def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def needs_download() -> bool:
    app_dir = get_app_dir()
    for rel in SENTINEL_FILES:
        if not os.path.exists(os.path.join(app_dir, rel)):
            return True
    return False


# ============================================================
# Thread tải + giải nén
# ============================================================
class DownloadThread(QThread):
    progress     = Signal(int, int, str)   # (current_bytes, total_bytes, label)
    asset_done   = Signal(str)             # tên asset vừa xong
    all_done     = Signal()
    error        = Signal(str)

    def run(self):
        app_dir = get_app_dir()
        try:
            for asset in ASSETS:
                dest_dir = os.path.join(app_dir, asset["dest_dir"])
                zip_path = os.path.join(app_dir, asset["zip_name"])

                # --- Download ---
                self.progress.emit(0, 1, f"Đang tải: {asset['name']}...")
                try:
                    resp = requests.get(asset["url"], stream=True, timeout=30)
                    resp.raise_for_status()
                except Exception as e:
                    self.error.emit(f"Không tải được {asset['name']}:\n{e}")
                    return

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            self.progress.emit(downloaded, total or downloaded,
                                               f"Đang tải: {asset['name']} "
                                               f"({downloaded // 1024 // 1024} MB / "
                                               f"{total // 1024 // 1024} MB)")

                # --- Giải nén ---
                self.progress.emit(0, 1, f"Đang giải nén: {asset['name']}...")
                os.makedirs(dest_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    members = zf.infolist()
                    for i, member in enumerate(members):
                        zf.extract(member, app_dir)
                        self.progress.emit(i + 1, len(members),
                                           f"Giải nén: {asset['name']} "
                                           f"({i + 1}/{len(members)} file)")

                # --- Xóa zip sau khi xong ---
                try:
                    os.remove(zip_path)
                except Exception:
                    pass

                self.asset_done.emit(asset["name"])

            self.all_done.emit()

        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# Dialog hiển thị progress
# ============================================================
class DownloadDialog(QDialog):
    download_finished = Signal()   # phát khi xong hết, main.py lắng nghe

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BOOM Review — Cài đặt lần đầu")
        self.setFixedSize(520, 230)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint)
        self._success = False
        self._build_ui()
        self._thread = DownloadThread()
        self._thread.progress.connect(self._on_progress)
        self._thread.asset_done.connect(self._on_asset_done)
        self._thread.all_done.connect(self._on_all_done)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)

        title = QLabel("⬇️  Tải tài nguyên lần đầu")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("App cần tải models giọng đọc & công cụ (~1.4 GB).\nChỉ tải 1 lần duy nhất, lần sau mở thẳng.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #666; font-size: 12px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self.lbl_status = QLabel("Đang chuẩn bị...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("font-size: 12px; color: #333;")
        layout.addWidget(self.lbl_status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(22)
        self.bar.setStyleSheet("""
            QProgressBar { border: 1px solid #ddd; border-radius: 6px; background: #f5f5f5; text-align: center; }
            QProgressBar::chunk { background: #e74c3c; border-radius: 5px; }
        """)
        layout.addWidget(self.bar)

        self.btn_close = QPushButton("Đang tải...")
        self.btn_close.setEnabled(False)
        self.btn_close.setFixedHeight(36)
        self.btn_close.setStyleSheet("""
            QPushButton { background: #aaa; color: white; border-radius: 8px; font-size: 13px; font-weight: bold; }
            QPushButton:enabled { background: #27ae60; }
            QPushButton:enabled:hover { background: #219150; }
        """)
        self.btn_close.clicked.connect(self._finish)
        layout.addWidget(self.btn_close)

    def _on_progress(self, current: int, total: int, label: str):
        self.lbl_status.setText(label)
        if total > 0:
            self.bar.setValue(int(current * 100 / total))

    def _on_asset_done(self, name: str):
        self.lbl_status.setText(f"✅ Xong: {name}")

    def _on_all_done(self):
        self._success = True
        self.bar.setValue(100)
        self.lbl_status.setText("✅ Tải xong! Nhấn Tiếp tục để vào app.")
        self.btn_close.setEnabled(True)
        self.btn_close.setText("Tiếp tục vào app →")

    def _on_error(self, msg: str):
        self.lbl_status.setText(f"❌ Lỗi: {msg}")
        self.btn_close.setEnabled(True)
        self.btn_close.setText("Đóng")
        self.btn_close.setStyleSheet("""
            QPushButton { background: #e74c3c; color: white; border-radius: 8px;
                          font-size: 13px; font-weight: bold; }
        """)

    def _finish(self):
        if self._success:
            self.download_finished.emit()
        self.accept()


# ============================================================
# Hàm gọi từ main.py
# ============================================================
def ensure_assets(parent=None) -> bool:
    """
    Kiểm tra và tải assets nếu cần.
    Trả về True nếu sẵn sàng chạy app, False nếu user đóng khi lỗi.
    """
    if not needs_download():
        return True

    result = {"ok": False}

    dialog = DownloadDialog(parent)

    def _on_done():
        result["ok"] = True

    dialog.download_finished.connect(_on_done)
    dialog.exec()
    return result["ok"]
