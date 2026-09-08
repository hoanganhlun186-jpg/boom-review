# -*- coding: utf-8 -*-
"""
gemini_web.py — Tự động hóa Gemini Web cho BoomReview
=======================================================
Dùng Playwright — không cần chromedriver, không bao giờ bị lỗi version mismatch.

Flow đăng nhập:
  1. Mở Chrome HIỂN THỊ với profile riêng (launch_persistent_context)
  2. User tự đăng nhập Google / Gemini
  3. Khi phát hiện ô chat → lưu storage_state (auth.json) + marker
  4. Lần sau: mỗi luồng tự tạo Chrome riêng từ auth.json → song song được

Đa luồng (1–5 Chrome song song):
  - Gọi set_max_workers(n) để chọn số Chrome tối đa
  - send_prompt_parallel([p1,p2,...]) để gửi nhiều prompt cùng lúc
  - Mỗi Chrome độc lập, không share profile → không bị lock

API công khai giữ nguyên:
  - is_profile_ready(), driver_is_headless(), mark_profile_ready()
  - wait_for_gemini_login(driver, timeout, log)
  - create_driver(headless, log), create_auto_driver(log, force_visible)
  - send_prompt_to_gemini(prompt, timeout, log, driver, close_after, navigate)
  - set_max_workers(n)                   ← MỚI: chọn số Chrome song song (1-5)
  - send_prompt_parallel(prompts, ...)   ← MỚI: gửi nhiều prompt song song
"""

import os
import sys
import time
import logging
import pathlib
import threading
import concurrent.futures
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)

_GEMINI_URL   = "https://gemini.google.com/app"
_LOGIN_MARKER = ".gemini_login_ready"
_AUTH_FILE    = "gemini_auth.json"          # storage_state lưu cookies/localStorage

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

# ── Số Chrome song song (1-5) ─────────────────────────────────────────────────
_MAX_WORKERS   = 1          # mặc định 1, dùng set_max_workers() để đổi
_worker_lock   = threading.Lock()

def set_max_workers(n: int):
    """Đặt số Chrome tối đa chạy song song (1-5).
    Gọi từ Settings/UI trước khi dùng send_prompt_parallel().
    """
    global _MAX_WORKERS
    _MAX_WORKERS = max(1, min(5, int(n)))
    logger.info(f"[GeminiWeb] Số Chrome song song: {_MAX_WORKERS}")


# ══════════════════════════════════════════════════════════════════
# GeminiDriver — wrapper thay thế Selenium WebDriver
# ══════════════════════════════════════════════════════════════════

class GeminiDriver:
    """Giữ playwright instance + context + page.

    Backward-compat với code cũ dùng Selenium WebDriver:
        driver._autorecap_headless
        driver._autorecap_profile_dir
        driver.quit() / driver.close()
    """
    def __init__(self, pw, ctx, page, headless: bool, profile_dir: str):
        self._pw                   = pw
        self._ctx                  = ctx
        self._page                 = page
        self._autorecap_headless   = headless
        self._autorecap_profile_dir = profile_dir

    def close(self):
        try: self._ctx.close()
        except Exception: pass
        try: self._pw.stop()
        except Exception: pass

    def quit(self):
        self.close()


# ══════════════════════════════════════════════════════════════════
# Profile helpers
# ══════════════════════════════════════════════════════════════════

def _default_profile_dir() -> str:
    configured = str(os.environ.get("AUTORECAP_CHROME_PROFILE_DIR") or "").strip()
    if configured:
        return configured
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return str(pathlib.Path(appdata) / "AutoRecapPro" / "gemini_profile")

def _auth_file_path() -> str:
    return os.path.join(_default_profile_dir(), _AUTH_FILE)

def _profile_has_session(profile_dir=None) -> bool:
    profile_dir = str(profile_dir or _default_profile_dir())
    return os.path.isfile(os.path.join(profile_dir, _LOGIN_MARKER))

def is_profile_ready(profile_dir=None) -> bool:
    return _profile_has_session(profile_dir)

def _prefer_headless() -> bool:
    mode = str(os.environ.get("AUTORECAP_GEMINI_WEB_HEADLESS", "auto") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off", "visible", "show"}:
        return False
    if mode in {"1", "true", "yes", "on", "headless", "hidden"}:
        return True
    return _profile_has_session()

def driver_is_headless(driver) -> bool:
    return bool(getattr(driver, "_autorecap_headless", False))

def mark_profile_ready(driver_or_dir) -> None:
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

def _chrome_channel() -> Optional[str]:
    """Trả về 'chrome' nếu Google Chrome cài trên máy, None nếu dùng Chromium bundled."""
    import shutil
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    if any(os.path.isfile(p) for p in paths):
        return "chrome"
    if shutil.which("google-chrome") or shutil.which("chrome"):
        return "chrome"
    return None

def _launch_kwargs(headless: bool, extra_args=None) -> dict:
    args = list(_BROWSER_ARGS) + (extra_args or [])
    kw = dict(headless=headless, user_agent=_UA,
              viewport={"width": 1280, "height": 900}, args=args)
    ch = _chrome_channel()
    if ch:
        kw["channel"] = ch
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
        }''', text)
    page.wait_for_timeout(300)
    page.keyboard.press("End")
    page.keyboard.press("Space")
    page.wait_for_timeout(200)

def _send_message(page) -> bool:
    btn_clicked = False
    for sel in _SEND_SELS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                if not btn.is_disabled() and btn.get_attribute("aria-disabled") != "true":
                    btn.click(); btn_clicked = True; break
        except Exception:
            continue
    if not btn_clicked:
        page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    for _ in range(6):
        try:
            txt = page.evaluate(
                '''() => { const el = document.querySelector("[contenteditable='true']");
                           return el ? (el.innerText || "").trim() : null; }''')
            if txt is None or txt == "":
                return True
        except Exception:
            pass
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
    prev, stable = "", 0
    deadline = time.time() + timeout
    last_log  = 0.0
    page.wait_for_timeout(1000)
    while time.time() < deadline:
        cur = _get_last_response(page)
        if cur and cur == prev:
            stable += 1
            if stable >= 8:
                return cur
        else:
            stable = 0; prev = cur
        page.wait_for_timeout(500)
        now = time.time()
        if now - last_log >= 20:
            log(f"   ⏳ Đợi Gemini... còn ~{max(0,int(deadline-now))}s")
            last_log = now
    return prev or _get_last_response(page)


# ══════════════════════════════════════════════════════════════════
# Driver creation — 2 chế độ
# ══════════════════════════════════════════════════════════════════

def create_driver(headless: Optional[bool] = None, log=None) -> GeminiDriver:
    """Chế độ 1: persistent context (dùng khi login hoặc chạy 1 luồng).
    Profile bị lock → không thể mở song song.
    """
    if log is None: log = lambda m: logger.info(m)
    if headless is None: headless = _prefer_headless()

    sync_playwright = _find_playwright()
    profile_dir = _default_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)
    kw = _launch_kwargs(headless)
    last_exc = None

    for attempt in range(1, 4):
        if attempt > 1:
            log(f"   ⚠️ Profile bị khóa (lần {attempt}/3), chờ 3s...")
            time.sleep(3)
        pw = None
        try:
            pw = sync_playwright().start()
            ctx = pw.chromium.launch_persistent_context(profile_dir, **kw)
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            log(f"   🌐 Chrome {'ẩn' if headless else 'hiển thị'} đã khởi động")
            return GeminiDriver(pw, ctx, page, headless, profile_dir)
        except Exception as e:
            last_exc = e
            log(f"   ❌ Chrome thất bại (lần {attempt}): {str(e)[:200]}")
            try:
                if pw: pw.stop()
            except Exception: pass

    raise RuntimeError(f"Không khởi động Chrome: {last_exc}")


def _create_worker_driver(log=None) -> GeminiDriver:
    """Chế độ 2: browser.launch() + storage_state — dùng cho đa luồng.
    Mỗi luồng tạo Chrome hoàn toàn độc lập, không share profile, không lock.
    Cần auth.json đã được lưu từ lần login trước.
    """
    if log is None: log = lambda m: logger.info(m)

    auth_path = _auth_file_path()
    if not os.path.isfile(auth_path):
        raise RuntimeError(
            "Chưa có file đăng nhập (gemini_auth.json). "
            "Bấm 'Đăng nhập Gemini Web' để đăng nhập trước."
        )

    sync_playwright = _find_playwright()
    kw = _launch_kwargs(headless=True)
    channel = kw.pop("channel", None)

    pw = sync_playwright().start()
    try:
        launch_kw = dict(
            headless=True,
            args=kw.get("args", []),
        )
        if channel:
            launch_kw["channel"] = channel

        browser = pw.chromium.launch(**launch_kw)
        ctx = browser.new_context(
            storage_state=auth_path,
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()
        return GeminiDriver(pw, ctx, page, True, _default_profile_dir())
    except Exception:
        try: pw.stop()
        except Exception: pass
        raise


def create_auto_driver(log=None, force_visible: bool = False) -> GeminiDriver:
    if log is None: log = lambda m: logger.info(m)
    use_headless = False if force_visible else _prefer_headless()
    try:
        return create_driver(headless=use_headless, log=log)
    except Exception as exc:
        if not use_headless:
            raise
        fallback = str(os.environ.get("AUTORECAP_GEMINI_WEB_VISIBLE_FALLBACK","1")).strip().lower()
        if fallback in {"0","false","no","off"}:
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
    """Chờ user đăng nhập Gemini trong cửa sổ Chrome hiển thị.
    Sau khi phát hiện ô chat → lưu marker + storage_state (auth.json).
    """
    if log is None: log = lambda m: logger.info(m)

    page     = driver._page
    deadline = time.time() + max(15, int(timeout or 300))
    last_notice = 0.0

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
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""

        if "gemini.google.com" not in current_url.lower():
            time.sleep(1); continue

        try:
            for sel in _INPUT_SELS:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    # ── Lưu marker + storage_state ────────────────
                    mark_profile_ready(driver)
                    _save_auth_state(driver._ctx, log)
                    log("   ✅ Đăng nhập Gemini Web thành công, phiên đã được lưu")
                    return True
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("target closed","invalid session","no such window")):
                raise RuntimeError("Cửa sổ Chrome đăng nhập đã bị đóng") from exc

        now = time.time()
        if now - last_notice >= 15:
            log(f"   ⏳ Đang chờ đăng nhập Gemini Web... còn {max(0,int(deadline-now))}s")
            last_notice = now
        time.sleep(1)

    log("   ⚠️ Chưa xác nhận được ô chat Gemini trong thời gian chờ")
    return False


def _save_auth_state(ctx, log=None):
    """Lưu cookies/localStorage vào auth.json để dùng cho đa luồng."""
    if log is None: log = lambda m: None
    try:
        auth_path = _auth_file_path()
        os.makedirs(os.path.dirname(auth_path), exist_ok=True)
        ctx.storage_state(path=auth_path)
        log(f"   💾 Đã lưu phiên đăng nhập → {auth_path}")
    except Exception as e:
        log(f"   ⚠️ Không lưu được auth.json: {e}")


# ══════════════════════════════════════════════════════════════════
# Send prompt — 1 luồng
# ══════════════════════════════════════════════════════════════════

def send_prompt_to_gemini(
    prompt: str,
    timeout: int = 240,
    log: Optional[Callable] = None,
    driver=None,
    close_after: bool = False,
    navigate: bool = True,
) -> str:
    """Gửi 1 prompt vào Gemini Web, trả về response text."""
    if log is None: log = lambda m: logger.info(m)

    _own_driver = driver is None
    try:
        if _own_driver:
            driver = create_auto_driver(log=log)

        page = driver._page

        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""

        if bool(navigate) or "gemini.google.com" not in current_url.lower():
            log("   🌐 Mở Gemini Web...")
            try:
                page.goto(_GEMINI_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
            except Exception as e:
                log(f"   ⚠️ Navigate Gemini lỗi: {e}")
        else:
            log("   🌐 Dùng lại phiên Gemini hiện tại...")
            page.wait_for_timeout(500)

        inp = None
        for _ in range(30):
            inp = _find_input(page)
            if inp: break
            page.wait_for_timeout(500)

        if not inp:
            log("   ❌ Không tìm thấy ô chat. Hãy đăng nhập Gemini Web trước.")
            return ""

        mark_profile_ready(driver)

        try: inp.click()
        except Exception: pass
        _inject_text(page, prompt)

        sent = _send_message(page)
        if not sent:
            log("   ⚠️ Chưa xác nhận gửi, thử Enter lần cuối...")
            try: page.keyboard.press("Enter")
            except Exception: pass

        log("   🚀 Đã gửi prompt, đợi Gemini response...")
        response = _wait_response(page, timeout, log)
        log(f"   ✅ Response: {len(response)} ký tự" if response else "   ⚠️ Response rỗng")
        return response

    except Exception as e:
        log(f"   ❌ Gemini Web lỗi: {e}")
        return ""
    finally:
        if _own_driver and close_after and driver:
            try: driver.close()
            except Exception: pass


# ══════════════════════════════════════════════════════════════════
# Send prompt — đa luồng (MỚI)
# ══════════════════════════════════════════════════════════════════

def _worker_send(args):
    """Hàm chạy trong từng thread: tạo Chrome riêng, gửi 1 prompt, đóng lại."""
    idx, prompt, timeout, log = args
    prefix = f"[Chrome {idx+1}]"
    tagged_log = lambda m: log(f"{prefix} {m}")

    driver = None
    try:
        tagged_log("🚀 Khởi động Chrome...")
        driver = _create_worker_driver(log=tagged_log)
        result = send_prompt_to_gemini(
            prompt, timeout=timeout, log=tagged_log,
            driver=driver, close_after=False, navigate=True,
        )
        return idx, result
    except Exception as e:
        tagged_log(f"❌ Lỗi: {e}")
        return idx, ""
    finally:
        if driver:
            try: driver.close()
            except Exception: pass


def send_prompt_parallel(
    prompts: List[str],
    timeout: int = 240,
    log: Optional[Callable] = None,
    max_workers: Optional[int] = None,
) -> List[str]:
    """Gửi nhiều prompt song song, mỗi prompt 1 Chrome riêng.

    Args:
        prompts:     Danh sách prompt cần gửi
        timeout:     Timeout mỗi prompt (giây)
        log:         Callback log
        max_workers: Số Chrome tối đa (None = dùng set_max_workers())

    Returns:
        Danh sách response tương ứng với prompts (cùng thứ tự)

    Ví dụ:
        set_max_workers(3)
        results = send_prompt_parallel(["prompt A", "prompt B", "prompt C"])
    """
    if log is None: log = lambda m: logger.info(m)
    if not prompts: return []

    n = max_workers or _MAX_WORKERS
    n = max(1, min(5, int(n)))

    # Kiểm tra auth.json trước khi spawn threads
    if not os.path.isfile(_auth_file_path()):
        log("❌ Chưa có file đăng nhập (gemini_auth.json). Bấm 'Đăng nhập Gemini Web' trước.")
        return [""] * len(prompts)

    log(f"🔀 Gửi {len(prompts)} prompt với {n} Chrome song song...")

    args_list = [(i, p, timeout, log) for i, p in enumerate(prompts)]
    results   = [""] * len(prompts)

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures = {pool.submit(_worker_send, a): a[0] for a in args_list}
        for fut in concurrent.futures.as_completed(futures):
            try:
                idx, resp = fut.result()
                results[idx] = resp
            except Exception as e:
                log(f"   ❌ Thread lỗi: {e}")

    log(f"✅ Hoàn tất {len(prompts)} prompt")
    return results


# ══════════════════════════════════════════════════════════════════
# Backward-compat
# ══════════════════════════════════════════════════════════════════

def is_selenium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return True
    except ImportError:
        return False
