"""Configuration management for AutoRecapPro V2 — with encrypted sensitive fields"""
import base64
import hashlib
import json
import os
import platform
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

# ── Fields được mã hóa (API keys, không để plaintext) ────────────────────────
_SENSITIVE_FIELDS = {
    "gemini_api_key", "openrouter_api_key", "gemini_keys_file", "pekka_api_key",
    "elevenlabs_vn_api_key", "token",
}

# ── Tạo encryption key từ machine ID (mỗi máy khác nhau) ────────────────────
def _machine_key() -> bytes:
    """Tạo key 32 bytes từ hardware info — duy nhất cho từng máy."""
    parts = [
        platform.node(),
        platform.machine(),
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
    ]
    raw = "|".join(p for p in parts if p)
    return hashlib.sha256(raw.encode("utf-8")).digest()  # 32 bytes


def _xor_encrypt(data: str, key: bytes) -> str:
    """XOR cipher + base64 — đủ để che API key khỏi scan thông thường."""
    data_bytes = data.encode("utf-8")
    key_len = len(key)
    encrypted = bytes(b ^ key[i % key_len] for i, b in enumerate(data_bytes))
    return base64.b64encode(encrypted).decode("ascii")


def _xor_decrypt(data: str, key: bytes) -> str:
    try:
        encrypted = base64.b64decode(data.encode("ascii"))
        key_len = len(key)
        decrypted = bytes(b ^ key[i % key_len] for i, b in enumerate(encrypted))
        return decrypted.decode("utf-8")
    except Exception:
        return data  # Fallback: trả về nguyên nếu decode lỗi


def _encrypt_value(value: str) -> str:
    """Mã hóa 1 giá trị — prefix ENC: để phân biệt."""
    if not value or value.startswith("ENC:"):
        return value
    return "ENC:" + _xor_encrypt(value, _machine_key())


def _decrypt_value(value: str) -> str:
    """Giải mã 1 giá trị."""
    if not value or not value.startswith("ENC:"):
        return value  # Chưa mã hóa (config cũ) → trả về nguyên
    return _xor_decrypt(value[4:], _machine_key())


class ConfigManager:
    """Handles all configuration — sensitive fields auto-encrypted on disk."""
    _write_lock = threading.RLock()

    @staticmethod
    def get_default_config_path() -> str:
        base_dir = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.join(Path.home(), "AppData", "Local")
        )
        return os.path.join(base_dir, "AutoRecapPro_V2", "config.json")

    def __init__(self, config_path: str = None):
        self.config_path = config_path or self.get_default_config_path()
        self._cache: Dict[str, Any] = None

    # ── Internal: đọc raw từ file (có ENC: prefix) ───────────────────────────
    def _load_raw(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Config] Load error: {e}")
        return {}

    # ── Public: load và tự động decrypt sensitive fields ─────────────────────
    def load(self) -> Dict[str, Any]:
        raw = self._load_raw()
        # Decrypt các field nhạy cảm
        decoded = {}
        for k, v in raw.items():
            if k in _SENSITIVE_FIELDS and isinstance(v, str):
                decoded[k] = _decrypt_value(v)
            else:
                decoded[k] = v
        self._cache = decoded
        return self._cache

    # ── Public: save — tự động encrypt sensitive fields ──────────────────────
    def save(self, data: Dict[str, Any]) -> bool:
        with self._write_lock:
            return self._save_locked(data)

    def _save_locked(self, data: Dict[str, Any]) -> bool:
        temporary = None
        try:
            cfg = self.load().copy()
            cfg.update(data)

            # Encrypt trước khi ghi ra file
            to_write = {}
            for k, v in cfg.items():
                if k in _SENSITIVE_FIELDS and isinstance(v, str) and v:
                    to_write[k] = _encrypt_value(v)
                else:
                    to_write[k] = v

            directory = os.path.dirname(os.path.abspath(self.config_path))
            os.makedirs(directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as f:
                temporary = f.name
                json.dump(to_write, f, ensure_ascii=False, indent=2)
            os.replace(temporary, self.config_path)
            self._cache = cfg
            return True
        except Exception as e:
            print(f"[Config] Save error: {e}")
            return False
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> bool:
        return self.save({key: value})

    def clear_cache(self):
        self._cache = None
