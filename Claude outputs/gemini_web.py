"""
AutoRecapPro V2 - Gemini Web Automation
========================================
Dùng Selenium điều khiển Google Chrome tự động:
  1. Mở gemini.google.com/app
  2. Paste prompt vào ô chat
  3. Gửi và chờ kết quả
  4. Trả về response text

Không cần API key — dùng session đăng nhập trong Chrome profile riêng.
Lần đầu chạy: đăng nhập Gmail trong cửa sổ Chrome mở ra, sau đó app tự nhớ.
"""

import os
import time
import logging
import re
import shutil
from typing import Optional, Callable

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://gemini.google.com/app"
_LOGIN_MARKER = ".gemini_login_ready"

_INPUT_SELECTORS = [
    "rich-textarea div[contenteditable='true']",
    "div[contenteditable='true']",
    "p[data-placeholder]",
    "[aria-label*='Hỏi']",
    "[aria-label*='Ask']",
    "[aria-label*='Message']",
    "textarea",
    ".ql-editor",
]

_RESPONSE_DONE_INDICATORS = [
    # Nút copy response xuất hiện = response xong
    "button[aria-label*='Copy']",
    "button[aria-label*='Sao chép']",
    # Nút thumbs up/down xuất hiện = response xong
    "thumbs-up-down-buttons",
    ".response-actions",
]


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _default_profile_dir() -> str:
    """Return the stable Chrome profile used by all Gemini Web calls."""
    import pathlib

    configured = str(os.environ.get("AUTORECAP_CHROME_PROFILE_DIR") or "").strip()
    if configured:
        return configured

    # Dùng %APPDATA% (ổn định, không bị Windows xóa như Temp)
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return str(pathlib.Path(appdata) / "AutoRecapPro" / "gemini_profile")


def _profile_has_session(profile_dir: Optional[str] = None) -> bool:
    """Best-effort check for a previous successful Gemini/Google session."""
    profile_dir = str(profile_dir or _default_profile_dir())
    if os.path.isfile(os.path.join(profile_dir, _LOGIN_MARKER)):
        return True

    # Preserve compatibility with profiles created by older app versions.
    for relative in (
        os.path.join("Default", "Network", "Cookies"),
        os.path.join("Default", "Cookies"),
    ):
        path = os.path.join(profile_dir, relative)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 4096:
                return True
        except OSError:
            continue
    return False


def is_profile_ready(profile_dir: Optional[str] = None) -> bool:
    """Public status helper used by the GUI login control."""
    return _profile_has_session(profile_dir)


def _prefer_headless() -> bool:
    """Resolve visible/headless mode from config and stored login state."""
    mode = str(os.environ.get("AUTORECAP_GEMINI_WEB_HEADLESS", "auto") or "auto").strip().lower()
    if mode in {"0", "false", "no", "off", "visible", "show"}:
        return False
    if mode in {"1", "true", "yes", "on", "headless", "hidden"}:
        return True
    return _profile_has_session()


def driver_is_headless(driver) -> bool:
    return bool(getattr(driver, "_autorecap_headless", False))


def mark_profile_ready(driver) -> None:
    """Remember that this profile reached a usable Gemini composer."""
    profile_dir = str(getattr(driver, "_autorecap_profile_dir", "") or "")
    if not profile_dir:
        return
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(os.path.join(profile_dir, _LOGIN_MARKER), "w", encoding="ascii") as handle:
            handle.write(str(int(time.time())))
    except OSError:
        pass


def wait_for_gemini_login(
    driver,
    timeout: int = 300,
    log: Optional[Callable] = None,
) -> bool:
    """Wait until the visible Gemini page exposes a usable chat composer.

    This method never sends a prompt. It is intended for the explicit login
    button in the desktop GUI so users can prepare the persistent web profile
    before starting a long pipeline.
    """
    if log is None:
        log = lambda msg: logger.info(msg)

    _, By, _, _, _ = _find_selenium()
    deadline = time.time() + max(15, int(timeout or 300))
    last_notice = 0.0

    try:
        current_url = str(driver.current_url or "")
    except Exception:
        current_url = ""
    if "gemini.google.com" not in current_url.lower():
        driver.get(_GEMINI_URL)

    log("   Đăng nhập Google trong cửa sổ Chrome. App đang chờ ô chat Gemini...")
    while time.time() < deadline:
        try:
            for selector in _INPUT_SELECTORS:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    try:
                        if element.is_displayed() and element.is_enabled():
                            mark_profile_ready(driver)
                            log("   ✅ Đăng nhập Gemini Web thành công, phiên đã được lưu")
                            return True
                    except Exception:
                        continue
        except Exception as exc:
            # A closed browser should fail immediately instead of waiting five minutes.
            message = str(exc).lower()
            if "invalid session" in message or "no such window" in message:
                raise RuntimeError("Cửa sổ Chrome đăng nhập đã bị đóng") from exc

        now = time.time()
        if now - last_notice >= 15:
            remaining = max(0, int(deadline - now))
            log(f"   ⏳ Đang chờ đăng nhập Gemini Web... còn {remaining}s")
            last_notice = now
        time.sleep(1)

    log("   ⚠️ Chưa xác nhận được ô chat Gemini trong thời gian chờ")
    return False

def _find_selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        return webdriver, By, Keys, WebDriverWait, EC
    except ImportError:
        raise ImportError("Thiếu selenium. Cài: pip install selenium==4.21.0")


def _get_browser_binary() -> str:
    """Ưu tiên Google Chrome (tương thích chromedriver chuẩn)."""
    import shutil
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        # Cốc Cốc cuối cùng — cần coccocdriver riêng, thường lỗi
        r"C:\Program Files\CocCoc\Browser\Application\browser.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _is_valid_exe(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    if os.path.getsize(path) < 1_000_000:
        return False
    try:
        with open(path, 'rb') as f:
            return f.read(2) == b'MZ'
    except Exception:
        return False


def _get_chromedriver_major(exe_path: str) -> str:
    """Lấy major version của chromedriver.exe. Trả về "" nếu thất bại."""
    import subprocess as _sp
    try:
        r = _sp.run([exe_path, "--version"], capture_output=True, text=True, timeout=5)
        m = re.search(r'ChromeDriver (\d+)\.', r.stdout + r.stderr)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _find_valid_chromedriver(log=None) -> str:
    """Tìm chromedriver.exe hợp lệ và khớp version với Chrome đã cài."""
    import glob as _glob, shutil

    chrome_major = _get_chrome_major_version()

    def _version_ok(exe_path: str) -> bool:
        if not _is_valid_exe(exe_path):
            return False
        if not chrome_major:
            return True  # không biết Chrome version, dùng luôn
        cd_major = _get_chromedriver_major(exe_path)
        return not cd_major or cd_major == chrome_major

    # 1. Auto-installed location
    auto_path = os.path.expanduser(
        r"~\.wdm\drivers\chromedriver\win64\auto\chromedriver.exe"
    )
    if _version_ok(auto_path):
        return auto_path

    # 2. PATH
    cd = shutil.which("chromedriver")
    if cd and _version_ok(cd):
        return cd

    # 3. webdriver-manager cache — scan toàn bộ, ưu tiên khớp version
    wdm_base = os.path.expanduser(r"~\.wdm\drivers\chromedriver")
    if os.path.isdir(wdm_base):
        cands = sorted(
            _glob.glob(os.path.join(wdm_base, "**", "chromedriver.exe"), recursive=True),
            key=os.path.getmtime, reverse=True
        )
        for c in cands:
            if _version_ok(c):
                return c

    return ""


def _get_chrome_major_version() -> str:
    """Lấy major version Chrome đang cài. Trả về chuỗi số hoặc ""."""
    # 1. Từ thư mục cạnh chrome.exe (đáng tin cậy nhất)
    binary = _get_browser_binary()
    if binary and "CocCoc" not in binary:
        try:
            app_dir = os.path.dirname(binary)
            ver_dirs = [d for d in os.listdir(app_dir)
                        if re.match(r'^\d+\.\d+\.\d+\.\d+$', d)]
            if ver_dirs:
                best = max(ver_dirs, key=lambda v: [int(x) for x in v.split(".")])
                return best.split(".")[0]
        except Exception:
            pass

    # 2. Registry Windows
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for path in (
                r"Software\Google\Chrome\BLBeacon",
                r"Software\Chromium\BLBeacon",
            ):
                try:
                    with winreg.OpenKey(root, path) as k:
                        ver, _ = winreg.QueryValueEx(k, "version")
                        m = re.match(r'^(\d+)\.', str(ver))
                        if m:
                            return m.group(1)
                except Exception:
                    continue
    except ImportError:
        pass

    return ""


def _auto_install_chromedriver(log=None) -> str:
    """Tải chromedriver từ Chrome for Testing. Trả về path hoặc ""."""
    import urllib.request, zipfile, json as _json

    if log is None:
        log = lambda msg: logger.info(msg)

    # Lấy major version Chrome
    major = _get_chrome_major_version()
    if not major:
        # Fallback: lấy Stable version mới nhất từ API
        try:
            with urllib.request.urlopen(
                "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json",
                timeout=15,
            ) as r:
                lkg = _json.loads(r.read())
                major = lkg["channels"]["Stable"]["version"].split(".")[0]
                log(f"   ℹ️ Không phát hiện Chrome local, dùng Stable: {major}")
        except Exception:
            log("   ❌ Không xác định được version Chrome")
            return ""
    else:
        log(f"   🔍 Chrome version phát hiện: {major}")

    dest_dir = os.path.expanduser(r"~\.wdm\drivers\chromedriver\win64\auto")
    dest_exe = os.path.join(dest_dir, "chromedriver.exe")
    os.makedirs(dest_dir, exist_ok=True)

    log(f"   📦 Tải chromedriver cho Chrome {major}...")
    try:
        url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = _json.loads(r.read())
    except Exception as e:
        log(f"   ❌ Lấy danh sách chromedriver thất bại: {e}")
        return ""

    candidates = []
    for v in data.get("versions", []):
        ver = v.get("version", "")
        if not ver.startswith(major + "."):
            continue
        for item in v.get("downloads", {}).get("chromedriver", []):
            if item.get("platform") in ("win64", "win32"):
                candidates.append((ver, item["url"], item["platform"]))

    candidates.sort(key=lambda x: (
        [int(p) for p in x[0].split(".")],
        1 if x[2] == "win64" else 0
    ), reverse=True)

    if not candidates:
        log(f"   ❌ Không có chromedriver cho Chrome {major}")
        return ""

    ver, dl_url, platform = candidates[0]
    log(f"   ⬇️  {ver} ({platform})")
    try:
        zip_path = dest_exe.replace(".exe", ".zip")
        urllib.request.urlretrieve(dl_url, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith("chromedriver.exe"):
                    with zf.open(name) as src, open(dest_exe, 'wb') as dst:
                        dst.write(src.read())
                    break
        os.unlink(zip_path)
        if _is_valid_exe(dest_exe):
            log(f"   ✅ chromedriver: {dest_exe}")
            return dest_exe
    except Exception as e:
        log(f"   ❌ Cài chromedriver thất bại: {e}")
    return ""


# ─────────────────────────────────────────────────────────────────
# Driver creation
# ─────────────────────────────────────────────────────────────────

def create_driver(headless: Optional[bool] = None, log=None):
    """Tạo Chrome WebDriver với profile riêng (không đụng profile user).

    Lần đầu: sẽ mở Chrome trống → user đăng nhập Google/Gemini một lần.
    Những lần sau: profile đã lưu, không cần đăng nhập lại.
    """
    if log is None:
        log = lambda msg: logger.info(msg)

    webdriver, By, Keys, WebDriverWait, EC = _find_selenium()
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service as ChromeService
    import tempfile, pathlib

    if headless is None:
        headless = _prefer_headless()
    headless = bool(headless)

    def _profile_candidates():
        base_profile = _default_profile_dir()
        fallback_profile = str(
            pathlib.Path(tempfile.gettempdir())
            / f"autorecap_chrome_profile_fallback_{os.getpid()}_{int(time.time())}"
        )
        return [base_profile, fallback_profile]

    def _build_options(sel_profile: str):
        options = Options()

        # Profile riêng của AutoRecap — không đụng Chrome profile user
        options.add_argument(f"--user-data-dir={sel_profile}")
        options.add_argument("--profile-directory=Default")

        binary = _get_browser_binary()
        if binary and "CocCoc" not in binary:
            options.binary_location = binary
            log(f"   🌐 Chrome: {os.path.basename(binary)}")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--log-level=3")
        options.add_argument("--silent")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-component-update")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--remote-allow-origins=*")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)
        if headless:
            options.add_argument("--headless=new")
        return options

    # Tìm / tải chromedriver
    cd_path = _find_valid_chromedriver(log=log)
    if not cd_path:
        log("   📦 Tải chromedriver tự động...")
        cd_path = _auto_install_chromedriver(log=log)

    if cd_path:
        log(f"   🔧 ChromeDriver: {os.path.basename(cd_path)}")
    else:
        log("   🔧 ChromeDriver: dùng selenium-manager tự động")

    last_exc = None
    for attempt, sel_profile in enumerate(_profile_candidates(), 1):
        try:
            os.makedirs(sel_profile, exist_ok=True)
        except Exception:
            pass
        try:
            if attempt > 1:
                log(
                    "   ⚠️ Profile Chrome chính có thể đang bị khóa/hỏng -> "
                    f"thử profile dự phòng: {sel_profile}"
                )
            options = _build_options(sel_profile)
            if cd_path:
                service = ChromeService(cd_path, service_args=["--silent"], log_output=os.devnull)
            else:
                # Để Selenium 4.6+ tự tìm/tải chromedriver qua selenium-manager
                service = ChromeService(service_args=["--silent"], log_output=os.devnull)
            driver = webdriver.Chrome(service=service, options=options)
            driver._autorecap_headless = headless
            driver._autorecap_profile_dir = sel_profile
            # Ẩn dấu hiệu automation
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            try:
                driver.get(_GEMINI_URL)
                time.sleep(2)
            except Exception as nav_exc:
                log(f"   ⚠️ Chrome mở được nhưng chưa vào Gemini: {nav_exc}")
            log("   🔒 Gemini Web chạy ẩn" if headless else "   👁️ Gemini Web đang hiển thị để đăng nhập/thao tác")
            return driver
        except Exception as e:
            last_exc = e
            log(f"   ❌ Chrome session lỗi lần {attempt}: {str(e)[:240]}")
            if attempt == 1:
                time.sleep(2)
                continue
            try:
                if sel_profile and "fallback_" in sel_profile and os.path.isdir(sel_profile):
                    shutil.rmtree(sel_profile, ignore_errors=True)
            except Exception:
                pass
    raise RuntimeError(f"Không khởi động Chrome: {last_exc}")


def create_auto_driver(log=None, force_visible: bool = False):
    """Create the preferred driver and fall back to visible Chrome if needed."""
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
        log(f"   ⚠️ Chrome ẩn không khởi động được: {str(exc)[:180]}")
        log("   👁️ Chuyển sang Chrome hiển thị để khôi phục phiên đăng nhập...")
        return create_driver(headless=False, log=log)


# ─────────────────────────────────────────────────────────────────
# Core: Send prompt & get response
# ─────────────────────────────────────────────────────────────────

def send_prompt_to_gemini(
    prompt: str,
    timeout: int = 240,
    log: Optional[Callable] = None,
    driver=None,
    close_after: bool = False,
    navigate: bool = True,
) -> str:
    """Gửi prompt vào Gemini Web và trả về response text.

    Args:
        prompt:      Nội dung prompt
        timeout:     Giây đợi tối đa response
        log:         Progress callback
        driver:      WebDriver đã tạo sẵn (None = tạo mới)
        close_after: True = đóng browser sau khi xong

    Returns:
        Response text, "" nếu thất bại.
    """
    if log is None:
        log = lambda msg: logger.info(msg)

    webdriver_mod, By, Keys, WebDriverWait, EC = _find_selenium()
    from selenium.common.exceptions import TimeoutException, WebDriverException

    _own_driver = driver is None
    try:
        if _own_driver:
            driver = create_auto_driver(log=log)

        current_url = ""
        try:
            current_url = str(driver.current_url or "")
        except Exception:
            current_url = ""

        # Reused Chrome sessions can be left on New Tab/Google after a user click
        # or a previous failed batch. Always bring the tab back to Gemini first.
        should_navigate = bool(navigate) or ("gemini.google.com" not in current_url.lower())
        log("   🌐 Mở Gemini Web..." if should_navigate else "   🌐 Dùng lại phiên Gemini Web hiện tại...")
        if should_navigate:
            driver.get(_GEMINI_URL)
            time.sleep(4)  # đợi page load
            try:
                landed_url = str(driver.current_url or "")
            except Exception:
                landed_url = ""
            if "gemini.google.com" not in landed_url.lower():
                log(f"   ⚠️ Chrome chưa ở Gemini ({landed_url or 'unknown'}), ép mở lại Gemini...")
                try:
                    driver.execute_script("window.location.href = arguments[0];", _GEMINI_URL)
                    time.sleep(4)
                except Exception:
                    driver.get(_GEMINI_URL)
                    time.sleep(4)
        else:
            time.sleep(1)

        # ── Tìm ô input ──────────────────────────────────────────
        input_el = None
        for selector in _INPUT_SELECTORS:
            try:
                input_el = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                if input_el and input_el.is_displayed():
                    break
                input_el = None
            except TimeoutException:
                continue

        if not input_el:
            # Fallback XPath
            try:
                input_el = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//div[@contenteditable='true']")
                    )
                )
            except Exception:
                pass

        if not input_el:
            log("   ❌ Không tìm thấy ô chat Gemini. Đăng nhập Gmail trong Chrome trước.")
            log("   💡 Chrome đang mở, hãy đăng nhập tại gemini.google.com rồi chạy lại.")
            return ""

        mark_profile_ready(driver)

        # ── Click + Paste ────────────────────────────────────────
        input_el.click()
        time.sleep(0.3)

        from selenium.webdriver.common.action_chains import ActionChains

        def _focus_input():
            """Focus Gemini editor even when Selenium found a child element."""
            try:
                driver.execute_script("""
                    const el = arguments[0];
                    const host = el && (el.closest('rich-textarea') || el.closest('[contenteditable="true"]') || el);
                    if (host) {
                        host.scrollIntoView({block: 'center', inline: 'center'});
                        host.click();
                        if (host.focus) host.focus();
                    }
                """, input_el)
                time.sleep(0.15)
            except Exception:
                try:
                    input_el.click()
                    time.sleep(0.15)
                except Exception:
                    pass

        def _input_text_value():
            """Read editor text from active element, ancestors, and Gemini editor hosts."""
            try:
                return str(driver.execute_script("""
                    const found = [];
                    const add = (el) => {
                        if (!el) return;
                        const val = (el.value || el.innerText || el.textContent || '').trim();
                        if (val) found.push(val);
                    };

                    let el = document.activeElement;
                    for (let i = 0; el && i < 6; i++, el = el.parentElement) add(el);

                    add(arguments[0]);
                    let host = arguments[0] && (
                        arguments[0].closest('rich-textarea') ||
                        arguments[0].closest('[contenteditable="true"]')
                    );
                    add(host);

                    document.querySelectorAll(
                        'rich-textarea, rich-textarea div[contenteditable="true"], div[contenteditable="true"], textarea, p[data-placeholder]'
                    ).forEach(add);

                    found.sort((a, b) => b.length - a.length);
                    return found[0] || '';
                """, input_el) or "")
            except Exception:
                return ""

        def _looks_entered(current: str, expected: str) -> bool:
            def norm(value: str) -> str:
                return re.sub(r"\s+", " ", (value or "")).strip()

            current_n = norm(current)
            expected_n = norm(expected)
            if not current_n or not expected_n:
                return False
            if expected_n[:80] in current_n:
                return True
            if len(expected_n) >= 160:
                return expected_n[:60] in current_n and expected_n[-60:] in current_n
            return expected_n in current_n

        def _insert_text_with_cdp(value: str) -> str:
            _focus_input()
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
                time.sleep(0.2)
            except Exception:
                pass
            # Chrome DevTools Protocol inserts real text into focused contenteditable/input
            # without using clipboard and without touching blocked innerHTML APIs.
            chunk_size = 4000
            for offset in range(0, len(value), chunk_size):
                driver.execute_cdp_cmd("Input.insertText", {"text": value[offset:offset + chunk_size]})
                time.sleep(0.05)
            time.sleep(0.5)
            return _input_text_value()

        def _insert_text_with_exec_command(value: str) -> str:
            _focus_input()
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
                time.sleep(0.2)
            except Exception:
                pass
            chunk_size = 3000
            for offset in range(0, len(value), chunk_size):
                driver.execute_script("""
                    const text = arguments[0];
                    const el = document.activeElement;
                    if (el && el.focus) el.focus();
                    document.execCommand('insertText', false, text);
                """, value[offset:offset + chunk_size])
                time.sleep(0.05)
            time.sleep(0.5)
            return _input_text_value()

        def _insert_text_with_clipboard(value: str) -> str:
            # Last resort: set clipboard ourselves, paste, then verify before send.
            try:
                import tkinter as _tk
                root = _tk.Tk()
                root.withdraw()
                root.clipboard_clear()
                root.clipboard_append(value)
                root.update()
                root.destroy()
            except Exception as clip_exc:
                raise RuntimeError(f"clipboard unavailable: {clip_exc}") from clip_exc

            _focus_input()
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
                time.sleep(0.2)
            except Exception:
                pass
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(0.8)
            return _input_text_value()

        def _insert_text_with_keys(value: str) -> str:
            _focus_input()
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
                time.sleep(0.2)
            except Exception:
                pass
            chunk_size = 800
            for offset in range(0, len(value), chunk_size):
                input_el.send_keys(value[offset:offset + chunk_size])
                time.sleep(0.03)
            time.sleep(0.5)
            return _input_text_value()

        pasted = False
        try:
            current = _insert_text_with_cdp(prompt)
            pasted = _looks_entered(current, prompt)
            if pasted:
                log("   Prompt entered by Chrome DevTools input")
        except Exception as cdp_exc:
            log(f"   CDP input failed, using keyboard input: {cdp_exc}")

        if not pasted:
            try:
                current = _insert_text_with_exec_command(prompt)
                pasted = _looks_entered(current, prompt)
                if pasted:
                    log("   Prompt entered by browser insertText")
            except Exception as exec_exc:
                log(f"   Browser insertText failed: {exec_exc}")

        if not pasted:
            try:
                current = _insert_text_with_keys(prompt)
                pasted = _looks_entered(current, prompt)
                if pasted:
                    log("   Prompt entered by keyboard input")
            except Exception as keys_exc:
                log(f"   Keyboard input failed: {keys_exc}")

        if not pasted:
            try:
                current = _insert_text_with_clipboard(prompt)
                pasted = _looks_entered(current, prompt)
                if pasted:
                    log("   Prompt entered by verified clipboard paste")
            except Exception as clip_exc:
                log(f"   Clipboard paste failed: {clip_exc}")

        if not pasted:
            raise RuntimeError("Could not enter prompt into Gemini; stopped to avoid sending stale clipboard text.")


        # Thử click nút Send trước
        sent = False
        for btn_sel in [
            "button[aria-label*='send' i]",
            "button[aria-label*='gửi' i]",
            "button[data-mat-icon-name='send']",
            "button.send-button",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, btn_sel)
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    sent = True
                    break
            except Exception:
                pass

        # Chụp baseline TRƯỚC khi gửi để tránh race condition
        pre_send_baseline = _get_page_text_len(driver)

        if not sent:
            input_el.send_keys(Keys.RETURN)

        log("   🚀 Đã gửi prompt, đợi Gemini response...")

        # ── Đợi response hoàn thành ─────────────────────────────
        response = _wait_and_get_response(driver, By, WebDriverWait, EC, timeout, log, baseline_len=pre_send_baseline)
        if not response:
            # Gemini Web may update the response DOM a few seconds after the
            # loading indicator disappears, especially across many batch calls.
            time.sleep(5)
            response = _extract_last_response(driver, By)
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
                driver.quit()
            except Exception:
                pass


def _get_page_text_len(driver) -> int:
    """Lấy độ dài toàn bộ text hiển thị trên trang (không phụ thuộc vào selector)."""
    try:
        text = driver.execute_script("return document.body.innerText || ''")
        return len(str(text or ""))
    except Exception:
        return 0


def _wait_and_get_response(driver, By, WebDriverWait, EC, timeout: int, log, old_turn_count: int = 0, baseline_len: int = -1) -> str:
    """Đợi Gemini hoàn thành generate rồi trả về text.

    baseline_len: độ dài text trang trước khi gửi prompt (chụp từ send_prompt_to_gemini).
                  Nếu không truyền (-1), sẽ tự chụp tại đây (kém chính xác hơn).
    """
    # Dùng baseline được truyền vào; nếu không có thì tự chụp
    if baseline_len < 0:
        baseline_len = _get_page_text_len(driver)

    # Đợi tối thiểu 1s để Gemini bắt đầu xử lý
    time.sleep(1.0)

    deadline = time.time() + timeout
    prev_text = ""
    stable_count = 0
    new_response_started = False
    last_page_len_check = 0  # tracking để phát hiện page không tăng

    while time.time() < deadline:
        elapsed = int(time.time() - (deadline - timeout))

        if not new_response_started:
            current_len = _get_page_text_len(driver)
            delta = current_len - baseline_len

            # Khi input box bị xóa sau khi gửi → trang co lại (delta âm)
            # Reset baseline về current_len để chờ tăng từ đây
            if delta < -100:
                log(f"   🔄 Input box đã gửi xong, trang co từ {baseline_len} → {current_len}. Reset baseline.")
                baseline_len = current_len
                delta = 0

            # Coi là "response mới bắt đầu" khi trang tăng đáng kể (>50 ký tự)
            if delta > 50:
                new_response_started = True
                log(f"   ✅ Gemini bắt đầu respond (delta +{delta} ký tự)")
            else:
                if elapsed > 0 and elapsed % 10 == 0:
                    log(f"   ⏳ Chờ Gemini bắt đầu response mới... {elapsed}s | page: {current_len} | baseline: {baseline_len} | delta: {delta}")
                time.sleep(0.5)
                continue

        # Response đã bắt đầu — theo dõi text cho đến khi stable
        current_text = _extract_last_response(driver, By)
        is_loading = _is_generating(driver, By)

        # DEBUG: nếu response đã bắt đầu (page tăng) nhưng selector không match,
        # log ra để biết DOM thực sự có gì
        if not current_text and elapsed == 5:
            try:
                debug_info = driver.execute_script("""
                    var tags = {};
                    document.querySelectorAll('*').forEach(function(el) {
                        var t = el.tagName.toLowerCase();
                        tags[t] = (tags[t] || 0) + 1;
                    });
                    // Lấy 20 tag phổ biến nhất
                    var sorted = Object.entries(tags).sort((a,b) => b[1]-a[1]).slice(0,20);
                    return sorted.map(x => x[0]+':'+x[1]).join(', ');
                """)
                log(f"   🔍 DOM tags: {debug_info}")
                # Thử lấy text theo cách đơn giản nhất
                page_text = driver.execute_script("return document.body.innerText")
                if page_text:
                    log(f"   🔍 Page text sample (last 300): ...{str(page_text)[-300:]}")
            except Exception:
                pass

        if current_text and not is_loading:
            if current_text == prev_text:
                stable_count += 1
                if stable_count >= 3:
                    return current_text
            else:
                stable_count = 0
                prev_text = current_text
        elif not is_loading and prev_text:
            return prev_text

        if elapsed > 0 and elapsed % 20 == 0:
            log(f"   ⏳ Đợi Gemini... {elapsed}s/{timeout}s")
        time.sleep(1)

    # Timeout — trả về bất cứ gì có được
    return prev_text or _extract_last_response(driver, By)


def _is_generating(driver, By) -> bool:
    """True nếu Gemini đang generate (có loading indicator)."""
    loading_selectors = [
        "[aria-label*='Loading']",
        "[aria-label*='Đang tải']",
        ".loading-indicator",
        "thinking-indicator",
        "mat-progress-bar",
        "[data-is-loading='true']",
        # Nút send bị disable = đang generate
    ]
    for sel in loading_selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if any(e.is_displayed() for e in els):
                return True
        except Exception:
            pass

    # Kiểm tra nút send bị disable
    try:
        send_btns = driver.find_elements(By.CSS_SELECTOR,
            "button[aria-label*='send' i], button[aria-label*='gửi' i]")
        all_disabled = send_btns and all(not b.is_enabled() for b in send_btns)
        if all_disabled:
            return True
    except Exception:
        pass

    return False


def _extract_last_response(driver, By) -> str:
    """Lấy text của response cuối cùng từ Gemini."""
    selectors = [
        # Response content — thử nhiều selector, Gemini thay đổi DOM theo thời gian
        "message-content model-response-text",
        "message-content .markdown",
        "model-response .response-content",
        ".response-container-content",
        "[data-message-author-role='model']",
        "model-response",
        ".chat-history .message:last-child",
        # Shadow DOM / Web Component phổ biến của Gemini
        "response-container",
        "chat-message:last-of-type",
        ".conversation-container > *:last-child",
    ]
    for selector in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            if els:
                text = els[-1].text.strip()
                if len(text) > 30:
                    return text
        except Exception:
            continue

    # JavaScript fallback — thử nhiều cách, kể cả shadow DOM
    try:
        js_text = driver.execute_script("""
            // Thử selector thông thường
            var selectors = [
                'model-response', '[data-message-author-role="model"]',
                'message-content', 'response-container',
                '.response-container-content', '.model-response-text'
            ];
            for (var sel of selectors) {
                var els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    var el = els[els.length - 1];
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t.length > 30) return t;
                }
            }
            // Fallback: lấy toàn bộ innerText trang, bỏ phần trùng với prompt
            return '';
        """)
        if js_text and len(str(js_text).strip()) > 30:
            return str(js_text).strip()
    except Exception:
        pass

    # Last resort: đọc toàn bộ page text (dùng cho stability check)
    # Không trả về ở đây vì text quá dài và bao gồm cả UI, prompt, v.v.
    return ""


# ─────────────────────────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────────────────────────

def is_selenium_available() -> bool:
    try:
        import selenium
        return True
    except ImportError:
        return False


def install_selenium_if_needed(log=None) -> bool:
    if log is None:
        log = print
    if is_selenium_available():
        return True
    log("   📦 Cài selenium...")
    import subprocess, sys
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "selenium==4.21.0", "--quiet", "--no-warn-script-location"
        ])
        log("   ✅ Đã cài selenium")
        return True
    except Exception as e:
        log(f"   ❌ Cài selenium thất bại: {e}")
        return False
