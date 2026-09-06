"""AutoRecapPro V2 — account auth (bypassed).

Module cũ dùng Supabase Auth. Đã tắt — giữ lại interface để
không vỡ import ở bất kỳ đâu còn gọi vào.
"""


class AccountAuthError(RuntimeError):
    def __init__(self, code: str = "disabled", message: str = "Auth disabled"):
        super().__init__(message)
        self.code = code


def has_saved_session() -> bool:
    return False


def clear_session() -> None:
    pass


def sign_in(email: str, password: str) -> dict:
    return {}


def activate_device(email: str, password: str) -> dict:
    return {"valid": True}


def check_device() -> dict:
    return {"valid": True}


def get_hwid_display() -> str:
    try:
        from engine.license_guard import get_hwid_display as _g
        return _g()
    except Exception:
        return "N/A"
