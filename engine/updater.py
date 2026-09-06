"""
AutoRecapPro V2 - Auto Update Checker
======================================
Kiểm tra phiên bản mới từ Supabase.
Khi có update: hiện thông báo + link tải.
Không tự download — user chủ động tải.
"""

import json
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Callable

# ── Đọc version hiện tại từ version.txt bundled ──────────────────────────────
def _get_app_base() -> Path:
    if getattr(sys, 'frozen', False) or globals().get('__compiled__'):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def get_current_version() -> str:
    """Đọc version từ version.txt trong folder app."""
    candidates = [
        _get_app_base() / "version.txt",
        Path(__file__).resolve().parent.parent / "version.txt",
    ]
    for ver_file in candidates:
        try:
            if ver_file.exists():
                return ver_file.read_text(encoding="utf-8-sig").strip().split("\n")[0].strip()
        except Exception:
            continue
    return "2.0.0"


def _parse_version(ver: str):
    """Chuyển '2.1.3' → (2, 1, 3) để so sánh."""
    try:
        return tuple(int(x) for x in ver.strip().split("."))
    except Exception:
        return (0, 0, 0)


# ── Check update từ Supabase public table ────────────────────────────────────
# Bảng "app_versions" trong Supabase — public read, không cần auth
# Tạo bảng:
#   CREATE TABLE app_versions (
#     app_id TEXT PRIMARY KEY,
#     latest_version TEXT,
#     download_url TEXT,
#     release_notes TEXT,
#     updated_at TIMESTAMPTZ DEFAULT NOW()
#   );
#   INSERT INTO app_versions VALUES (
#     'autorecappro_v2', '2.0.0',
#     'https://drive.google.com/...',
#     'Phiên bản đầu tiên',
#     NOW()
#   );
# Nhớ set RLS policy: SELECT for public (anon) role

_SUPABASE_URL = None  # Tự lấy từ license_guard để tránh lặp
_APP_ID = "autorecappro_v2"


def _get_supabase_url() -> str:
    global _SUPABASE_URL
    if _SUPABASE_URL:
        return _SUPABASE_URL
    try:
        from engine.license_guard import SUPABASE_URL, SUPABASE_KEY
        _SUPABASE_URL = (SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        _SUPABASE_URL = ("", "")
    return _SUPABASE_URL


def check_for_update(timeout: int = 8) -> Optional[dict]:
    """
    Kiểm tra có bản mới không.
    
    Returns:
        None nếu đang dùng bản mới nhất hoặc lỗi
        dict nếu có bản mới: {
            'latest': '2.1.0',
            'current': '2.0.0', 
            'download_url': 'https://...',
            'release_notes': '...'
        }
    """
    try:
        url_key = _get_supabase_url()
        if not url_key[0]:
            return None

        supabase_url, supabase_key = url_key
        url = f"{supabase_url.rstrip('/')}/rest/v1/app_versions?app_id=eq.{_APP_ID}&select=latest_version,download_url,release_notes"
        
        req = urllib.request.Request(url, method="GET")
        req.add_header("apikey", supabase_key)
        req.add_header("Authorization", f"Bearer {supabase_key}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8", errors="replace"))

        if not rows:
            return None

        row = rows[0]
        latest = str(row.get("latest_version", "")).strip()
        current = get_current_version()

        if not latest:
            return None

        # So sánh version
        if _parse_version(latest) > _parse_version(current):
            return {
                "latest": latest,
                "current": current,
                "download_url": str(row.get("download_url", "") or ""),
                "release_notes": str(row.get("release_notes", "") or ""),
            }

        return None  # Đang dùng bản mới nhất

    except Exception:
        return None  # Lỗi mạng — bỏ qua


def check_update_async(callback: Callable[[Optional[dict]], None]):
    """
    Kiểm tra update trong background thread.
    Gọi callback(result) khi xong.
    
    Usage:
        def on_update(result):
            if result:
                show_update_dialog(result['latest'], result['download_url'])
        
        check_update_async(on_update)
    """
    def _run():
        result = check_for_update()
        try:
            callback(result)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── UI Helper — hiện dialog thông báo update ─────────────────────────────────
def show_update_dialog(parent, update_info: dict):
    """
    Hiện dialog thông báo có bản mới.
    Gọi từ main thread (tkinter).
    
    Args:
        parent: CTk root window
        update_info: dict từ check_for_update()
    """
    try:
        import customtkinter as ctk
        import webbrowser

        dialog = ctk.CTkToplevel(parent)
        dialog.title("Có phiên bản mới!")
        dialog.geometry("420x280")
        dialog.resizable(False, False)
        dialog.grab_set()

        # Center
        dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 420) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 280) // 2
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(
            dialog, text="🎉 Có phiên bản mới!",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(24, 8))

        ctk.CTkLabel(
            dialog,
            text=f"Phiên bản hiện tại: v{update_info['current']}\n"
                 f"Phiên bản mới nhất: v{update_info['latest']}",
            font=ctk.CTkFont(size=13)
        ).pack(pady=4)

        if update_info.get("release_notes"):
            notes_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            notes_frame.pack(fill="x", padx=24, pady=8)
            ctk.CTkLabel(
                notes_frame,
                text=f"📋 {update_info['release_notes'][:120]}",
                font=ctk.CTkFont(size=11),
                text_color="gray",
                wraplength=370,
                justify="left"
            ).pack()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=16)

        def _download():
            url = update_info.get("download_url", "")
            if url:
                webbrowser.open(url)
            dialog.destroy()

        ctk.CTkButton(
            btn_frame, text="⬇️ Tải về ngay",
            width=140, height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#6366f1", hover_color="#4f46e5",
            command=_download
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_frame, text="Để sau",
            width=100, height=36,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            border_width=1,
            command=dialog.destroy
        ).pack(side="left", padx=8)

    except Exception as e:
        print(f"[Updater] Dialog error: {e}")
