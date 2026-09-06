"""AutoRecapPro V2 — license guard (bypassed).

Hệ thống license cũ đã được tắt. File này giữ lại API cũ để
không vỡ import, nhưng mọi check đều trả valid=True.
Server license mới sẽ được tích hợp riêng.
"""

import hashlib
import platform
import re
import subprocess
import sys


# ── Stub constants (không dùng Supabase nữa) ─────────────────────────────────
SUPABASE_URL = ""
SUPABASE_KEY = ""
APP_ID       = "autorecappro_v2"
APP_VER      = "2.0.0"
try:
    from engine.updater import get_current_version as _gcv
    APP_VER = _gcv()
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════════════
# Hardware ID (giữ lại để dùng sau nếu cần)
# ══════════════════════════════════════════════════════════════════════════════

def _run(cmd: list, timeout=6) -> str:
    try:
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        import subprocess as sp
        r = sp.run(cmd, capture_output=True, text=True,
                   encoding="utf-8", errors="ignore",
                   timeout=timeout, startupinfo=si)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def get_hwid() -> str:
    """Hardware ID 32 ký tự."""
    try:
        import uuid, hashlib
        node = uuid.getnode()
        mac = ":".join(f"{(node >> (i*8))&0xff:02x}" for i in range(5, -1, -1))
        raw = f"{platform.node()}|{mac}|{platform.machine()}".upper()
        return hashlib.sha256(raw.encode()).hexdigest()[:32].upper()
    except Exception:
        return hashlib.sha256(platform.node().encode()).hexdigest()[:32].upper()


def get_hwid_display() -> str:
    """HWID format dễ đọc: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"""
    h = get_hwid()
    return "-".join(h[i:i+4] for i in range(0, len(h), 4))


# ══════════════════════════════════════════════════════════════════════════════
# Public API — tất cả bypass, luôn valid=True
# ══════════════════════════════════════════════════════════════════════════════

class LicenseResult:
    def __init__(self, valid: bool = True, msg="", user="", expires="",
                 from_cache=False, code="ok"):
        self.valid      = valid
        self.msg        = msg
        self.user       = user
        self.expires    = expires
        self.from_cache = from_cache
        self.code       = code

    def __repr__(self):
        return f"LicenseResult(valid={self.valid}, user={self.user!r})"

    def remaining_days(self):
        return None  # Unlimited


def check_license(force_online=False) -> LicenseResult:
    """Luôn trả về valid — license check đã được tắt."""
    return LicenseResult(valid=True, msg="OK", user="", expires="Unlimited", code="ok")


def load_cached_license() -> LicenseResult:
    return LicenseResult(valid=True, msg="OK", user="", expires="Unlimited", code="ok")


def login_account(email: str, password: str) -> LicenseResult:
    return LicenseResult(valid=True, msg="OK", user=email, expires="Unlimited", code="ok")


def logout_account() -> None:
    pass


def cache_clear() -> None:
    pass
