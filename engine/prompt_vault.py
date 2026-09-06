"""
AutoRecapPro V2 - Prompt Vault
===============================
Mã hóa và giải mã các prompt AI quan trọng khi runtime.
Khi build Nuitka → code compile sang C, cộng thêm XOR obfuscation.
"""

import base64
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Obfuscation key — đổi trước khi build ────────────────────────────────────
_VAULT_KEY = b"ARP2_VAULT_2026_SECRET_KEY_CHANGE_BEFORE_BUILD"

def _xor(data: bytes, key: bytes) -> bytes:
    kl = len(key)
    return bytes(b ^ key[i % kl] for i, b in enumerate(data))

def _enc(text: str) -> str:
    return base64.b64encode(_xor(text.encode("utf-8"), _VAULT_KEY)).decode("ascii")

def _dec(token: str) -> str:
    try:
        return _xor(base64.b64decode(token.encode("ascii")), _VAULT_KEY).decode("utf-8")
    except Exception:
        return token


# ── Anti-tamper: kiểm tra integrity của license module ───────────────────────
def _check_integrity() -> bool:
    """Kiểm tra license_guard chưa bị patch/bypass."""
    try:
        from engine.license_guard import check_license, SUPABASE_URL, SUPABASE_KEY
        # Kiểm tra Supabase URL không bị xóa
        if not SUPABASE_URL or "supabase" not in SUPABASE_URL.lower():
            return False
        if not SUPABASE_KEY or len(str(SUPABASE_KEY)) < 40:
            return False
        if not callable(check_license):
            return False

        # Khi chạy source dev, có thể kiểm tra source để bắt monkey patch thô.
        # Khi đã build bằng Nuitka, inspect.getsource thường không còn hoạt động;
        # nếu vẫn ép check source thì EXE tự thoát ngay lúc mở.
        is_compiled = (
            "__compiled__" in globals()
            or getattr(sys, "frozen", False)
            or hasattr(sys, "__compiled__")
            or getattr(sys, "nuitka_version", None) is not None
            or not hasattr(check_license, "__code__")  # compiled = no __code__
        )
        if not is_compiled:
            try:
                import inspect
                src = inspect.getsource(check_license)
                if len(src) < 200 or "LicenseResult" not in src:
                    return False
            except (OSError, TypeError):
                pass  # Compiled env — không check source
        return True
    except Exception:
        return False  # Nếu không load được → coi là tampered


def verify_runtime_integrity():
    """Gọi khi app khởi động — tạm thời disable để debug."""
    pass  # TODO: re-enable sau khi confirm exe chạy OK


# ── Prompt Vault ──────────────────────────────────────────────────────────────
class PromptVault:
    """Giải mã prompt khi runtime — không lưu plaintext trong binary."""

    _TOKENS = {
        "recap_system": "EgYJfhoJCBF2dC9AX1RTLCAsLDwkOAAyKiwrNiokESoqKSsgGT03Jj4yXxoYHQ0XD3weGwRvbAY6RFlXQX8KKjYGMDY6ayYxKjqL6yBnKzgqLKf01TVVFBoADwF7cgTT5OdhISU6cxJC8YN/kPhvciaX7GsrMb74wzFuICy++epmLDqG/Sx1JyuC8bPrrzZ2OTAheH9cWPSGMTRlKDqG4DEsZSk3gPsvKWeBzqP/5yZyNj4rdTqt//ByJFq+7OwhYl4NemlmfhJpZQCR5yF/PaTi9CJoMSam//wraWYhs/7OK3Ukrf7gMTgSMz+C/yJ0K9OLl1Vkcyii6dI9fykpNjwoaCKN9GUyo/7Zb5H4c2IyIK3+4jtwRjeV7DYkeH9A0YmlNnMugPsqdCwqKz5/IKn77SktfzEkM2FYAxABABp2ZAmR8Fw3doXEre/GXFcS8s4yKyRyIT2+8MA3fzEpbW6E+H8sIi6L+yR/IbTy6yVhPzFcN3YstPfFNh4Q9qe+6NwtNWU3meplNzeA6i9uMaTl7zFqbzqk5e8gaT0xoOjzEjSV6DpsJz5cVxLyzjyk+fMrdCsipOPgM2g1JiIqcUgHCgARDgAEGhsBEQ0TahI8t/v2IjxwQdGJh384LKLpwjp/JS2a8y1oNSam//o7ZWtxcobiYjsuJIDoM39WLTcsNGx5YRJY04zyJmUyJ6Tu/GQmmv02aC+v/NQ2bE8HGR0MG3h1AiSH9Tw3Eik/oO/zIH9RWEdYOHMmKycrM38gLLjkwD1haab+wGImp/XxKzdiO4rsPWZ+cBU8Pih1OD2+iI9GFjGQ5Tp1aXR4JqTj/iAgYT4vLDJiMS+u6PovYiGo9+EidWsSND6C4SIzf17RiIEvcyaA6yszfyak4sY3aCKN8ys4YjEurunsPGxfDh4LFBwUexERe3UcPDBcVxJVnPImK3ImPL7wzHmb0iGg9cYwfyEtp/TbKzdiMiCt/8w8NxI0t/rWYnQTR/OGWH+V9TZyMT2c4St5DBEcbW4xLCw3JCoQMys8Kjo7YGQiOjFAPjU1MD4LOV1TR0V/JYbjcjE9Mi4pMDEmZg==",
        "story_writer": "EgYfYAYJFgcFABpgb353BhYXeVhodAs5IDgrYy0gLS9lPS4qJSRyJCxiOicpZDImP0AmdiMwLSBzEl5dQn88KyZyMSY+JTY1PjctJW40MD02LDIjN2tVb3UMOiEzK3BQMzkiPmw5KkFEElcxIDImIGVnfzowPCw3IS4gNH9/FS0nO3ImNyM7Lik3YT0+Eiw1MzApOmASZ1pPfzcqJiFlPStrKDgrNy0zcWcSNyMxZj8gICwxIDspazAnNUErPy47bDc+QEJbUyxzLC0mKnQrIyB5MSYwNW4lID42ekxichAsJ3U9JC0ycjJXPiJhOS0wO1dCElc8ISowIWUgNy5lLzYnLS50Zy0wLS5mYmxlLCchPDxkbGxwVjYlIjo6MS1LEB8IfyAwMCIsNzYkK3lyfWgkPSQkMyMxLyA8ZXJ8dT07LTImf0A6ICQ0IHRyDBBRWTEgIDInIDo8LmV0YWMrLSchI3AjIzIqIDE+MSEsYk5schZdLXYkNC88f1BcXVU0f2U0ICwgOmskeTEiPDQ8Jil/MCAlLiJlKS08KikrNzciEig/NT12dClbQ1tUMzZlIjExPTAlajozNi1hY3llMickKCY8In8kOjtsJykzIlM8IiQnYzc+QVUSG2FzJiw8NjEuPiA3PCZnIzwuITgna0xicgE2IzkmKzEkcjlBfzM3PCgxMVFVElkxPzxtcgE7fyUqLX8zKTI6ImUzLSshbzYsPi46LjkhYTAxUTR2KDs4O39GWFcWLTYzKjcyb38oKjcpJjo1bi4xfysrMiByKz4wJyg4LS48cFMxMmE4KTorW19cFjA9KTpyMTw6ay48JmM4KTwmNjpiMi4qPGUxJzAtKSBvWH0SDyQkMykmf0FAV1U2NSwgcis7KiU2eTA1LTNuICAxJzcvLHIpPiAwJT9+YTE4Uy03IiEpJn9cUV9Tc3MqITggNytnZTYtJy0zYmcmMzcgam8gLCwpeWk+IS0zJFswODI9JSRzElxdVT4nLCw8aXQyJDEwKSZmS2NnBCktLCJvNSAxJycgL2QnOzxeOiRhJjk3NxJRQRZ4PqT58yY8fzstMDJjPCiv/fovYjGn9PcmeG51bq3/3nIz0+X1Lz1sOpySSRUaf3QmKztlIDaq/+YrYyaC7j5ic2Jihe4iZTOj7vgvZDWW01w4di2W5jp4HhBHWDM2NjByIzszJyouOidoIzdnJH8hKigsICArJ3UvLSc1cjZAMDthISQxf1BcXVU0fU9uchcxKz43N3AoLSQ+Zyg6NiQiLiYkfzU9LCJkCwEffH83LTkjIywSWUYMfyAxLCA8Cz0uJC1zYyYgPDUkKyszIxA1Kj4ueWkuNig2N1cAOig7KXh/UVhTRD4wMSYgGjIwKDAqcUllYRovIH8kLCguPmUtJyY8IDBhPyVBK3YnMCk4f15ZWVN/MmUgPSsgNiUwNiowaDE8KCM6MTYvIDwkM2I4JjotJHIiVzw3MXlsOjBGEFtYOzY1JjwhMTE/ZSoqITwoOisgLGw=",
        "script_fix":   None,
        "duplicate_repair": "GD0lEj4kJHUtdAlbVUZYPj4gMDdlOTA9LDx/MS0iLzdlLCE3Lz8mZTomPD0jNm9YAlcoJCghKXQQfHxrFis7IGMwKTs8IDZ5PSYkLjlnMTcjMWYsPSsrIzwnbDYkIjVTKzMldSI1LUBRRl8wPWtjACAkMyomPH8nPTEiLiY+NiBmPDcrKyc7Kik3YSU5Rjd2JycpJzcSQ1FTMTZoJCAqITEvID1/LSkzPCYxNi0raEVYDR4QEWkeEQ0XAwhVe2EHKSAqQF4SeREfHGMkJDg2L2UTDAwGbW4pKn8vJDQkNiooLHtDYWQLAR98fyUpNDwxZRJLEEU8ISwzJho2MyQmMixhcho1ZSczLSYtEDshfXhkZW4wJCokEGV0b3tidiJvTTgbfwEgNyc3On8uPTg8NyQ4bjMtOmInKiAxLgArMWk6JS0nNUF/MDM6IXQWfGBnYgARCQwRDgdxQWh5FCYtMW4iJDwqZSQjPSY0YjYlIzckciRdfyIgJysxK21HXUQ7IGU0OiA6fz8kKzgmPB45KDc7MWUvPHI1LS0jICghJXxaH38AKDA4Oj5fVUFTfycgOyZlOSo4MXk3Ij4kbiEwMy5lIiYzJi0rISAvN2EzPlZ/NS44PDg6RlUSRTo9MSY8JjEsZU90fxA8IDdnIi0tMCgrNyF/KztpPzY1DSJXOTMzMCI3Oh1GW0UqMikcICAyOjkgNzwmZyI7NTc6LDEZOzc9K2x1DSNkLz0kEjY4NzAiIH9BU1dYOiBrSX9lEDBrKzYrYywkIiIxOmIqKCMrfn8wMDkgJSI3cFYqJi08LzUrV29BUzEnIC0xICcAPyoGLSY4LS8kIH81LDIncis6NXU+IzYlOz5Vc3YkOCMgNl1eHhY8PCswNzQhOiUmPHNjJzNuMzc+LDYvOzsqMWxfZGwTMzskV386KD4pdD4SQEBZOTY2MDsqOj4nZQ82JjwvLyogLCdlKyAkLDpiJywvJTFyJl02NSQ6OjEtCBBRWTEwNyYmIHQsKCA3OmwpIjouKjFiaHhvIikwNnUkKSUvOz5VcDMsOjg9MFwQHwh/MCotISAlKi4rOjpsKjMnIyI6bE9rbxYqfyw6PWwxMjdwVTo4JCclN39UWV5aOiFlMCcmPH8qNnl9LikiJmc1NysoZjs7IC9iITwvZm1yclE3P2EhJTErEl5TT31/ZWE9ZTc+JS15MSIxY2JnKi1iZzImPC1/Nj0sbDAzPXBcOjhjdTk6M1dDQRYrOiAncjE7fyplOjAtKzMrMyB/IS0nPTMmKycnZi0nNTs/XHA1LSApelUfEHtQfyctJnI2Oyo5Jjx/KjthKi4kMy0iMyp/LTojIzBgZCI9PUItMzImbCA3VxBWXz4/KiQnIHQ2JTE2fzQgIDpnLCtiKCMuPDZ/JDo7bDApN3BRMDgnOSU3Kx4QRV4wcyIiOysnfzs3PCwwPTMra2U+LCFmODokK2I2IS0qJjcjEjEzOSFiXnISdF0WMTwxYyAgJDoqMXkrKy1hPSYoOmI2IyEmIDEhMGktJzM9I0F/JCQhOSYxV1QSVDM8Jigha15VAgsJChcXAwIIBhQRf0w0OysvNyEWLiguMTtBIlw=",
    }

    _FALLBACK = {
        "recap_system": None,
        "story_writer": None,
        "script_fix":   None,
        "duplicate_repair": None,
    }

    _instance = None
    _cache = {}

    @classmethod
    def instance(cls) -> "PromptVault":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, key: str) -> Optional[str]:
        if key in self._cache:
            return self._cache[key]
        token = self._TOKENS.get(key)
        if token:
            result = _dec(token)
            self._cache[key] = result
            return result
        return self._FALLBACK.get(key)

    def is_vaulted(self, key: str) -> bool:
        return bool(self._TOKENS.get(key))


def generate_vault_tokens():
    """Utility: generate tokens trước khi build."""
    try:
        from core.review_styles import review_style_instruction, story_writer_instruction
        styles_text = review_style_instruction("professional_youtube_movie_recap")
        story_text  = story_writer_instruction()
        print("=== VAULT TOKENS ===")
        print(f'    "recap_system": "{_enc(styles_text)}",')
        print(f'    "story_writer": "{_enc(story_text)}",')
        print("\nCopy vào _TOKENS dict trong prompt_vault.py")
    except Exception as e:
        print(f"Lỗi: {e}")


if __name__ == "__main__":
    generate_vault_tokens()
