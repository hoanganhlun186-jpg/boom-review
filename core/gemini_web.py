# -*- coding: utf-8 -*-
"""
gemini_web.py — Tự động hóa Gemini Web cho BoomReview
=======================================================
Dùng Playwright (thay Selenium) — không cần chromedriver, không bao giờ bị
lỗi version mismatch khi Chrome tự update.

Flow đăng nhập (giống BoomStudio translate_tab):
  1. Mở Chrome HIỂN THỊ với profile riêng (launch_persistent_context)
  2. User tự đăng nhập Google / Gemini
  3. Khi phát hiện ô chat → lưu marker .gemini_login_ready
  4. Lần sau: Chrome chạy ẨN, reuse profile đã login

API công khai giữ nguyên để không cần sửa main.py / các module khác:
  - is_profile_ready(), driver_is_headless(), mark_profile_ready()
  - wait_for_gemini_login(driver, timeout, log)
  - create_driver(headless, log), create_auto_driver(log, force_visible)
  - send_prompt_to_gemini(prompt, timeout, log, driver, close_after, navigate)
"""

import os
import time
import logging
import re
import pathlib
from typing import Optional, Callable

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://gemini.google.com/app"
_LOGIN_MARKER = ".gemini_login_ready"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-software-rasterizer",
    "--no-first-run",
    "--no-default-browser-check",
]

_INPUT_SELS = [
    "rich-textarea div.ql-editor[contenteditable='true']",
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true']",
    "p[data-placeholder]",
    "textarea",
    ".ql-editor",
]
_SEND_SELS = [
    "button[aria-label='Send message']",
    "button[aria-label='Gửi']",
    "button[aria-label*='send' i]",
    "button.send-button",
    "button[data-mat-icon-name='send']",
]
_RESP_SELS = [
    ".model-response-text .markdown",
    "message-content .markdown",
    "[data-message-author-role='model']",
    "model-response-text",
    "model-response",
]


# ══════════════════════════════════════════════════════════════════
# GeminiDriver — wrapper thay thế Selenium WebDriver
# ══════════════════════════════════════════════════════════════════

class GeminiDriver:
    """Giữ playwright instance + context + page.

    Có đầy đủ attribute backward-compat với code cũ dùng Selenium WebDriver:
        driver._autorecap_headless
        driver._autorecap_profile_dir
        driver.quit()
        driver.close()
    """

    def __init__(self, pw, ctx, page, headless: bool, profile_dir: str):
        self._pw = pw                               # sync_playwright instance
        self._ctx = ctx                             # BrowserContext
        self._page = page                           # Page (tab hiện tại)
        self._autorecap_headless = headless
        self._autorecap_profile_dir = profile_dir

    # ── Đóng ─────────────────────────────────────────────────────
    def close(self):
        try: self._ctx.close()
        except Exception: pass
        try: self._pw.stop()
        except Exception: pass

    def quit(self):                                 # alias Selenium
        self.close()


# ══════════════════════════════════════════════════════════════════
# Profile helpers (giữ nguyên logic cũ)
# ══════════════════════════════════════════════════════════════════

def _default_profile_dir() -> str:
    configured = str(os.environ.get("AUTORECAP_CHROME_PROFILE_DIR") or "").strip()
    if configured:
        return configured
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return str(pathlib.Path(appdata) / "AutoRecapPro" / "gemini_profile")


def _profile_has_session(profile_dir=None) -> bool:
    """Kiểm tra profile đã đăng nhập bằng marker file."""
    profile_dir = str(profile_dir or _default_profile_dir())
    return os.path.isfile(os.path.join(profile_dir, _LOGIN_MARKER))


def is_profile_ready(profile_dir=None) -> bool:
    """Public API — True nếu profile đã có phiên đăng nhập."""
    return _profile_has_session(profile_dir)


def _prefer_headless() -> bool:
    mode = str(os.environ.get("AUTORECAP_GEMINI_WEB_HEADLESS", "auto") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off", "visible", "show"}:
        return False
    if mode in {"1", "true", "yes", "on", "headless", "hidden"}:
        return True
    return _profile_has_session()          # ẩn nếu đã login, hiện nếu chưa


def driver_is_headless(driver) -> bool:
    return bool(getattr(driver, "_autorecap_headless", False))


def mark_profile_ready(driver_or_dir) -> None:
    """Ghi marker file báo profile đã đăng nhập thành công."""
    if isinstance(driver_or_dir, str):
        profile_dir = driver_or_dir
    else:
        profile_dir = str(getattr(driver_or_dir, "_autorecap_profile_dir", "") or "")
    if not profile_dir:
        return
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(os.path.join(profile_dir, _LOGIN_MARKER), "w", encoding="ascii") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════
# Playwright helpers
# ══════════════════════════════════════════════════════════════════

def _find_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise ImportError(
            "Thiếu playwright. Cài: pip install playwright && playwright install chromium"
        )


def _launch_kwargs(headless: bool, extra_args=None) -> dict:
    """Tạo kwargs cho launch_persistent_context.

    Ưu tiên Google Chrome hệ thống (channel='chrome') như translate_tab.py;
    nếu không có → dùng Chromium bundled của Playwright.
    """
    import shutil

    args = list(_BROWSER_ARGS)
    if extra_args:
        args.extend(extra_args)

    kw = dict(
        headless=headless,
        user_agent=_UA,
        viewport={"width": 1280, "height": 900},
        args=args,
    )

    # Kiểm tra Chrome hệ thống (Windows)
    chrome_paths_win = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    has_chrome = any(os.path.isfile(p) for p in chrome_paths_win)
    if not has_chrome:
        has_chrome = bool(shutil.which("google-chrome") or shutil.which("chrome"))

    if has_chrome:
        kw["channel"] = "chrome"      # Playwright tự map sang chrome.exe

    return kw


def _find_input(page):
    for sel in _INPUT_SELS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


def _inject_text(page, text: str):
    """Nhét text vào ô chat Gemini (cùng cách translate_tab.py dùng)."""
    page.evaluate(
        '''(text) => {
            const el = document.activeElement?.contentEditable === "true"
                ? document.activeElement
                : document.querySelector("[contenteditable='true']");
            if (el) {
                el.focus();
                el.innerText = text;
                el.dispatchEvent(new Event("input", {bubbles: true}));
                el.dispatchEvent(new InputEvent("input", {bubbles: true, data: text}));
            }
        }''',
        text,
    )
    page.wait_for_timeout(300)
    page.keyboard.press("End")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)


def _send_message(page) -> bool:
    """Click nút gửi hoặc Enter. Trả về True khi ô nhập đã trống (đã gửi)."""
    btn_clicked = False
    for sel in _SEND_SELS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                disabled = btn.get_attribute("aria-disabled")
                if not btn.is_disabled() and disabled != "true":
                    btn.click()
                    btn_clicked = True
                    break
        except Exception:
            continue

    if not btn_clicked:
        page.keyboard.press("Enter")

    page.wait_for_timeout(600)

    # Xác nhận đã gửi: ô nhập phải trống
    for _ in range(6):
        try:
            txt = page.evaluate(
                '''() => {
                    const el = document.querySelector("[contenteditable='true']");
                    return el ? (el.innerText || "").trim() : null;
                }'''
            )
            if txt is None or txt == "":
                return True
        except Exception:
            pass
        # Chưa gửi → thử Enter thêm
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)

    return False


def _get_last_response(page) -> str:
    for sel in _RESP_SELS:
        try:
            els = page.query_selector_all(sel)
            if els:
                text = els[-1].inner_text().strip()
                if len(text) > 10:
                    return text
        except Exception:
            continue
    return ""


def _wait_response(page, timeout: int, log: Callable) -> str:
    """Đợi Gemini response ổn định (cùng cơ chế translate_tab.py)."""
    prev, stable = "", 0
    deadline = time.time() + timeout
    last_log = 0.0

    page.wait_for_timeout(1000)          # đợi Gemini bắt đầu xử lý

    while time.time() < deadline:
        cur = _get_last_response(page)
        if cur and cur == prev:
            stable += 1
            if stable >= 8:              # ổn định 4 giây (8 × 500ms)
                return cur
        else:
            stable = 0
            prev = cur
        page.wait_for_timeout(500)

        now = time.time()
        remaining = max(0, int(deadline - now))
        if now - last_log >= 20:
            log(f"   ⏳ Đợi Gemini... còn ~{remaining}s")
            last_log = now

    return prev or _get_last_response(page)


# ══════════════════════════════════════════════════════════════════
# Driver creation
# ══════════════════════════════════════════════════════════════════

def create_driver(headless: Optional[bool] = None, log=None) -> GeminiDriver:
    """Tạo GeminiDriver (Playwright persistent context).

    Lần đầu (chưa login): headless=False → Chrome hiển thị để user đăng nhập.
    Những lần sau (đã có marker): headless=True → Chrome ẩn.
    Retry tự động 3 lần nếu Chrome bận / profile bị khóa.
    """
    if log is None:
        log = lambda msg: logger.info(msg)
    if headless is None:
        headless = _prefer_headless()

    sync_playwright = _find_playwright()
    profile_dir = _default_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)

    kw = _launch_kwargs(headless)
    last_exc = None

    for attempt in range(1, 4):
        if attempt > 1:
            log(f"   ⚠️ Chrome bận hoặc profile bị khóa (lần {attempt}/3), chờ 3s...")
            time.sleep(3)
        pw = None
        try:
            pw = sync_playwright().start()
            ctx = pw.chromium.launch_persistent_context(profile_dir, **kw)
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            driver = GeminiDriver(pw, ctx, page, headless, profile_dir)
            log(f"   🌐 Chrome {'ẩn' if headless else 'hiển thị'} đã khởi động")
            return driver
        except Exception as e:
            last_exc = e
            log(f"   ❌ Chrome khởi động thất bại (lần {attempt}): {str(e)[:200]}")
            try:
                if pw: pw.stop()
            except Exception:
                pass

    raise RuntimeError(f"Không khởi động Chrome: {last_exc}")


def create_auto_driver(log=None, force_visible: bool = False) -> GeminiDriver:
    """Tạo driver, tự động chọn headless/visible, tự fallback nếu cần."""
    if log is None:
        log = lambda msg: logger.info(msg)

    use_headless = False if force_visible else _prefer_headless()
    try:
        return create_driver(headless=use_headless, log=log)
    except Exception as exc:
        if not use_headless:
            raise
        visible_fallback = str(
            os.environ.get("AUTORECAP_GEMINI_WEB_VISIBLE_FALLBACK", "1") or "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        if not visible_fallback:
            raise
        log(f"   ⚠️ Chrome ẩn thất bại: {str(exc)[:180]}")
        log("   👁️ Chuyển sang Chrome hiển thị để khôi phục phiên...")
        return create_driver(headless=False, log=log)


# ══════════════════════════════════════════════════════════════════
# Login
# ══════════════════════════════════════════════════════════════════

def wait_for_gemini_login(
    driver: GeminiDriver,
    timeout: int = 300,
    log: Optional[Callable] = None,
) -> bool:
    """Chờ user đăng nhập Gemini trong cửa sổ Chrome HIỂN THỊ.

    Không gửi prompt — chỉ detect ô chat, lưu marker, rồi trả về.
    Gọi từ nút "Đăng nhập Gemini Web" trong GUI.
    """
    if log is None:
        log = lambda msg: logger.info(msg)

    page = driver._page
    deadline = time.time() + max(15, int(timeout or 300))
    last_notice = 0.0

    # Điều hướng đến Gemini nếu chưa ở đó
    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""
    if "gemini.google.com" not in current_url.lower():
        try:
            page.goto(_GEMINI_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"   ⚠️ Navigate đến Gemini lỗi: {e}")

    log("   Đăng nhập Google trong cửa sổ Chrome. App đang chờ ô chat Gemini...")

    while time.time() < deadline:
        # Đảm bảo đang ở gemini.google.com
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        if "gemini.google.com" not in current_url.lower():
            time.sleep(1)
            continue

        # Phát hiện ô chat → đã đăng nhập
        try:
            for sel in _INPUT_SELS:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    mark_profile_ready(driver)
                    log("   ✅ Đăng nhập Gemini Web thành công, phiên đã được lưu")
                    return True
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("target closed", "invalid session", "no such window")):
                raise RuntimeError("Cửa sổ Chrome đăng nhập đã bị đóng") from exc

        now = time.time()
        if now - last_notice >= 15:
            log(f"   ⏳ Đang chờ đăng nhập Gemini Web... còn {max(0, int(deadline - now))}s")
            last_notice = now
        time.sleep(1)

    log("   ⚠️ Chưa xác nhận được ô chat Gemini trong thời gian chờ")
    return False


# ══════════════════════════════════════════════════════════════════
# Send prompt
# ══════════════════════════════════════════════════════════════════

def send_prompt_to_gemini(
    prompt: str,
    timeout: int = 240,
    log: Optional[Callable] = None,
    driver=None,
    close_after: bool = False,
    navigate: bool = True,
) -> str:
    """Gửi prompt vào Gemini Web, trả về response text.

    Args:
        prompt:      Nội dung gửi Gemini
        timeout:     Giây đợi tối đa response
        log:         Callback log (nhận str)
        driver:      GeminiDriver đã tạo sẵn (None = tự tạo + đóng sau)
        close_after: Đóng browser sau khi xong (chỉ dùng khi driver=None)
        navigate:    True = luôn điều hướng về Gemini trước khi gửi
    """
    if log is None:
        log = lambda msg: logger.info(msg)

    _own_driver = driver is None
    try:
        if _own_driver:
            driver = create_auto_driver(log=log)

        page = driver._page

        # ── Navigate đến Gemini ─────────────────────────────────
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""

        should_navigate = bool(navigate) or ("gemini.google.com" not in current_url.lower())
        if should_navigate:
            log("   🌐 Mở Gemini Web...")
            try:
                page.goto(_GEMINI_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
            except Exception as e:
                log(f"   ⚠️ Navigate Gemini lỗi: {e}")
        else:
            log("   🌐 Dùng lại phiên Gemini hiện tại...")
            page.wait_for_timeout(500)

        # ── Chờ ô chat xuất hiện ───────────────────────────────
        inp = None
        for _ in range(30):              # đợi tối đa 15s
            inp = _find_input(page)
            if inp:
                break
            page.wait_for_timeout(500)

        if not inp:
            log("   ❌ Không tìm thấy ô chat Gemini. Hãy đăng nhập Gmail trước.")
            log("   💡 Bấm nút 'Đăng nhập Gemini Web' trong app để mở Chrome và đăng nhập.")
            return ""

        mark_profile_ready(driver)

        # ── Nhập text ──────────────────────────────────────────
        try:
            inp.click()
        except Exception:
            pass
        _inject_text(page, prompt)

        # ── Gửi ───────────────────────────────────────────────
        sent = _send_message(page)
        if not sent:
            log("   ⚠️ Chưa xác nhận gửi được, thử Enter lần cuối...")
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        log("   🚀 Đã gửi prompt, đợi Gemini response...")

        # ── Chờ response ───────────────────────────────────────
        response = _wait_response(page, timeout, log)
        if response:
            log(f"   ✅ Response nhận được ({len(response)} ký tự)")
        else:
            log("   ⚠️ Response rỗng")
        return response

    except Exception as e:
        log(f"   ❌ Gemini Web lỗi: {e}")
        return ""
    finally:
        if _own_driver and close_after and driver:
            try:
                driver.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════
# Backward-compat utils
# ══════════════════════════════════════════════════════════════════

def is_selenium_available() -> bool:
    """Backward compat — check Playwright thay vì Selenium."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False
