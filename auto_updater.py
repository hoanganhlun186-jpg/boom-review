# -*- coding: utf-8 -*-
"""
auto_updater.py — Tự động kiểm tra và cài bản mới cho BoomReview
=================================================================
Flow:
  1. Gọi GET /api/boomreview/version → {"version": "1.0.15", "url": "https://...BoomReview.zip"}
  2. So sánh với APP_VERSION hiện tại
  3. Nếu có bản mới → hiện dialog hỏi user có muốn update không
  4. Download ZIP vào %TEMP%, giải nén, chạy batch script tự copy đè rồi restart app
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
import threading
import subprocess
import requests
from packaging.version import Version

from PySide6.QtCore    import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QMessageBox, QApplication
)

# ─── Config ───────────────────────────────────────────────────────────────────
SERVER_URL      = "http://163.61.182.119:8000"
VERSION_ENDPOINT = f"{SERVER_URL}/api/boomreview/check_update"
REQUEST_TIMEOUT = 10

# ─── Packaging fallback nếu chưa cài ──────────────────────────────────────────
try:
    from packaging.version import Version as _Version
    def _newer(remote: str, local: str) -> bool:
        try:
            return _Version(remote) > _Version(local)
        except Exception:
            return remote.strip() != local.strip()
except ImportError:
    def _newer(remote: str, local: str) -> bool:
        """So sánh version đơn giản nếu packaging chưa có."""
        def _parts(v):
            return [int(x) for x in v.strip().split(".") if x.isdigit()]
        try:
            return _parts(remote) > _parts(local)
        except Exception:
            return False


# ─── Thread tải file ──────────────────────────────────────────────────────────
class DownloadThread(QThread):
    progress = Signal(int)          # 0-100
    finished = Signal(str)          # đường dẫn file tải về
    error    = Signal(str)

    def __init__(self, url: str, dest_path: str):
        super().__init__()
        self.url       = url
        self.dest_path = dest_path

    def run(self):
        try:
            resp = requests.get(self.url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(self.dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded * 100 / total))
            self.progress.emit(100)
            self.finished.emit(self.dest_path)
        except Exception as e:
            self.error.emit(str(e))


# ─── Dialog download + cài đặt ────────────────────────────────────────────────
class UpdateDialog(QDialog):
    def __init__(self, current_ver: str, new_ver: str, download_url: str, parent=None, force: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Cập nhật BoomReview")
        self.setFixedSize(420, 230)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._url     = download_url
        self._force   = force
        self._zip_path = None
        self._thread  = None
        self._build_ui(current_ver, new_ver)

    def _build_ui(self, cur, new):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        lbl_title = QLabel(f"🎉  Có bản mới: <b>v{new}</b>")
        lbl_title.setStyleSheet("font-size: 16px;")
        lay.addWidget(lbl_title)

        lbl_cur = QLabel(f"Phiên bản hiện tại: v{cur}")
        lbl_cur.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(lbl_cur)

        self.lbl_status = QLabel("Bấm <b>Cập nhật ngay</b> để tải và cài tự động.")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        lay.addStretch()

        btn_row = QHBoxLayout()
        self.btn_later = QPushButton("Để sau")
        self.btn_later.setFixedHeight(36)
        self.btn_later.clicked.connect(self.reject)
        self.btn_later.setVisible(not self._force)   # force_update → ẩn nút Để sau
        btn_row.addWidget(self.btn_later)

        self.btn_update = QPushButton("Cập nhật ngay")
        self.btn_update.setFixedHeight(36)
        self.btn_update.setStyleSheet(
            "QPushButton{background:#e74c3c;color:white;border-radius:6px;font-weight:bold;}"
            "QPushButton:hover{background:#c0392b;}"
            "QPushButton:disabled{background:#aaa;}"
        )
        self.btn_update.clicked.connect(self._start_download)
        btn_row.addWidget(self.btn_update)
        lay.addLayout(btn_row)

    # ── Download ──────────────────────────────────────────────────────────────
    def _start_download(self):
        self.btn_update.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.progress.setVisible(True)
        self.lbl_status.setText("Đang tải bản cập nhật...")

        tmp_dir = tempfile.mkdtemp(prefix="boomreview_upd_")
        # Detect đuôi file từ URL (.exe hoặc .zip)
        url_lower = self._url.lower().split("?")[0]
        ext = ".exe" if url_lower.endswith(".exe") else ".zip"
        self._zip_path = os.path.join(tmp_dir, f"BoomReview_update{ext}")

        self._thread = DownloadThread(self._url, self._zip_path)
        self._thread.progress.connect(self.progress.setValue)
        self._thread.finished.connect(self._on_downloaded)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_downloaded(self, file_path: str):
        self.lbl_status.setText("Đang khởi chạy cài đặt...")
        try:
            _apply_update(file_path)
        except Exception as e:
            self._on_error(str(e))

    def _on_error(self, msg: str):
        self.btn_update.setEnabled(True)
        self.btn_later.setEnabled(True)
        self.progress.setVisible(False)
        self.lbl_status.setText(f"❌ Lỗi: {msg}")


# ─── Áp dụng update: giải nén → chạy batch restart ───────────────────────────
def _get_app_dir() -> str:
    """Thư mục chứa BoomReview.exe (hoặc main.py khi dev)."""
    if getattr(sys, "frozen", False) or "__compiled__" in dir():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _apply_update(file_path: str):
    """
    Nếu file_path là .exe  → chạy installer thẳng, thoát app để installer chạy.
    Nếu file_path là .zip  → giải nén, tạo batch xcopy đè, restart exe.
    """
    is_exe = file_path.lower().endswith(".exe")

    if is_exe:
        # Chạy installer (NSIS/Inno Setup) rồi thoát app hiện tại
        subprocess.Popen(
            [file_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        QApplication.instance().quit()
        return

    # ── ZIP flow ──────────────────────────────────────────────────────────────
    app_dir     = _get_app_dir()
    tmp_extract = file_path.replace(".zip", "_extracted")
    os.makedirs(tmp_extract, exist_ok=True)

    with zipfile.ZipFile(file_path, "r") as zf:
        zf.extractall(tmp_extract)

    entries = os.listdir(tmp_extract)
    if len(entries) == 1 and os.path.isdir(os.path.join(tmp_extract, entries[0])):
        src_dir = os.path.join(tmp_extract, entries[0])
    else:
        src_dir = tmp_extract

    exe_name = os.path.basename(sys.executable) if (
        getattr(sys, "frozen", False) or "__compiled__" in dir()
    ) else "BoomReview.exe"
    exe_path = os.path.join(app_dir, exe_name)
    bat_path = os.path.join(tmp_extract, "do_update.bat")

    bat_content = f"""@echo off
echo Dang cap nhat BoomReview...
timeout /t 3 /nobreak >nul
xcopy /E /Y /I "{src_dir}\\*" "{app_dir}\\" >nul 2>&1
echo Cap nhat hoan tat!
start "" "{exe_path}"
rmdir /S /Q "{tmp_extract}"
del "%~f0"
"""
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    subprocess.Popen(
        ["cmd.exe", "/C", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
    QApplication.instance().quit()


# ─── Hàm public: kiểm tra version ────────────────────────────────────────────
def check_for_update(current_version: str, parent=None, silent: bool = True):
    """
    Gọi từ main.py khi khởi động (dùng thread để không block UI).
      silent=True  → chỉ hiện dialog khi CÓ bản mới
      silent=False → luôn hiện kết quả
    """
    def _check():
        try:
            resp = requests.get(VERSION_ENDPOINT, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return
            data = resp.json()
            remote_ver = data.get("latest_version", "")
            download_url = data.get("download_url", "")
            force = data.get("force_update", False)
            if not remote_ver or not download_url:
                return
            if _newer(remote_ver, current_version):
                # Phải chạy dialog trên main thread
                _show_update_dialog(current_version, remote_ver, download_url, parent, force)
        except Exception:
            pass  # Im lặng nếu không kết nối được

    t = threading.Thread(target=_check, daemon=True)
    t.start()


def _show_update_dialog(cur, new, url, parent, force=False):
    """Gọi từ worker thread → dùng QTimer để đưa về main thread."""
    from PySide6.QtCore import QTimer
    def _open():
        dlg = UpdateDialog(cur, new, url, parent, force)
        dlg.exec()
    QTimer.singleShot(0, _open)
