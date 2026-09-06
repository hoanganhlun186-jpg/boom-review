"""AutoRecapPro V2 — license screen (bypassed).

Màn hình xác thực cũ đã được tắt. run_license_check() luôn
trả True để app mở thẳng không cần check.
"""


class LicenseScreen:
    """Stub — không hiển thị gì."""
    def __init__(self, parent=None, on_success=None):
        if on_success:
            try:
                from engine.license_guard import LicenseResult
                on_success(LicenseResult(valid=True, code="ok"))
            except Exception:
                on_success(None)


def run_license_check(root=None) -> bool:
    """Luôn trả True — license check đã tắt."""
    try:
        from engine.license_guard import LicenseResult
        if root is not None:
            root.license_result = LicenseResult(valid=True, code="ok")
    except Exception:
        pass
    return True
