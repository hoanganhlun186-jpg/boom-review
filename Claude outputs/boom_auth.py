# -*- coding: utf-8 -*-
"""
boom_auth.py — Module xác thực BoomReview
Gọi /api/boomreview/login trên server AnhStudio.
"""
import os, json, datetime, requests
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap

# ============================================================
# CẤU HÌNH — đổi SERVER_URL thành URL server thật của bạn
# ============================================================
SERVER_URL = "http://163.61.182.119:8000"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_token.json")
REQUEST_TIMEOUT = 15


# ============================================================
# Lưu / đọc token local
# ============================================================
def save_token(username: str, token: str, expiry: str):
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": username, "token": token, "expiry": expiry}, f)
    except Exception:
        pass

def load_token() -> dict:
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def clear_token():
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
    except Exception:
        pass

def is_token_valid(token_data: dict) -> bool:
    """Kiểm tra token còn hạn không (dùng expiry từ server)."""
    if not token_data.get("token"):
        return False
    expiry_str = token_data.get("expiry", "")
    if not expiry_str:
        return False
    try:
        expiry = datetime.datetime.strptime(expiry_str[:19], "%Y-%m-%d %H:%M:%S")
        return expiry > datetime.datetime.now()
    except Exception:
        return False

def verify_token_with_server(token: str) -> bool:
    """Xác minh token với server — dùng khi khởi động app."""
    try:
        resp = requests.get(
            f"{SERVER_URL}/api/boomreview/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT
        )
        return resp.status_code == 200
    except Exception:
        return False


# ============================================================
# Thread đăng nhập (không block UI)
# ============================================================
class LoginThread(QThread):
    success = Signal(dict)   # {"username": ..., "token": ..., "expiry": ...}
    failed  = Signal(str)    # thông báo lỗi

    def __init__(self, username: str, password: str):
        super().__init__()
        self.username = username
        self.password = password

    def run(self):
        try:
            resp = requests.post(
                f"{SERVER_URL}/api/boomreview/login",
                json={"username": self.username, "password": self.password},
                timeout=REQUEST_TIMEOUT
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "success":
                self.success.emit(data)
            else:
                msg = data.get("detail") or data.get("message") or "Sai tài khoản hoặc mật khẩu."
                self.failed.emit(msg)
        except requests.exceptions.ConnectionError:
            self.failed.emit("Không kết nối được server. Kiểm tra mạng hoặc server đã chạy chưa.")
        except requests.exceptions.Timeout:
            self.failed.emit("Server phản hồi quá chậm. Thử lại sau.")
        except Exception as e:
            self.failed.emit(f"Lỗi: {str(e)}")


# ============================================================
# Dialog đăng nhập
# ============================================================
class LoginDialog(QDialog):
    login_success = Signal(str, str)   # (username, token)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BOOM Review — Đăng nhập")
        self.setFixedSize(400, 320)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._thread = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)

        # Logo / tiêu đề
        title = QLabel("🎬 BOOM Review")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Đăng nhập tài khoản của bạn")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # Username
        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("Tên đăng nhập")
        self.txt_user.setFixedHeight(40)
        self.txt_user.setStyleSheet(self._input_style())
        layout.addWidget(self.txt_user)

        # Password
        self.txt_pass = QLineEdit()
        self.txt_pass.setPlaceholderText("Mật khẩu")
        self.txt_pass.setEchoMode(QLineEdit.Password)
        self.txt_pass.setFixedHeight(40)
        self.txt_pass.setStyleSheet(self._input_style())
        self.txt_pass.returnPressed.connect(self._do_login)
        layout.addWidget(self.txt_pass)

        # Nút đăng nhập
        self.btn_login = QPushButton("Đăng nhập")
        self.btn_login.setFixedHeight(42)
        self.btn_login.setCursor(Qt.PointingHandCursor)
        self.btn_login.setStyleSheet("""
            QPushButton {
                background: #e74c3c; color: white;
                border-radius: 8px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #c0392b; }
            QPushButton:disabled { background: #aaa; }
        """)
        self.btn_login.clicked.connect(self._do_login)
        layout.addWidget(self.btn_login)

        # Thông báo lỗi
        self.lbl_error = QLabel("")
        self.lbl_error.setAlignment(Qt.AlignCenter)
        self.lbl_error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        layout.addStretch()

        # Footer
        footer = QLabel("Liên hệ admin để được cấp tài khoản")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #bbb; font-size: 11px;")
        layout.addWidget(footer)

    def _input_style(self):
        return """
            QLineEdit {
                border: 1.5px solid #ddd; border-radius: 8px;
                padding: 6px 12px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #e74c3c; }
        """

    def _do_login(self):
        username = self.txt_user.text().strip()
        password = self.txt_pass.text().strip()
        if not username or not password:
            self.lbl_error.setText("Vui lòng nhập đầy đủ tài khoản và mật khẩu.")
            return
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Đang đăng nhập...")
        self.lbl_error.setText("")
        self._thread = LoginThread(username, password)
        self._thread.success.connect(self._on_success)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_success(self, data: dict):
        username = data.get("username", "")
        token    = data.get("token", "")
        expiry   = data.get("expiry", "")
        save_token(username, token, expiry)
        self.login_success.emit(username, token)
        self.accept()

    def _on_failed(self, msg: str):
        self.lbl_error.setText(msg)
        self.btn_login.setEnabled(True)
        self.btn_login.setText("Đăng nhập")


# ============================================================
# Hàm gọi từ main.py khi khởi động app
# ============================================================
def check_auth(parent=None) -> tuple[bool, str, str]:
    """
    Kiểm tra xác thực khi khởi động.
    Trả về (ok, username, token).
    - Nếu có token còn hạn → trả về luôn (không hiện dialog).
    - Nếu hết hạn hoặc chưa đăng nhập → hiện LoginDialog.
    - Nếu user đóng dialog → trả về (False, "", "").
    """
    token_data = load_token()

    # 1) Token còn hạn — kiểm tra nhanh với server
    if is_token_valid(token_data):
        token = token_data["token"]
        if verify_token_with_server(token):
            return True, token_data["username"], token
        # Token expired hoặc server từ chối → xóa, yêu cầu đăng nhập lại
        clear_token()

    # 2) Hiện dialog đăng nhập
    dialog = LoginDialog(parent)
    result = {"ok": False, "username": "", "token": ""}

    def _on_ok(username, token):
        result["ok"] = True
        result["username"] = username
        result["token"] = token

    dialog.login_success.connect(_on_ok)
    dialog.exec()
    return result["ok"], result["username"], result["token"]
