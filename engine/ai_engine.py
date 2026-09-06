import asyncio
import base64
import json
import logging
import mimetypes
import os
import edge_tts
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

import warnings
from utils.helpers import FFmpegUtils
from engine.premium_pipeline import PremiumReviewPipeline
from engine.script_grounding import ScriptGroundingValidator
from engine.vietnamese_text import VietnameseTextGuard
try:
    from core.review_styles import normalize_review_style, review_style_instruction, story_writer_instruction
except Exception:
    def normalize_review_style(style=None):
        return "professional_youtube_movie_recap"

    def review_style_instruction(style=None):
        return "STYLE_ID: professional_youtube_movie_recap\nVOICE: Professional Vietnamese YouTube movie recap."

    def story_writer_instruction():
        return "STORY_WRITER_LAYER: Write each block as a story beat, not a translated subtitle."
try:
    from core.recap_prompt_pack import recap_prompt_pack_text, recap_block_prompt_rules, narrative_role_for_position, narrative_role_rules_text
except Exception:
    def recap_prompt_pack_text(section=None):
        return ""
    def recap_block_prompt_rules():
        return ""

genai = None
GENAI_V2 = False
_legacy_genai_vision = None


def _closed_temp_path(suffix: str) -> str:
    descriptor, path = tempfile.mkstemp(suffix=suffix)
    os.close(descriptor)
    return path


def _load_gemini_sdk(prefer_v2: bool = True):
    """Lazy-load Gemini SDK so the desktop app can start without compiling Google SDK."""
    global genai, GENAI_V2
    if genai is not None:
        return genai
    if prefer_v2:
        try:
            import google.genai as _genai_v2
            genai = _genai_v2
            GENAI_V2 = True
            return genai
        except Exception:
            pass
    import google.generativeai as _genai_legacy
    genai = _genai_legacy
    GENAI_V2 = False
    return genai


def _load_legacy_gemini_sdk():
    """Lazy-load legacy SDK only for vision paths that still need PIL image support."""
    global _legacy_genai_vision
    if _legacy_genai_vision is not None:
        return _legacy_genai_vision
    try:
        import google.generativeai as _legacy
        _legacy_genai_vision = _legacy
    except Exception:
        _legacy_genai_vision = None
    return _legacy_genai_vision

from engine.video_cutter import VideoCutter


class AIEngine:
    # Cache danh sach voices de tranh goi network moi lan
    _voice_cache: list = []
    _voice_cache_time: float = 0.0
    _VOICE_CACHE_TTL: float = 300.0  # 5 phut
    _openrouter_model_cache: list = []
    _openrouter_model_cache_time: float = 0.0
    _OPENROUTER_MODEL_CACHE_TTL: float = 900.0
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

    def __init__(self, api_key):
        self.candidates = [
            'gemini-2.0-flash-lite',
            'gemini-2.0-flash',
            'gemini-2.5-flash-lite',
            'gemini-2.5-flash',
            'gemini-1.5-flash-002',
            'gemini-1.5-flash',
            'text-bison-001',
            'chat-bison-001'
        ]
        self.api_keys = self._normalize_api_keys(api_key)
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.client = None

        if self.api_key:
            self._configure_api_key(self.api_key)
        else:
            env_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
            if env_key:
                self.api_keys = [env_key]
                self.api_key = env_key
                self._configure_api_key(env_key)

    @staticmethod
    def _normalize_api_keys(api_key):
        if not api_key:
            return []
        if isinstance(api_key, (list, tuple)):
            raw_keys = api_key
        else:
            raw = str(api_key).strip()
            if os.path.exists(raw) and os.path.isfile(raw):
                with open(raw, "r", encoding="utf-8") as f:
                    raw_keys = f.read().splitlines()
            else:
                raw_keys = re.split(r"[\r\n,;]+", raw)
        keys = []
        for key in raw_keys:
            key = str(key).strip()
            if not key or key.startswith("#"):
                continue
            if key not in keys:
                keys.append(key)
        return keys

    def _configure_api_key(self, api_key):
        self.api_key = api_key
        if self._is_openrouter_key(api_key):
            return
        if self._is_ollama_key(api_key):
            return
        # Market builds intentionally omit the large Google SDK tree. Text and
        # vision still work through the REST methods below; SDK is optional.
        try:
            sdk = _load_gemini_sdk(prefer_v2=True)
        except (ImportError, ModuleNotFoundError):
            self.client = None
            return
        if GENAI_V2:
            self.client = sdk.Client(api_key=api_key)
        else:
            sdk.configure(api_key=api_key)

    @staticmethod
    def _is_openrouter_key(api_key):
        key = str(api_key or "").strip()
        lower = key.lower()
        return lower.startswith("openrouter:") or lower.startswith("or:") or key.startswith("sk-or-")

    @staticmethod
    def _is_groq_key(api_key: str) -> bool:
        """Groq key: bắt đầu bằng 'gsk_' hoặc prefix 'groq:'"""
        key = str(api_key or "").strip()
        return key.startswith("gsk_") or key.lower().startswith("groq:")

    @staticmethod
    def _clean_groq_key(api_key: str) -> str:
        key = str(api_key or "").strip()
        if key.lower().startswith("groq:"):
            return key[len("groq:"):].strip()
        return key

    @staticmethod
    def _clean_openrouter_key(api_key):
        key = str(api_key or "").strip()
        lower = key.lower()
        for prefix in ("openrouter:", "or:"):
            if lower.startswith(prefix):
                return key[len(prefix):].strip()
        return key

    def _get_available_model_names(self):
        try:
            sdk = _load_gemini_sdk(prefer_v2=False)
            names = []
            for model in sdk.list_models():
                if isinstance(model, dict):
                    name = model.get('name')
                else:
                    name = getattr(model, 'name', None) or getattr(model, 'model_name', None)
                if name:
                    names.append(name)
            return names
        except Exception:
            return []

    @staticmethod
    def _is_key_or_quota_error(exc):
        text = str(exc).lower()
        markers = [
            "api key",
            "api_key",
            "permission",
            "unauthorized",
            "forbidden",
            "quota",
            "rate limit",
            "resource_exhausted",
            "429",
            "403",
            "401",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_quota_error(exc):
        text = str(exc).lower()
        return any(marker in text for marker in ("429", "quota", "rate limit", "resource_exhausted"))

    @staticmethod
    def _is_ollama_key(api_key: str) -> bool:
        """True nếu key là Ollama: ollama:model_name hoặc ollama:host|model_name"""
        key = str(api_key or "").strip().lower()
        return key.startswith("ollama:")

    @staticmethod
    def _parse_ollama_key(api_key: str):
        """Parse 'ollama:model' hoặc 'ollama:host|model' → (host, model).
        Ví dụ:
          ollama:gemma3          → (http://127.0.0.1:11434, gemma3)
          ollama:llama3.2        → (http://127.0.0.1:11434, llama3.2)
          ollama:localhost:11434|qwen2.5  → (http://localhost:11434, qwen2.5)
        """
        raw = str(api_key or "").strip()
        # Bỏ prefix "ollama:"
        body = raw[len("ollama:"):].strip()
        default_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if "|" in body:
            host_part, model = body.split("|", 1)
            host_part = host_part.strip()
            if not host_part.startswith("http"):
                host_part = f"http://{host_part}"
            return host_part.rstrip("/"), model.strip()
        # Không có |, toàn bộ là model name
        return default_host, body.strip() or "gemma3"

    @staticmethod
    def _retry_delay_seconds(exc, default=25.0):
        text = str(exc)
        patterns = [
            r"retry_delay\s*\{\s*seconds:\s*(\d+)",
            r"retry in\s*([0-9]+(?:\.[0-9]+)?)s",
            r"retry after\s*([0-9]+(?:\.[0-9]+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    return max(1.0, float(match.group(1)) + 1.0)
                except Exception:
                    pass
        return float(default)

    @classmethod
    def _call_with_quota_retry(cls, call, max_retries=1):
        last_exc = None
        for attempt in range(max(1, int(max_retries)) + 1):
            try:
                return call()
            except Exception as exc:
                last_exc = exc
                if not cls._is_quota_error(exc) or attempt >= max_retries:
                    raise
                delay = min(65.0, cls._retry_delay_seconds(exc))
                time.sleep(delay)
        raise last_exc

    def _build_model_candidates(self):
        desired = [
            'gemini-2.0-flash-lite',
            'gemini-2.0-flash',
            'gemini-2.5-flash-lite',
            'gemini-2.5-flash',
            'gemini-1.5-flash-002',
            'gemini-1.5-flash',
            'gemini-1.5',
            'text-bison-001',
            'chat-bison-001',
            'gemini-1.0',
        ]
        available = self._get_available_model_names()
        if available:
            candidates = []
            for desired_name in desired:
                for available_name in available:
                    if available_name.endswith(desired_name) or available_name == desired_name:
                        if available_name not in candidates:
                            candidates.append(available_name)
            if candidates:
                return candidates
            # Fallback to any generative model found in list
            for available_name in available:
                if any(marker in available_name for marker in ('gemini', 'bison')):
                    candidates.append(available_name)
            if candidates:
                return candidates
        # Fallback to hard-coded candidate list
        return [*self.candidates, *[f"models/{name}" for name in self.candidates]]

    @staticmethod
    def _normalize_gemini_model_for_rest(model_name: str) -> str:
        model_name = str(model_name or "").strip()
        if model_name.startswith("models/"):
            return model_name.split("/", 1)[1]
        return model_name

    @classmethod
    def _gemini_rest_request(cls, api_key: str, model_name: str, prompt: str) -> str:
        model_name = cls._normalize_gemini_model_for_rest(model_name)
        if not model_name:
            raise RuntimeError("Gemini REST model is empty")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": str(prompt or "")}],
                }
            ],
            "generationConfig": {
                "temperature": float(os.environ.get("GEMINI_TEMPERATURE", "0.55") or "0.55"),
                "maxOutputTokens": int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "8192") or "8192"),
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json; charset=utf-8")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(
                request,
                timeout=int(os.environ.get("GEMINI_REST_TIMEOUT", "120") or "120"),
            ) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini REST HTTP {exc.code}: {body_text}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Gemini REST error: {data.get('error')}")
        candidates = data.get("candidates") if isinstance(data, dict) else []
        for candidate in candidates or []:
            content = candidate.get("content") if isinstance(candidate, dict) else {}
            parts = content.get("parts") if isinstance(content, dict) else []
            text_parts = [
                str(part.get("text") or "")
                for part in parts or []
                if isinstance(part, dict) and str(part.get("text") or "").strip()
            ]
            if text_parts:
                return "\n".join(text_parts).strip()
        raise RuntimeError(f"Gemini REST returned empty response for model {model_name}")

    def _try_generate_gemini_rest(self, prompt: str, api_key: str) -> str:
        last_exc = None
        configured = self._env_list("GEMINI_MODEL") or self._env_list("GEMINI_MODELS")
        model_candidates = [*configured, *self.candidates]
        for model_name in model_candidates:
            if any(skip in str(model_name).lower() for skip in ("bison", "embedding")):
                continue
            try:
                result = self._call_with_quota_retry(
                    lambda model_name=model_name: self._gemini_rest_request(api_key, model_name, prompt)
                )
                self.last_provider_used = "gemini_rest"
                self.last_fallback_used = False
                return result
            except Exception as exc:
                last_exc = exc
                if self._is_key_or_quota_error(exc):
                    break
                continue
        raise RuntimeError(f"All Gemini REST models failed. Last error: {last_exc}")

    @classmethod
    def _gemini_multimodal_request(cls, api_key, model_name, content_parts):
        """Call Gemini Vision through REST so packaged builds need no Google SDK."""
        model_name = cls._normalize_gemini_model_for_rest(model_name)
        if not model_name:
            raise RuntimeError("Gemini Vision REST model is empty")

        parts = []
        for part in content_parts or []:
            if isinstance(part, dict) and part.get("type") == "image_path":
                image_path = str(part.get("path") or "")
                mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                with open(image_path, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                parts.append({"inlineData": {"mimeType": mime, "data": encoded}})
            elif isinstance(part, dict) and part.get("type") == "image_url":
                image_url = part.get("image_url") or {}
                data_url = image_url.get("url") if isinstance(image_url, dict) else image_url
                match = re.match(r"^data:([^;]+);base64,(.+)$", str(data_url or ""), re.DOTALL)
                if not match:
                    raise RuntimeError("Gemini Vision only accepts local/base64 images")
                parts.append({"inlineData": {"mimeType": match.group(1), "data": match.group(2)}})
            elif isinstance(part, dict):
                parts.append({"text": str(part.get("text") or "")})
            else:
                parts.append({"text": str(part)})

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": float(os.environ.get("GEMINI_VISION_TEMPERATURE", "0.2") or "0.2"),
                "maxOutputTokens": int(os.environ.get("GEMINI_VISION_MAX_TOKENS", "4096") or "4096"),
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json; charset=utf-8")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(
                request,
                timeout=int(os.environ.get("GEMINI_VISION_TIMEOUT", "45") or "45"),
            ) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini Vision REST HTTP {exc.code}: {body_text}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Gemini Vision REST error: {data.get('error')}")
        for candidate in data.get("candidates", []) if isinstance(data, dict) else []:
            content = candidate.get("content") if isinstance(candidate, dict) else {}
            response_parts = content.get("parts") if isinstance(content, dict) else []
            text_parts = [
                str(item.get("text") or "")
                for item in response_parts or []
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if text_parts:
                return "\n".join(text_parts).strip()
        raise RuntimeError(f"Gemini Vision REST returned empty response for model {model_name}")

    def _try_gemini_vision_content(self, content_parts, api_key=None):
        api_key = str(api_key or self.api_key or "").strip()
        configured = self._env_list("GEMINI_VISION_MODEL") or self._env_list("GEMINI_VISION_MODELS")
        candidates = [
            *configured,
            "gemini-2.0-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
        ]
        last_exc = None
        seen = set()
        for model_name in candidates:
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)
            try:
                result = self._call_with_quota_retry(
                    lambda model_name=model_name: self._gemini_multimodal_request(
                        api_key, model_name, content_parts
                    )
                )
                self.last_provider_used = f"gemini_vision_rest:{model_name}"
                self.last_fallback_used = False
                return result
            except Exception as exc:
                last_exc = exc
                if self._is_key_or_quota_error(exc):
                    break
        raise RuntimeError(f"All Gemini Vision REST models failed. Last error: {last_exc}")

    @staticmethod
    def _env_list(name):
        raw = os.environ.get(name, "")
        return [item.strip() for item in re.split(r"[,;\r\n]+", raw) if item.strip()]

    @classmethod
    def _get_openrouter_free_models(cls, api_key=None, limit=8):
        now = time.time()
        if cls._openrouter_model_cache and now - cls._openrouter_model_cache_time < cls._OPENROUTER_MODEL_CACHE_TTL:
            return cls._openrouter_model_cache[:limit]
        request = urllib.request.Request(cls.OPENROUTER_MODELS_URL, method="GET")
        if api_key:
            request.add_header("Authorization", f"Bearer {cls._clean_openrouter_key(api_key)}")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception:
            return []

        free_models = []
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            pricing = item.get("pricing") or {}
            prompt_price = str(pricing.get("prompt", "")).strip()
            completion_price = str(pricing.get("completion", "")).strip()
            is_free = model_id.endswith(":free") or (prompt_price in ("0", "0.0", "0.000000") and completion_price in ("0", "0.0", "0.000000"))
            if not is_free:
                continue
            name = str(item.get("name") or model_id).lower()
            if any(skip in name for skip in ("safety", "moderation", "guardrail", "embedding", "rerank")):
                continue
            architecture = item.get("architecture") or {}
            output_modalities = architecture.get("output_modalities") or []
            input_modalities = architecture.get("input_modalities") or []
            if output_modalities and "text" not in output_modalities:
                continue
            if input_modalities and "text" not in input_modalities:
                continue
            context_length = int(item.get("context_length") or 0)
            free_models.append((context_length, model_id))

        free_models.sort(reverse=True)
        models = []
        for _, model_id in free_models:
            if model_id not in models:
                models.append(model_id)
            if len(models) >= limit:
                break
        cls._openrouter_model_cache = models
        cls._openrouter_model_cache_time = now
        return models[:limit]

    @classmethod
    def _build_openrouter_model_candidates(cls, api_key=None):
        configured = cls._env_list("OPENROUTER_MODEL") or cls._env_list("OPENROUTER_MODELS")
        dynamic = cls._get_openrouter_free_models(api_key=api_key, limit=8)
        fallback = [
            "qwen/qwen2.5-vl-72b-instruct:free",
            "qwen/qwen2.5-vl-32b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
            "google/gemma-4-31b-it:free",          # Gemma 4 31B — mạnh, context dài, ưu tiên 1
            "google/gemma-3-27b-it:free",           # Gemma 3 27B — fallback Gemma
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen-2.5-72b-instruct:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nex-agi/nex-n2-pro:free",
            "google/gemma-2-9b-it:free",
        ]
        models = []
        for model_id in [*configured, *dynamic, *fallback]:
            if model_id and model_id not in models:
                models.append(model_id)
        return models

    @classmethod
    def _build_openrouter_vision_model_candidates(cls, api_key=None):
        configured = cls._env_list("OPENROUTER_VISION_MODEL") or cls._env_list("OPENROUTER_VISION_MODELS")
        fallback = [
            "qwen/qwen2.5-vl-72b-instruct:free",
            "qwen/qwen2.5-vl-32b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
            "google/gemma-3-27b-it:free",
            "openrouter/auto",
        ]
        models = []
        for model_id in [*configured, *fallback]:
            if model_id and model_id not in models:
                models.append(model_id)
        return models

    @staticmethod
    def _image_to_data_url(image_path):
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        with open(image_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @classmethod
    def _openrouter_multimodal_request(cls, api_key, model_name, content_parts):
        converted = []
        for part in content_parts or []:
            if isinstance(part, dict) and part.get("type") == "image_path":
                converted.append({
                    "type": "image_url",
                    "image_url": {"url": cls._image_to_data_url(part.get("path"))},
                })
            elif isinstance(part, dict) and part.get("type") == "image_url":
                converted.append(part)
            elif isinstance(part, dict):
                converted.append({"type": "text", "text": str(part.get("text") or "")})
            else:
                converted.append({"type": "text", "text": str(part)})

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": converted}],
            "temperature": float(os.environ.get("OPENROUTER_VISION_TEMPERATURE", "0.2") or "0.2"),
            "max_tokens": int(os.environ.get("OPENROUTER_VISION_MAX_TOKENS", "4096") or "4096"),
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(cls.OPENROUTER_URL, data=body, method="POST")
        request.add_header("Authorization", f"Bearer {cls._clean_openrouter_key(api_key)}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("HTTP-Referer", os.environ.get("OPENROUTER_SITE_URL", "http://localhost/autorecappro"))
        request.add_header("X-Title", os.environ.get("OPENROUTER_APP_NAME", "AutoRecapPro V2"))
        try:
            with urllib.request.urlopen(request, timeout=int(os.environ.get("OPENROUTER_VISION_TIMEOUT", "45") or "45")) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter vision HTTP {exc.code}: {body_text}") from exc

        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text") or part) for part in content)
            if content:
                return str(content)
        if data.get("error"):
            raise RuntimeError(f"OpenRouter vision error: {data.get('error')}")
        raise RuntimeError(f"OpenRouter vision returned empty response for model {model_name}")

    def _try_openrouter_vision_content(self, content_parts, api_key=None):
        api_key = api_key or self.api_key
        last_exc = None
        for model_name in self._build_openrouter_vision_model_candidates(api_key=api_key):
            try:
                result = self._call_with_quota_retry(
                    lambda model_name=model_name: self._openrouter_multimodal_request(api_key, model_name, content_parts)
                )
                self.last_provider_used = f"openrouter_vision:{model_name}"
                self.last_fallback_used = True
                return result
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"All OpenRouter vision models failed. Last error: {last_exc}")

    @classmethod
    def _openrouter_request(cls, api_key, model_name, prompt):
        clean_key = cls._clean_openrouter_key(api_key)
        max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "8192") or "8192")
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise movie recap writer. Return only the requested output, no markdown wrapper. "
                        "When writing Vietnamese, always use full Vietnamese diacritics; never return unaccented Vietnamese."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": float(os.environ.get("OPENROUTER_TEMPERATURE", "0.55") or "0.55"),
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(cls.OPENROUTER_URL, data=body, method="POST")
        request.add_header("Authorization", f"Bearer {clean_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("HTTP-Referer", os.environ.get("OPENROUTER_SITE_URL", "http://localhost/autorecappro"))
        request.add_header("X-Title", os.environ.get("OPENROUTER_APP_NAME", "AutoRecapPro V2"))
        try:
            with urllib.request.urlopen(request, timeout=int(os.environ.get("OPENROUTER_TIMEOUT", "90") or "90")) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            retry_text = f" retry after {retry_after}" if retry_after else ""
            raise RuntimeError(f"OpenRouter HTTP {exc.code}{retry_text}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter network error: {exc}") from exc

        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text") or part) for part in content)
            if content:
                return str(content)
        if data.get("error"):
            raise RuntimeError(f"OpenRouter error: {data.get('error')}")
        raise RuntimeError(f"OpenRouter returned empty response for model {model_name}")

    def _try_generate_openrouter(self, prompt, api_key):
        last_exc = None
        for model_name in self._build_openrouter_model_candidates(api_key=api_key):
            try:
                return self._call_with_quota_retry(
                    lambda model_name=model_name: self._openrouter_request(api_key, model_name, prompt)
                )
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"All OpenRouter models failed. Last error: {last_exc}")


    # ── Persistent Gemini Web driver (dùng chung cho cả pipeline) ────────────
    _web_driver = None        # Selenium WebDriver instance, dùng lại nhiều lần
    _web_driver_lock = None   # threading.Lock

    @classmethod
    def _get_web_driver_lock(cls):
        if cls._web_driver_lock is None:
            import threading
            cls._web_driver_lock = threading.Lock()
        return cls._web_driver_lock

    @classmethod
    def _get_or_create_web_driver(cls, log=None, force_visible=False):
        """Lấy hoặc tạo mới Selenium driver. Dùng lại session đã mở."""
        if log is None:
            log = lambda msg: None
        with cls._get_web_driver_lock():
            # Kiểm tra driver hiện tại còn sống không
            if cls._web_driver is not None:
                try:
                    _ = cls._web_driver.title  # ping
                    if force_visible:
                        from core.gemini_web import driver_is_headless
                        if driver_is_headless(cls._web_driver):
                            try:
                                cls._web_driver.quit()
                            except Exception:
                                pass
                            cls._web_driver = None
                        else:
                            return cls._web_driver
                    else:
                        return cls._web_driver
                except Exception:
                    try: cls._web_driver.quit()
                    except Exception: pass
                    cls._web_driver = None

            # Tạo driver mới
            try:
                from core.gemini_web import create_auto_driver
                log("   🌐 Tạo Gemini Web session (dùng lại cho toàn pipeline)...")
                cls._web_driver = create_auto_driver(log=log, force_visible=force_visible)
                log("   ✅ Gemini Web session sẵn sàng")
            except Exception as e:
                cls._web_driver = None
                raise RuntimeError(f"Không tạo được Gemini Web driver: {e}") from e
        return cls._web_driver

    @classmethod
    def close_web_driver(cls):
        """Đóng Selenium driver khi pipeline kết thúc."""
        with cls._get_web_driver_lock():
            if cls._web_driver is not None:
                try:
                    cls._web_driver.quit()
                except Exception:
                    pass
                cls._web_driver = None

    @staticmethod
    def _gemini_web_fallback_enabled() -> bool:
        value = str(os.environ.get("AUTORECAP_GEMINI_WEB_FALLBACK", "1") or "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _try_generate_gemini_web(self, prompt: str) -> str:
        """Dùng Gemini Web (Selenium) khi API hết quota.
        
        Dùng persistent driver — không mở/đóng browser mỗi lần gọi.
        """
        try:
            from core.gemini_web import send_prompt_to_gemini
        except Exception as exc:
            raise RuntimeError(f"Gemini Web fallback unavailable: {exc}") from exc

        timeout = int(os.environ.get("AUTORECAP_GEMINI_WEB_TIMEOUT", "300") or "300")
        log_fn = lambda msg: logger.info(msg)

        driver = self._get_or_create_web_driver(log=log_fn)
        response = ""
        visible_retry_done = False
        for attempt in range(1, 4):
            try:
                response = send_prompt_to_gemini(
                    prompt=prompt,
                    timeout=timeout,
                    log=log_fn,
                    driver=driver,
                    close_after=False,  # KHÔNG đóng — dùng lại lần sau
                    navigate=(attempt == 1),  # lần đầu navigate, lần sau dùng lại tab
                )
                if response and str(response).strip():
                    self.last_provider_used = "gemini_web"
                    self.last_fallback_used = True
                    return str(response).strip()
            except Exception as e:
                # Driver có thể bị crash → reset
                if attempt == 3:
                    self.__class__._web_driver = None
                    raise RuntimeError(f"Gemini Web attempt {attempt} failed: {e}")
            if not visible_retry_done:
                try:
                    from core.gemini_web import driver_is_headless
                    if driver_is_headless(driver):
                        log_fn("   ⚠️ Gemini chạy ẩn chưa dùng được -> mở cửa sổ để đăng nhập lại")
                        self.close_web_driver()
                        driver = self._get_or_create_web_driver(log=log_fn, force_visible=True)
                        visible_retry_done = True
                        continue
                except Exception as visible_exc:
                    log_fn(f"   ⚠️ Không chuyển được sang Chrome hiển thị: {visible_exc}")
            import time as _t
            _t.sleep(3 + attempt * 2)

        raise RuntimeError("Gemini Web trả về rỗng sau 3 lần thử")

    def _try_generate(self, prompt):
        """Try Gemini/OpenRouter API first, then optional free Gemini Web fallback."""
        last_exc = None
        self.last_provider_used = ""
        self.last_fallback_used = False
        text_api_enabled = str(os.environ.get("AUTORECAP_TEXT_API_ENABLED", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        keys_to_try = self.api_keys or ([self.api_key] if self.api_key else [])

        if text_api_enabled:
            for api_key in keys_to_try:
                if self._is_openrouter_key(api_key):
                    openrouter_text_enabled = str(
                        os.environ.get("AUTORECAP_OPENROUTER_TEXT_ENABLED", "0") or "0"
                    ).strip().lower() in {"1", "true", "yes", "on"}
                    if not openrouter_text_enabled:
                        continue
                    try:
                        result = self._try_generate_openrouter(prompt, api_key)
                        self.last_provider_used = "openrouter"
                        self.last_fallback_used = False
                        return result
                    except Exception as exc:
                        last_exc = exc
                        continue
                if self._is_ollama_key(api_key) or self._is_groq_key(api_key):
                    # Local/cloud fallback providers are optional; market build uses Gemini API/Web by default.
                    continue

                try:
                    result = self._try_generate_gemini_rest(prompt, api_key)
                    self.last_provider_used = "gemini_rest"
                    self.last_fallback_used = False
                    return result
                except Exception as exc:
                    last_exc = exc
                    if self._is_key_or_quota_error(exc):
                        continue

                sdk_enabled = str(os.environ.get("AUTORECAP_GEMINI_SDK_FALLBACK", "0") or "0").strip().lower() in {
                    "1", "true", "yes", "on"
                }
                if not sdk_enabled:
                    continue

                try:
                    self._configure_api_key(api_key)
                except Exception as exc:
                    last_exc = exc
                    continue

                model_candidates = self._build_model_candidates()
                if GENAI_V2:
                    for model_name in model_candidates:
                        try:
                            response = self._call_with_quota_retry(
                                lambda model_name=model_name: self.client.models.generate_content(
                                    model=model_name,
                                    contents=prompt,
                                )
                            )
                            self.last_provider_used = "gemini_api"
                            self.last_fallback_used = False
                            return getattr(response, 'text', None) or str(response)
                        except Exception as exc:
                            last_exc = exc
                            if self._is_key_or_quota_error(exc):
                                break
                            continue
                else:
                    sdk = _load_gemini_sdk(prefer_v2=False)
                    for model_name in model_candidates:
                        try:
                            gen_model = sdk.GenerativeModel(model_name=model_name)
                            response = self._call_with_quota_retry(
                                lambda gen_model=gen_model: gen_model.generate_content(prompt)
                            )
                            self.last_provider_used = "gemini_api"
                            self.last_fallback_used = False
                            return (
                                getattr(response, 'text', None)
                                or getattr(response, 'content', None)
                                or (response.candidates[0].content if getattr(response, 'candidates', None) else None)
                                or str(response)
                            )
                        except Exception as exc:
                            last_exc = exc
                            if self._is_key_or_quota_error(exc):
                                break
                            continue

        if self._gemini_web_fallback_enabled():
            try:
                return self._try_generate_gemini_web(prompt)
            except Exception as web_exc:
                last_exc = web_exc

        key_count = len(keys_to_try)
        raise RuntimeError(f"All {key_count} AI provider keys failed or hit quota, and Gemini Web fallback did not succeed. Last error: {last_exc}")

    def generate_script(
        self,
        movie_name,
        movie_description="",
        keep_seconds=3,
        skip_seconds=10,
        output_duration_seconds=None,
        target_words=None,
        language="Vietnamese",
        source_duration_seconds=None,
    ):
        """Generate script for video recap.
        
        Args:
            output_duration_seconds: ACTUAL video output duration after cutting
                                    This should be calculated from VideoCalculator.calculate_output_duration()
                                    e.g., if input=40min, keep=3s, skip=10s → output=~9-10min
        """
        if not self.api_key:
            raise ValueError("Vui lòng nhập API Key")

        if target_words is None:
            if output_duration_seconds and output_duration_seconds > 0:
                # Vietnamese: ~2.5 words per second (more conservative)
                target_words = int(output_duration_seconds * 3.0)
                # Clamp to reasonable bounds
                target_words = max(100, min(target_words, 1500))
            else:
                target_words = 250

        cycle = keep_seconds + skip_seconds
        cut_context = self._build_cut_timing_context(
            source_duration_seconds=source_duration_seconds,
            keep_seconds=keep_seconds,
            skip_seconds=skip_seconds,
        )
        
        prompt = (
            f"Viết kịch bản voiceover review phim '{movie_name}' bằng tiếng {language}, khoảng {target_words} từ.\n\n"
            "BẮT BUỘC: nếu viết tiếng Việt thì phải dùng đầy đủ dấu tiếng Việt; không được viết không dấu.\n\n"
            f"⏱️ VIDEO OUTPUT DURATION: ~{int(output_duration_seconds or 0)} giây\n"
            f"(Video gốc sẽ được cắt: giữ {keep_seconds}s + bỏ {skip_seconds}s lặp lại)\n\n"
            f"{cut_context}\n\n"
            "📝 HƯỚNG DẪN VIẾT:\n"
            f"1. Kịch bản PHẢI phù hợp CHÍNH XÁC với {target_words} từ để match thời gian video.\n"
            "2. Viết NGẮN GỌN, mỗi câu độc lập (video sẽ cắt xen kẽ).\n"
            "3. Tập trung vào CÁC ĐIỂM CHÍNH: cốt truyện, nhân vật, cao trào, cảm xúc.\n"
            "4. Hạn chế liên từ như 'sau đó' - dùng 'đặc biệt', 'tuyệt vời', 'bất ngờ'.\n"
            "5. Mở đầu: gây tò mò. Giữa: nhấn mạnh twist. Cuối: kêu gọi xem phim.\n"
            "6. Viết review, không tóm tắt film toàn bộ cốt truyện.\n\n"
        )

        if output_duration_seconds and output_duration_seconds > 0:
            minutes = round(output_duration_seconds / 60, 1)
            prompt += f"⏱️ Thời gian output: {minutes} phút = khoảng {target_words} từ\n"

        if movie_description:
            prompt += f"\n📌 Nội dung phim: {movie_description}\n"
        else:
            prompt += "\n📌 Viết review tổng quan hay, kích thích khán giả xem phim.\n"
        
        prompt += "\n✅ CHỈ TRẢ VỀ NỘI DUNG KỊCH BẢN - không giải thích, không thêm hướng dẫn, không thêm markdown."

        try:
            return self._try_generate(prompt)
        except Exception as e:
            raise Exception(f"Lỗi AI: {str(e)}")

    def generate_hooks(self, movie_name):
        """Tạo tiêu đề Header/Footer giật gân"""
        if not self.api_key:
            return "TIÊU ĐỀ MẪU", "XEM NGAY KẺO LỠ"
        prompt = (
            f"Tạo 1 cặp tiêu đề TikTok cho phim '{movie_name}': 1. Header (VIẾT HOA, gây tò mò),"
            " 2. Footer (Gây sốc). Trả về dạng: Header | Footer"
        )
        try:
            text = self._try_generate(prompt)
            parts = text.split("|")
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
            return text.strip(), "XEM NGAY KẾT CỤC"
        except Exception:
            return "SIÊU PHẨM REVIEW", "CÁI KẾT QUÁ SỐC"

    @staticmethod
    def _extract_json_payload(text):
        if not text:
            return None
        cleaned = text.strip()

        # Xu ly code fence: ```json ... ``` hoac ``` ... ```
        # Co the co newline sau ``` hoac khong
        if "```" in cleaned:
            # Xoa tat ca code fence markers
            cleaned = re.sub(r"```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"```", "", cleaned)
            cleaned = cleaned.strip()

        # Lay phan JSON tu { den } cuoi cung
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start:end + 1]

        # Thu parse truc tiep
        try:
            return json.loads(cleaned)
        except Exception:
            pass

        # Thu xu ly escaped newlines: \n literal -> newline that
        try:
            cleaned2 = cleaned.encode("utf-8").decode("unicode_escape")
            return json.loads(cleaned2)
        except Exception:
            pass

        # Thu xoa backtick con sot
        try:
            cleaned3 = cleaned.replace("`", "")
            return json.loads(cleaned3)
        except Exception:
            pass

        # Thu dung regex de lay tung truong
        try:
            summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
            script_match = re.search(r'"script"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
            if summary_match or script_match:
                result = {}
                if summary_match:
                    result["summary"] = summary_match.group(1).replace("\\n", "\n")
                if script_match:
                    result["script"] = script_match.group(1).replace("\\n", "\n")
                # Tim subtitle_chunks
                chunks_match = re.search(r'"subtitle_chunks"\s*:\s*(\[.*?\])', cleaned, re.DOTALL)
                if chunks_match:
                    try:
                        result["subtitle_chunks"] = json.loads(chunks_match.group(1))
                    except Exception:
                        pass
                blocks_match = re.search(r'"script_blocks"\s*:\s*(\[.*?\])', cleaned, re.DOTALL)
                if blocks_match:
                    try:
                        result["script_blocks"] = json.loads(blocks_match.group(1))
                    except Exception:
                        pass
                if result:
                    return result
        except Exception:
            pass

        return None

    @staticmethod
    def _normalize_script_blocks(blocks):
        normalized = []
        if not isinstance(blocks, list):
            return normalized
        for index, block in enumerate(blocks, 1):
            if isinstance(block, dict):
                block_id = block.get("block_id") or block.get("book_id") or block.get("id") or index
                text = block.get("text") or block.get("script") or block.get("voiceover") or ""
            else:
                block_id = index
                text = str(block)
            try:
                block_id = int(block_id)
            except Exception:
                block_id = index
            text = AIEngine._clean_review_script_text(text)
            if text:
                normalized.append({
                    "block_id": block_id,
                    "book_id": block_id,
                    "text": text,
                    "target_words": block.get("target_words") if isinstance(block, dict) else None,
                    "scene_ids": block.get("scene_ids", []) if isinstance(block, dict) else [],
                    "dialogue_text": block.get("dialogue_text", "") if isinstance(block, dict) else "",
                    "visual_hint": block.get("visual_hint", "") if isinstance(block, dict) else "",
                    "scene_anchor": block.get("scene_anchor", "") if isinstance(block, dict) else "",
                    "scene_role": block.get("scene_role", "") if isinstance(block, dict) else "",
                    "scene_role_label": block.get("scene_role_label", "") if isinstance(block, dict) else "",
                    "emotion": block.get("emotion", "") if isinstance(block, dict) else "",
                    "pace": block.get("pace", "") if isinstance(block, dict) else "",
                    "beat": block.get("beat", "") if isinstance(block, dict) else "",
                    "book_title": block.get("book_title", "") if isinstance(block, dict) else "",
                    "narrative_goal": block.get("narrative_goal", "") if isinstance(block, dict) else "",
                    "bridge_line": block.get("bridge_line", "") if isinstance(block, dict) else "",
                    "bridge_to_next": block.get("bridge_to_next", "") if isinstance(block, dict) else "",
                    "duration_hint_seconds": block.get("duration_hint_seconds") if isinstance(block, dict) else None,
                    "visual_anchor": block.get("visual_anchor", "") if isinstance(block, dict) else "",
                    "visual_notes": block.get("visual_notes", "") if isinstance(block, dict) else "",
                    "visual_evidence_source": block.get("visual_evidence_source", "") if isinstance(block, dict) else "",
                    "must_mention": block.get("must_mention", "") if isinstance(block, dict) else "",
                    "book_summary": block.get("book_summary", "") if isinstance(block, dict) else "",
                    "transition_line": block.get("transition_line", "") if isinstance(block, dict) else "",
                    "srt_anchor": block.get("srt_anchor", "") if isinstance(block, dict) else "",
                    "source_srt_anchor": block.get("source_srt_anchor", "") if isinstance(block, dict) else "",
                    "cut_srt_anchor": block.get("cut_srt_anchor", "") if isinstance(block, dict) else "",
                    "one_main_idea": block.get("one_main_idea", "") if isinstance(block, dict) else "",
                    "render_start": block.get("render_start") if isinstance(block, dict) else None,
                    "render_duration": block.get("render_duration") if isinstance(block, dict) else None,
                    "render_end": block.get("render_end") if isinstance(block, dict) else None,
                    "render_reason": block.get("render_reason", "") if isinstance(block, dict) else "",
                    "semantic_reason": block.get("semantic_reason", "") if isinstance(block, dict) else "",
                    "smart_score": block.get("smart_score") if isinstance(block, dict) else None,
                })
        return sorted(normalized, key=lambda item: item["block_id"])

    @staticmethod
    def _extract_script_blocks(text):
        if not text:
            return []
        raw = str(text or "")
        pattern = re.compile(
            r"(?im)^\s*Block\s*(\d+)\s*:\s*(.*?)"
            r"(?=^\s*Block\s*\d+\s*:\s*|\Z)",
            re.DOTALL,
        )
        matches = pattern.findall(raw)
        blocks = []
        for block_index, block_text in matches:
            cleaned = AIEngine._clean_review_script_text(block_text)
            if cleaned:
                blocks.append({
                    "block_id": int(block_index),
                    "text": cleaned,
                })
        if not blocks and raw.strip():
            cleaned = AIEngine._clean_review_script_text(raw)
            if cleaned:
                blocks.append({"block_id": 1, "text": cleaned})
        return sorted(blocks, key=lambda item: item["block_id"])

    @staticmethod
    def _split_text_into_balanced_chunks(text, chunk_count):
        cleaned = AIEngine._clean_review_script_text(text)
        if not cleaned:
            return []

        try:
            chunk_count = int(chunk_count)
        except Exception:
            chunk_count = 1

        if chunk_count <= 1:
            return [cleaned]

        words = cleaned.split()
        if len(words) >= chunk_count:
            boundaries = [round(index * len(words) / chunk_count) for index in range(chunk_count + 1)]
            boundaries[0] = 0
            boundaries[-1] = len(words)
            chunks = []
            for index in range(chunk_count):
                start = boundaries[index]
                end = boundaries[index + 1]
                if index < chunk_count - 1:
                    end = max(end, start + 1)
                else:
                    end = len(words)
                piece = " ".join(words[start:end]).strip()
                if piece:
                    chunks.append(piece)
            if len(chunks) == chunk_count:
                return chunks

        # Fallback by characters, then split the longest piece until the count matches.
        pieces = []
        total_chars = len(cleaned)
        for index in range(chunk_count):
            start = round(index * total_chars / chunk_count)
            end = round((index + 1) * total_chars / chunk_count)
            piece = cleaned[start:end].strip()
            if piece:
                pieces.append(piece)

        if len(pieces) < chunk_count:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
            if sentences:
                pieces = sentences[:]

        while len(pieces) < chunk_count and pieces:
            longest_index = max(range(len(pieces)), key=lambda i: len(pieces[i]))
            piece = pieces.pop(longest_index)
            split_words = piece.split()
            if len(split_words) >= 2:
                midpoint = max(1, len(split_words) // 2)
                left = " ".join(split_words[:midpoint]).strip()
                right = " ".join(split_words[midpoint:]).strip()
            else:
                midpoint = max(1, len(piece) // 2)
                left = piece[:midpoint].strip()
                right = piece[midpoint:].strip()
            if not left or not right:
                pieces.insert(longest_index, piece)
                break
            pieces.insert(longest_index, right)
            pieces.insert(longest_index, left)

        if len(pieces) > chunk_count:
            merged = pieces[:chunk_count - 1]
            merged.append(" ".join(pieces[chunk_count - 1:]).strip())
            pieces = merged

        return [piece for piece in pieces if piece.strip()] or [cleaned]

    @classmethod
    def _align_script_blocks_to_render_blocks(cls, script_blocks, script_text, render_blocks):
        render_blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        if not render_blocks:
            return cls._normalize_script_blocks(script_blocks)

        normalized = cls._normalize_script_blocks(script_blocks)
        if len(normalized) != len(render_blocks):
            fallback_text = "\n\n".join(
                block.get("text", "") for block in normalized if block.get("text")
            ).strip() or cls._clean_review_script_text(script_text)
            aligned_texts = cls._split_text_into_balanced_chunks(fallback_text, len(render_blocks))
            normalized = []
            for index, render_block in enumerate(render_blocks, 1):
                role_meta = narrative_role_for_position(index, len(render_blocks))
                text = aligned_texts[index - 1] if index - 1 < len(aligned_texts) else (aligned_texts[-1] if aligned_texts else "")
                text = cls._clean_review_script_text(text)
                if not text:
                    text = cls._clean_review_script_text(fallback_text)
                normalized.append({
                    "block_id": int(render_block.get("block_id") or index),
                    "text": text,
                    "target_words": render_block.get("target_words"),
                    "scene_ids": render_block.get("scene_ids", []),
                    "dialogue_text": render_block.get("dialogue_text", ""),
                    "visual_hint": render_block.get("visual_hint", "") or render_block.get("cut_reason", ""),
                    "scene_anchor": render_block.get("cut_reason", ""),
                    "scene_role": render_block.get("scene_role") or role_meta.get("scene_role"),
                    "scene_role_label": render_block.get("scene_role_label") or role_meta.get("scene_role_label"),
                    "narrative_role": render_block.get("narrative_role") or render_block.get("scene_role") or role_meta.get("scene_role"),
                    "emotion": render_block.get("emotion", ""),
                    "pace": render_block.get("pace", "normal"),
                    "beat": render_block.get("beat", ""),
                    "book_title": render_block.get("book_title", "") or render_block.get("scene_role_label", ""),
                    "narrative_goal": render_block.get("narrative_goal", ""),
                    "bridge_line": render_block.get("bridge_line", "") or render_block.get("bridge_hint", ""),
                    "bridge_to_next": render_block.get("bridge_to_next", "") or render_block.get("bridge_hint", ""),
                    "duration_hint_seconds": render_block.get("duration_hint_seconds") or render_block.get("duration", 0.0),
                    "visual_anchor": render_block.get("visual_anchor", "") or render_block.get("cut_reason", ""),
                    "visual_notes": render_block.get("visual_notes", ""),
                    "visual_evidence_source": render_block.get("visual_evidence_source", ""),
                    "must_mention": render_block.get("must_mention", ""),
                    "book_summary": render_block.get("book_summary", "") or render_block.get("narrative_goal", ""),
                    "transition_line": render_block.get("transition_line", "") or render_block.get("bridge_hint", ""),
                    "srt_anchor": render_block.get("srt_anchor", ""),
                    "source_srt_anchor": render_block.get("source_srt_anchor", ""),
                    "cut_srt_anchor": render_block.get("cut_srt_anchor", ""),
                    "one_main_idea": render_block.get("one_main_idea", "") or render_block.get("narrative_goal", ""),
                    "render_start": render_block.get("start_in_final_video", 0.0),
                    "render_duration": render_block.get("duration", 0.0),
                    "render_end": render_block.get("end_in_final_video", 0.0),
                    "render_reason": render_block.get("cut_reason", ""),
                    "semantic_reason": render_block.get("semantic_reason", "") or render_block.get("reason", ""),
                    "smart_score": render_block.get("smart_score", 0),
                })
            return normalized

        aligned = []
        for index, (block, render_block) in enumerate(zip(normalized, render_blocks), 1):
            role_meta = narrative_role_for_position(index, len(render_blocks))
            aligned_block = dict(block)
            aligned_block.update({
                "block_id": int(render_block.get("block_id") or block.get("block_id") or index),
                "book_id": int(render_block.get("block_id") or block.get("book_id") or block.get("block_id") or index),
                "text": block.get("text", ""),
                "target_words": block.get("target_words") or render_block.get("target_words"),
                "scene_ids": block.get("scene_ids") or render_block.get("scene_ids", []),
                "dialogue_text": block.get("dialogue_text") or render_block.get("dialogue_text", ""),
                "visual_hint": block.get("visual_hint") or render_block.get("visual_hint", "") or render_block.get("cut_reason", ""),
                "scene_anchor": block.get("scene_anchor") or render_block.get("cut_reason", ""),
                "scene_role": block.get("scene_role") or render_block.get("scene_role") or role_meta.get("scene_role"),
                "scene_role_label": block.get("scene_role_label") or render_block.get("scene_role_label") or role_meta.get("scene_role_label"),
                "narrative_role": block.get("narrative_role") or block.get("scene_role") or render_block.get("narrative_role") or render_block.get("scene_role") or role_meta.get("scene_role"),
                "emotion": block.get("emotion") or "",
                "pace": block.get("pace") or "normal",
                "beat": block.get("beat") or "",
                "book_title": block.get("book_title") or render_block.get("scene_role_label", "") or "",
                "narrative_goal": block.get("narrative_goal") or "",
                "bridge_line": block.get("bridge_line") or block.get("bridge_to_next") or "",
                "bridge_to_next": block.get("bridge_to_next") or "",
                "duration_hint_seconds": block.get("duration_hint_seconds") or render_block.get("duration", 0.0),
                "visual_anchor": block.get("visual_anchor") or render_block.get("visual_anchor", "") or render_block.get("cut_reason", ""),
                "visual_notes": block.get("visual_notes") or render_block.get("visual_notes", ""),
                "visual_evidence_source": block.get("visual_evidence_source") or render_block.get("visual_evidence_source", ""),
                "must_mention": block.get("must_mention") or render_block.get("must_mention", ""),
                "book_summary": block.get("book_summary") or "",
                "transition_line": block.get("transition_line") or render_block.get("transition_line", "") or render_block.get("bridge_hint", ""),
                "srt_anchor": block.get("srt_anchor") or render_block.get("srt_anchor", ""),
                "source_srt_anchor": block.get("source_srt_anchor") or render_block.get("source_srt_anchor", ""),
                "cut_srt_anchor": block.get("cut_srt_anchor") or render_block.get("cut_srt_anchor", ""),
                "one_main_idea": block.get("one_main_idea") or render_block.get("one_main_idea", "") or render_block.get("narrative_goal", ""),
                "render_start": render_block.get("start_in_final_video", 0.0),
                "render_duration": render_block.get("duration", 0.0),
                "render_end": render_block.get("end_in_final_video", 0.0),
                "render_reason": render_block.get("cut_reason", ""),
                "semantic_reason": block.get("semantic_reason") or render_block.get("semantic_reason", "") or render_block.get("reason", ""),
                "smart_score": render_block.get("smart_score", 0),
            })
            aligned.append(aligned_block)
        return sorted(aligned, key=lambda item: item["block_id"])

    @staticmethod
    def _split_subtitle_chunks(text):
        text = re.sub(r"\[(KEEP|CUT)\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[PACE\s*:\s*(normal|fast|urgent|slow|calm)\]", "", text, flags=re.IGNORECASE).strip()
        # Bo marker ky thuat nhung giu lai cau review/thoai di kem.
        text = re.sub(r'\[DIALOGUE[^\]]*\]\s*', "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\[SCENE[^\]]*\]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[B-ROLL[^\]]*\]\s*", "", text, flags=re.IGNORECASE)
        if not text:
            return []
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks = []
        for paragraph in paragraphs:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if sentence:
                    chunks.append(sentence)
        return chunks or [text]
    
    def generate_script_modular(
        self,
        movie_name,
        movie_description="",
        keep_seconds=3,
        skip_seconds=10,
        max_duration_seconds=None,
        target_words=None,
        language="Vietnamese",
        character_focus=None,
        subtitle_context=None,
        timing_hint=None,
        target_duration_seconds=None,
        source_duration_seconds=None,
    ):
        """Generate script in modular parts (intro + body + outro) with [KEEP]/[CUT] markers for sync"""
        if not self.api_key:
            raise ValueError("Vui lòng nhập API Key")

        if target_words is None:
            if max_duration_seconds and max_duration_seconds > 0:
                target_words = min(max(300, int(max_duration_seconds * 3.0)), 5000)
            else:
                target_words = 800

        cycle = keep_seconds + skip_seconds
        cut_context = self._build_cut_timing_context(
            source_duration_seconds=source_duration_seconds,
            keep_seconds=keep_seconds,
            skip_seconds=skip_seconds,
        )
        
        # Part 1: Intro (hook) - always KEEP
        intro_prompt = (
            f"Viết phần mở đầu (INTRO) cho review phim '{movie_name}' bằng tiếng {language}, khoảng 30-40 từ.\n"
            "Mục tiêu: Gây tò mò nhưng phải đúng bối cảnh, nhân vật và mâu thuẫn chính trong tư liệu phim.\n"
            "VÍ DỤ: 'Phim này sẽ thổi bay trí óc bạn!'\n"
            "CHỈ TRẢ VỀ NỘI DUNG - không giải thích, không tham dự."
        )
        if cut_context:
            intro_prompt += f"\n\nNGỮ CẢNH CẮT VIDEO:\n{cut_context}"
        if movie_description:
            intro_prompt += f"\n\nTƯ LIỆU PHIM ĐỂ BÁM SÁT:\n{movie_description[:6000]}"
        if character_focus:
            intro_prompt += f"\n\nNHÂN VẬT/TRỌNG TÂM CẦN BÁM:\n{character_focus[:600]}"
        if subtitle_context:
            intro_prompt += f"\n\nNGỮ CẢNH SRT BỔ SUNG:\n{subtitle_context[:3000]}"
        if target_duration_seconds:
            intro_prompt += f"\n\nMỤC TIÊU THỜI GIAN TOÀN BỘ: khoảng {int(target_duration_seconds)} giây."
        if timing_hint:
            intro_prompt += f"\n\nĐIỀU CHỈNH NHỊP ĐỌC: {timing_hint}"
        
        # Part 2: Body (main content) - with KEEP/CUT markers
        body_prompt = (
            f"Viết phần nội dung (BODY) cho review phim '{movie_name}' bằng tiếng {language}, khoảng {target_words * 0.6} từ.\n"
            f"VIDEO CẮT: Giữ {keep_seconds}s, bỏ {skip_seconds}s theo chu kỳ {cycle}s. Voice phải đi xuyên suốt theo mạch phim, còn các câu/đoạn được đánh dấu để video lấy đúng cảnh.\n"
            "\n🎬 HƯỚNG DẪN ĐÁNH DẤU VIDEO:\n"
            "- Mỗi đoạn bắt đầu bằng [KEEP] hoặc [CUT].\n"
            "- Sau marker [KEEP]/[CUT] có thể thêm [PACE:normal], [PACE:fast], hoặc [PACE:urgent] để AI chỉ nhịp đọc theo cảnh.\n"
            "- Không dùng giảm tốc voice. Đoạn cần cảm xúc/chậm rãi dùng [PACE:normal] và viết câu có nhịp ngắt tự nhiên.\n"
            "- [KEEP] dùng cho cảnh/sự kiện có hình ảnh quan trọng cần xuất hiện trong video: nhân vật, xung đột, manh mối, cao trào, twist.\n"
            "- [CUT] dùng cho câu nối, bình luận, giải thích nền, cảm xúc hoặc chuyển ý; audio vẫn đọc bình thường nhưng video có thể nhảy qua.\n"
            "- Viết theo đúng trình tự phim từ mở đầu, bối cảnh, biến cố, điều tra, cao trào đến kết.\n"
            "- Không đảo sự kiện, không bịa nhân vật, không bỏ mất bối cảnh quan trọng trong tư liệu.\n"
            "- Mỗi đoạn nên 1-3 câu, đủ độc lập để khi video cắt 3s/10s vẫn hiểu nội dung.\n"
            "- Dùng giọng kể review phim cuốn hút, có phân tích và cảm xúc, không liệt kê khô.\n"
            "\nNội dung cần bao phủ: bối cảnh, nhân vật chính, động cơ, manh mối, cảnh quan trọng, chuyển biến cảm xúc, cao trào và kết luận.\n"
        )
        if cut_context:
            body_prompt += f"\nNGỮ CẢNH CẮT VIDEO:\n{cut_context}\n"
        
        if movie_description:
            body_prompt += f"TƯ LIỆU PHIM BẮT BUỘC BÁM SÁT:\n{movie_description}\n"
        if character_focus:
            body_prompt += f"Tập trung nhấn mạnh nhân vật/chủ đề này: {character_focus}\n"
        if subtitle_context:
            body_prompt += f"Bối cảnh từ SRT để bám nhịp cảnh: {subtitle_context[:3000]}\n"
        if target_duration_seconds:
            body_prompt += f"Mục tiêu timing cho toàn bộ script là khoảng {int(target_duration_seconds)} giây.\n"
        if timing_hint:
            body_prompt += f"Điều chỉnh nhịp đọc theo hướng: {timing_hint}\n"
        
        body_prompt += (
            "CHỈ TRẢ VỀ KỊCH BẢN VOICEOVER VỚI MARKER [KEEP]/[CUT] VÀ MARKER [PACE] NẾU CẦN. "
            "Không thêm hướng dẫn, không nhắc lại yêu cầu, không viết ngoài nội dung phim."
        )
        
        # Part 3: Outro (call to action) - always KEEP
        outro_prompt = (
            f"Viết phần kết (OUTRO) cho review phim '{movie_name}' bằng tiếng {language}, khoảng 30-40 từ.\n"
            "Mục tiêu: Kết lại đúng tinh thần câu chuyện, nhắc lại nút thắt/cảm xúc chính và kêu gọi xem tiếp.\n"
            "VÍ DỤ: 'Phải xem để tin!', 'Kết thúc khiến bạn nổi da gà!'\n"
            "CHỈ TRẢ VỀ NỘI DUNG - không giải thích."
        )
        if cut_context:
            outro_prompt += f"\n\nNGỮ CẢNH CẮT VIDEO:\n{cut_context}"
        if movie_description:
            outro_prompt += f"\n\nTƯ LIỆU PHIM ĐỂ BÁM SÁT:\n{movie_description[-6000:]}"
        if character_focus:
            outro_prompt += f"\n\nNHÂN VẬT/TRỌNG TÂM KẾT:\n{character_focus[:600]}"
        if target_duration_seconds:
            outro_prompt += f"\n\nRÀNG BUỘC THỜI GIAN TOÀN BỘ: {int(target_duration_seconds)} giây."
        
        try:
            intro = self._try_generate(intro_prompt).strip()
            body = self._try_generate(body_prompt).strip()
            outro = self._try_generate(outro_prompt).strip()
            
            # Add markers to intro and outro (always KEEP)
            intro_marked = f"[KEEP] {intro}"
            outro_marked = f"[KEEP] {outro}"
            
            # Combine parts
            full_script = f"{intro_marked}\n\n{body}\n\n{outro_marked}"
            return full_script
        except Exception as e:
            raise Exception(f"Lỗi AI: {str(e)}")

    @staticmethod
    def _clean_review_script_text(text):
        if not text:
            return ""
        text = unicodedata.normalize("NFC", str(text))
        dialogue_placeholders = []

        def _stash_dialogue(match):
            dialogue_placeholders.append(match.group(0))
            return f"DIALOGUEPLACEHOLDER{len(dialogue_placeholders) - 1}TOKEN"

        text = re.sub(r"\[DIALOGUE[^\]]*\]", _stash_dialogue, text, flags=re.IGNORECASE)
        # Remove SRT-style line numbers (single digit or multiple digits on their own line)
        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
        # Remove SRT timestamps (00:00:01,000 --> 00:00:03,000)
        text = re.sub(r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}", "", text)
        text = re.sub(r"\d{2}:\d{2}:\d{3}", "", text)  # Variation: HH:MM:SSS
        text = text.replace("**", "").replace("__", "").replace("~~", "")
        text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
        text = re.sub(r"(?m)^\s*[-*+]\s*", "", text)
        text = re.sub(r"(?m)^\s*>\s*", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        for index, original in enumerate(dialogue_placeholders):
            text = text.replace(f"DIALOGUEPLACEHOLDER{index}TOKEN", original)
        return text.strip()

    @staticmethod
    def _estimate_script_metrics(text, words_per_second=2.5):
        words = len((text or "").split())
        seconds = words / words_per_second if words_per_second > 0 else 0.0
        return words, seconds

    @staticmethod
    def _trim_evidence_text(text, limit=220):
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rsplit(" ", 1)[0].strip()

    @classmethod
    def _fallback_block_text(cls, render_block, index, total):
        render_block = render_block or {}
        evidence = ScriptGroundingValidator._evidence_text(render_block, {})
        anchor = cls._trim_evidence_text(evidence, 220)
        if not anchor:
            anchor = "cảnh phim chuyển sang một diễn biến mới, nhưng phần thoại nguồn không đủ rõ để khẳng định thêm chi tiết"
        role = str(render_block.get("scene_role_label") or render_block.get("scene_role") or "").strip()
        if index == 1:
            lead = "Mở đầu đoạn recap, câu chuyện đặt người xem vào đúng chi tiết đang xuất hiện trên màn hình"
        elif index >= total:
            lead = "Ở đoạn cuối này, mạch phim khép lại bằng chi tiết cần giữ đúng theo hình ảnh và lời thoại"
        else:
            lead = "Tiếp theo, mạch phim chuyển sang một chi tiết quan trọng khác"
        sentences = [
            f"{lead}: {anchor}.",
        ]
        anchor_sentence = PremiumReviewPipeline._anchor_sentence(anchor)
        if anchor_sentence and not PremiumReviewPipeline._similar_sentence_present(" ".join(sentences), anchor_sentence):
            sentences.append(anchor_sentence)
        if role:
            sentences.append(f"Vai trò của cảnh là {role}, nên nhịp kể cần giữ rõ hành động, phản ứng và hệ quả trực tiếp.")
        if index < total:
            sentences.append("Câu kể chỉ mở một nhịp sang cảnh sau, không kéo thêm sự kiện ngoài bằng chứng của block.")

        target = int(render_block.get("target_words") or 28)
        min_words = max(14, int(target * 0.70))
        fillers = [
            f"Điểm đáng giữ lại là {anchor}, vì nó giúp recap đi theo đúng thứ tự phim.",
            "Thay vì suy diễn thêm, đoạn này để hình ảnh và lời thoại gốc dẫn đường cho cảm xúc.",
        ]
        text = " ".join(sentences)
        for filler in fillers:
            if len(text.split()) >= min_words:
                break
            if PremiumReviewPipeline._similar_sentence_present(text, filler):
                continue
            text = f"{text} {filler}"
        return cls._clean_review_script_text(text)

    @classmethod
    def _build_quota_fallback_package(cls, render_blocks, target_duration_seconds=None, reason="quota_exceeded"):
        blocks = []
        render_blocks = [block for block in (render_blocks or []) if isinstance(block, dict)]
        total = len(render_blocks)
        for index, render_block in enumerate(render_blocks, 1):
            block_id = int(render_block.get("block_id") or index)
            text = cls._fallback_block_text(render_block, index, total)
            blocks.append({
                "block_id": block_id,
                "book_id": block_id,
                "text": text,
                "target_words": render_block.get("target_words"),
                "scene_ids": render_block.get("scene_ids", []),
                "dialogue_text": render_block.get("dialogue_text", ""),
                "visual_hint": render_block.get("visual_hint", "") or render_block.get("visual_anchor", ""),
                "scene_anchor": render_block.get("srt_anchor", "") or render_block.get("dialogue_text", ""),
                "scene_role": render_block.get("scene_role", ""),
                "scene_role_label": render_block.get("scene_role_label", ""),
                "duration_hint_seconds": render_block.get("duration", 0.0),
                "visual_anchor": render_block.get("visual_anchor", ""),
                "srt_anchor": render_block.get("srt_anchor", "") or render_block.get("dialogue_text", ""),
                "render_start": render_block.get("start_in_final_video", 0.0),
                "render_duration": render_block.get("duration", 0.0),
                "render_end": render_block.get("end_in_final_video", 0.0),
                "render_reason": render_block.get("cut_reason", "") or render_block.get("reason", ""),
                "semantic_reason": render_block.get("semantic_reason", "") or render_block.get("source", ""),
                "pace": "normal",
                "emotion": "grounded",
            })
        script = "\n\n".join(block["text"] for block in blocks if block.get("text")).strip()
        words, seconds = cls._estimate_script_metrics(script)
        package = {
            "summary": "Kịch bản tạm được tạo từ bằng chứng SRT/scene vì Gemini API đang bị giới hạn quota.",
            "script": script,
            "script_blocks": blocks,
            "render_blocks": render_blocks,
            "subtitle_chunks": [block["text"] for block in blocks if block.get("text")],
            "story_beats": [],
            "book_map": render_blocks,
            "beat_plan": [],
            "target_duration_seconds": target_duration_seconds,
            "estimated_script_words": words,
            "estimated_script_seconds": seconds,
            "script_sync_ratio": (seconds / target_duration_seconds) if target_duration_seconds else 1.0,
            "sync_rewrite_used": False,
            "quality_rewrite_used": False,
            "length_rewrite_used": False,
            "timing_padding_used": False,
            "story_polish_rewrite_used": False,
            "ai_quota_fallback_used": True,
            "ai_quota_fallback_reason": reason,
            "raw": script,
        }
        package["sync_report"] = PremiumReviewPipeline.validate_sync(
            package.get("script_blocks"),
            render_blocks,
            target_duration_seconds if target_duration_seconds and target_duration_seconds > 0 else None,
        )
        package["quality_report"] = PremiumReviewPipeline.validate_story_quality(
            package.get("script_blocks"),
            render_blocks,
        )
        package, grounding_report = ScriptGroundingValidator.annotate_package(package, render_blocks)
        package["script_grounding_report"] = grounding_report
        return package

    def repair_vietnamese_diacritics_package(self, package, render_blocks=None):
        """Restore Vietnamese diacritics in a generated review package when a model returns no-accent text."""
        package = dict(package or {})
        report = VietnameseTextGuard.report_blocks(package.get("script_blocks") or [])
        script_needs = VietnameseTextGuard.needs_diacritic_repair(package.get("script", ""))
        if not report.get("needs_repair") and not script_needs:
            package["vietnamese_diacritics_report"] = report
            package["vietnamese_diacritics_repair_used"] = False
            return package, report

        all_blocks = [
            dict(block)
            for block in (package.get("script_blocks") or [])
            if isinstance(block, dict)
        ]
        weak_ids = set()
        for item in report.get("blocks", []):
            if item.get("needs_repair"):
                try:
                    weak_ids.add(int(item.get("block_id")))
                except Exception:
                    pass
        if not weak_ids and script_needs and all_blocks:
            weak_ids = {
                int(block.get("block_id") or block.get("book_id") or index)
                for index, block in enumerate(all_blocks, 1)
            }

        fixed_by_id = {}
        raw_parts = []

        def _build_repair_prompt(payload):
            return (
                "You are a Vietnamese diacritics restoration engine.\n"
                "Task: add correct Vietnamese diacritics to the text below.\n"
                "Rules:\n"
                "- Preserve meaning, timeline order, block_id, names, and approximate length.\n"
                "- Do not summarize, expand, shorten, censor, or add new plot facts.\n"
                "- Return ONLY valid JSON with keys: summary, script, script_blocks, subtitle_chunks.\n"
                "- Every Vietnamese sentence must have proper diacritics.\n\n"
                "Example: 'So So phat hien dau lau duoi ao' -> 'Sở Sở phát hiện đầu lâu dưới ao'.\n\n"
                f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            )

        batch_size = 24
        weak_blocks = []
        for index, block in enumerate(all_blocks, 1):
            try:
                block_id = int(block.get("block_id") or block.get("book_id") or index)
            except Exception:
                block_id = index
            if block_id in weak_ids:
                weak_blocks.append({"block_id": block_id, "text": block.get("text", "")})

        for start in range(0, len(weak_blocks), batch_size):
            batch = weak_blocks[start:start + batch_size]
            payload = {
                "summary": package.get("summary", "") if start == 0 else "",
                "script": "",
                "script_blocks": batch,
                "subtitle_chunks": [],
            }
            raw = self._try_generate(_build_repair_prompt(payload))
            raw_parts.append(raw)
            data = self._extract_json_payload(raw) or {}
            fixed_blocks = data.get("script_blocks") if isinstance(data, dict) else []
            if isinstance(fixed_blocks, list):
                for item in fixed_blocks:
                    if not isinstance(item, dict):
                        continue
                    try:
                        block_id = int(item.get("block_id") or item.get("book_id"))
                    except Exception:
                        continue
                    fixed_text = self._clean_review_script_text(item.get("text") or "")
                    if fixed_text:
                        fixed_by_id[block_id] = fixed_text
            if start == 0:
                summary = self._clean_review_script_text(data.get("summary") or "")
                if summary:
                    package["summary"] = summary

        if not all_blocks and script_needs:
            payload = {
                "summary": package.get("summary", ""),
                "script": package.get("script", ""),
                "script_blocks": [],
                "subtitle_chunks": package.get("subtitle_chunks") or [],
            }
            raw = self._try_generate(_build_repair_prompt(payload))
            raw_parts.append(raw)
            data = self._extract_json_payload(raw) or {}
            summary = self._clean_review_script_text(data.get("summary") or "")
            if summary:
                package["summary"] = summary
            script = self._clean_review_script_text(data.get("script") or "")
            if script:
                package["script"] = script
            subtitle_chunks = data.get("subtitle_chunks")
            if isinstance(subtitle_chunks, list) and subtitle_chunks:
                package["subtitle_chunks"] = [
                    self._clean_review_script_text(chunk)
                    for chunk in subtitle_chunks
                    if str(chunk).strip()
                ]

        repaired_blocks = []
        for index, block in enumerate(all_blocks, 1):
            if not isinstance(block, dict):
                continue
            item = dict(block)
            try:
                block_id = int(item.get("block_id") or item.get("book_id") or index)
            except Exception:
                block_id = index
            if fixed_by_id.get(block_id):
                item["text"] = fixed_by_id[block_id]
            repaired_blocks.append(item)

        if repaired_blocks:
            package["script_blocks"] = repaired_blocks
            package["script"] = "\n\n".join(
                str(block.get("text", "")).strip()
                for block in repaired_blocks
                if str(block.get("text", "")).strip()
            ).strip()

        if repaired_blocks:
            package["subtitle_chunks"] = [block["text"] for block in repaired_blocks if block.get("text")]

        final_report = VietnameseTextGuard.report_blocks(package.get("script_blocks") or [])
        package["vietnamese_diacritics_report"] = final_report
        package["vietnamese_diacritics_repair_used"] = True
        package["vietnamese_diacritics_repair_raw"] = "\n\n--- batch ---\n\n".join(raw_parts[-3:])
        return package, final_report

    @classmethod
    def _parse_review_package_raw(cls, raw, render_blocks=None):
        data = cls._extract_json_payload(raw) or {}
        summary = cls._clean_review_script_text(data.get("summary", "").strip())
        script = cls._clean_review_script_text(data.get("script") or raw.strip())
        subtitle_chunks = data.get("subtitle_chunks") or []
        script_blocks = cls._normalize_script_blocks(data.get("script_blocks"))
        story_beats = data.get("story_beats") or []

        if not script_blocks:
            script_blocks = cls._extract_script_blocks(data.get("script") or raw)
        if render_blocks:
            script_blocks = cls._align_script_blocks_to_render_blocks(script_blocks, script or raw, render_blocks)
        if script_blocks:
            script = "\n\n".join(block["text"] for block in script_blocks if block.get("text")).strip() or script

        if not script:
            script = cls._clean_review_script_text(raw)
        if script_blocks:
            subtitle_chunks = [block["text"] for block in script_blocks if block.get("text")]
        if not isinstance(subtitle_chunks, list) or not subtitle_chunks:
            subtitle_chunks = cls._split_subtitle_chunks(script)

        subtitle_chunks = [str(chunk).strip() for chunk in subtitle_chunks if str(chunk).strip()]
        return {
            "summary": summary,
            "script": script,
            "script_blocks": script_blocks,
            "render_blocks": render_blocks if render_blocks else [],
            "subtitle_chunks": subtitle_chunks,
            "story_beats": story_beats if isinstance(story_beats, list) else [],
            "raw": raw,
        }

    @staticmethod
    def _build_review_prompt(
        mode,
        movie_name,
        language="Vietnamese",
        target_words=None,
        movie_description="",
        subtitle_context="",
        timed_subtitles="",
        character_focus=None,
        target_duration_seconds=None,
        keep_seconds=3,
        skip_seconds=10,
        source_duration_seconds=None,
        render_blocks=None,
        visual_context="",
        block_context="",
        timing_hint="",
        existing_script="",
        quality_report=None,
    ):
        mode = (mode or "generate").lower()
        style_name = normalize_review_style(os.environ.get("AUTORECAP_REVIEW_STYLE"))
        style_block = review_style_instruction(style_name)
        story_block = story_writer_instruction()
        cut_context = AIEngine._build_cut_timing_context(
            source_duration_seconds=source_duration_seconds,
            keep_seconds=keep_seconds,
            skip_seconds=skip_seconds,
        )
        if mode == "revise":
            prompt = (
                f"Sửa lại kịch bản review phim '{movie_name}' bằng tiếng {language} dựa trên lời thoại SRT nguồn.\n"
                f"REVIEW STYLE / GIỌNG KỂ:\n{style_block}\n"
                f"STORY WRITER / DẪN CHUYỆN:\n{story_block}\n"
                "Trả về DUY NHẤT JSON hợp lệ gồm 3 khóa: \"summary\", \"script\", \"subtitle_chunks\", \"script_blocks\".\n"
                "Mục tiêu sửa:\n"
                "- Giữ sức hút của review nhưng viết lại để bám mạch phim theo thứ tự thời gian, không nhảy cảnh.\n"
                "- Khung 5 nhịp bắt buộc: Hook mở bằng biến cố/xung đột chính, Setup, Escalation, Climax, Ending/hậu vị.\n"
                "- Không mở kiểu 'Hôm nay nói về', không giật tít chung chung rồi bỏ trôi khỏi cột truyện.\n"
                "- Nhân vật trọng tâm phải dựa trên diễn biến xuyên suốt của tập phim, không lấy một câu thoại rời rạc làm cốt truyện.\n"
                "- Xác định nhân vật/chủ đề từ thoại thật; không tự bịa tên, quan hệ, sự kiện hoặc lời thoại.\n"
                "- Nếu một câu trong bản cũ không gắn được vào plot hoặc SRT, viết lại cho sát cảnh hơn hoặc bỏ đi.\n"
                "- script phải là voiceover trơn, không markdown, không [KEEP]/[CUT], không ** hay #.\n"
                "- subtitle_chunks là subtitle tiếng Việt sạch, không chứa marker kỹ thuật.\n"
                "- summary là tóm tắt tiếng Việt 3-5 câu về cốt truyện và nhân vật trọng tâm.\n"
            )
            if existing_script:
                prompt += f"\nKỊCH BẢN HIỆN TẠI CẦN SỬA:\n{existing_script[:14000]}\n"
        else:
            prompt = f"""Bạn là một biên kịch review phim chuyên nghiệp, chuyên viết voiceover cho YouTube/TikTok/Facebook.
Nhiệm vụ: Viết kịch bản review phim '{movie_name}' bằng tiếng {language}, khoảng {target_words} từ.

REVIEW STYLE / GIỌNG KỂ BẮT BUỘC:
{style_block}

STORY WRITER / DẪN CHUYỆN BẮT BUỘC:
{story_block}

RECAP2 SMOOTH REVIEW QUALITY GATE:
- Viet nhu mot voiceover review phim hien dai: canh cu the -> y nghia/drama -> he qua/cau noi.
- Khong bien SRT thanh ban dich thoai. Khong copy thoai dai; nen giai thich dieu do lam thay doi tinh the ra sao.
- Moi block phai co mot vai tro rieng: mo nut, day ap luc, giai thich manh moi, doi huong cam xuc, hoac noi sang bien co tiep theo.
- Cam cac cau chung chung lap lai nhu "mach phim tiep tuc", "chi tiet nay cho thay", "o canh nay" neu khong co neo canh cu the.
- Voice khong duoc hut canh: neu time canh dai hon loi thoai, mo rong bang review bam canh thay vi filler.

YÊU CẦU NGÔN NGỮ BẮT BUỘC:
- Nếu viết tiếng Việt, toàn bộ summary, script, subtitle_chunks và script_blocks.text PHẢI là tiếng Việt có dấu chuẩn.
- Cấm trả tiếng Việt không dấu kiểu "So So phat hien dau lau duoi ao"; phải viết "Sở Sở phát hiện đầu lâu dưới ao".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NGHỆ THUẬT DẪN DẮT CÂU CHUYỆN (BẮT BUỘC)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. HOOK MỞ ĐẦU (2-3 câu đầu tiên):
   - Mở bằng một câu hỏi gây tò mò HOẶC một tình huống căng thẳng đang xảy ra
   - VÍ DỤ HAY: "Điều gì xảy ra khi người bạn tin tưởng nhất lại chính là kẻ phản bội?"
   - VÍ DỤ HAY: "Chỉ một quyết định sai, và cả cuộc đời cô ấy sụp đổ."
   - TRÁNH: "Hôm nay mình sẽ review...", "Phim này kể về...", "Xin chào các bạn..."

2. XÂY DỰNG NHÂN VẬT (không liệt kê, phải có cảm xúc):
   - Giới thiệu nhân vật qua HÀNH ĐỘNG và MÂU THUẪN, không phải tên + nghề nghiệp
   - VÍ DỤ HAY: "Sở Sở không phải kiểu phụ nữ ngồi khóc — cô ấy đọc hiện trường như đọc sách."
   - VÍ DỤ HAY: "Vương gia lạnh lùng bề ngoài, nhưng mỗi lần cô nguy hiểm, anh ta đều xuất hiện."

3. NHỊP DẪN DẮT (tạo sức hút liên tục):
   - Kết mỗi đoạn bằng một câu khiến người xem muốn biết tiếp
   - Dùng câu hỏi tu từ: "Liệu anh ta có thể thoát không?", "Và rồi điều không ai ngờ đã xảy ra..."
   - Xen kẽ giữa mô tả cảnh + bình luận cảm xúc + tiên đoán kết quả

4. CAO TRÀO VÀ TWIST:
   - Không spoil thẳng — dẫn dắt đến ngưỡng cửa rồi để người xem tự cảm nhận
   - VÍ DỤ HAY: "Và khi sự thật được hé lộ, không ai trong phòng chiếu kịp phản ứng."
   - Dùng ngôn ngữ mạnh, giàu hình ảnh: "tan vỡ", "bẽ bàng", "vỡ oà", "nghẹt thở"

5. KẾT THÚC (kêu gọi xem phim):
   - Không nói thẳng kết quả — tạo cảm giác tiếc nuối, muốn biết thêm
   - VÍ DỤ HAY: "Phần còn lại... bạn phải tự xem để hiểu tại sao tôi xem lại 3 lần."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NGUYÊN TẮC GIỌNG VIẾT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Giọng đồng hành: như bạn kể chuyện cho bạn nghe, không phải báo cáo
- Câu ngắn và dài xen kẽ: câu ngắn tạo nhịp gấp, câu dài giải thích cảm xúc
- Dùng từ Việt tự nhiên, tránh dịch sát từ Trung/Anh
- Mỗi câu đọc lên phải nghe tự nhiên như người thật đang nói
- Tránh hoàn toàn: liệt kê sự kiện khô khan, lặp từ, câu mở đầu "bắt đầu từ..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YÊU CẦU KỸ THUẬT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trả về DUY NHẤT JSON hợp lệ với 4 khóa:
{{
  "summary": "Tóm tắt 3-5 câu về cốt truyện và nhân vật trọng tâm, bám diễn biến tập phim.",
  "script": "Kịch bản voiceover hoàn chỉnh, tự nhiên, không markdown, không [KEEP]/[CUT], không ** hay #.",
  "subtitle_chunks": ["Các câu ngắn tiếng Việt để ghép SRT, 8-15 từ mỗi câu"],
  "script_blocks": [
    {{
      "block_id": 1,
      "text": "Nội dung voice cho block này, đủ dài và liền mạch với block trước/sau",
      "target_words": 25,
      "scene_role": "hook_intro",
      "narrative_goal": "Giới thiệu xung đột chính và nhân vật",
      "bridge_line": "Câu cuối nối sang block tiếp theo"
    }}
  ]
}}

- script_blocks: PHẢI có đúng số block bằng số render_blocks bên dưới
- Mỗi block: 2-4 câu voiceover hoàn chỉnh, không cụt ngắn, không đơn điệu
- Câu đầu mỗi block tiếp nối tự nhiên từ block trước (không bắt đầu lại từ đầu)
- Câu cuối mỗi block là bridge_line dẫn sang block tiếp theo
- target_words: viết đủ từ đó, không được ít hơn 70% target
"""
            prompt += (
                "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "FACT LOCK / CHỐNG BỊA NỘI DUNG\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "- Mỗi câu trong script_blocks phải bám bằng chứng của chính block: SRT, visual_anchor, dialogue_text, scene_ids.\n"
                "- Nếu block có MUST_MENTION, script_blocks.text của block đó BẮT BUỘC nhắc đúng vật thể/hành động/nhân vật trong MUST_MENTION.\n"
                "- Cảnh đắt giá phải được gọi tên cụ thể, ví dụ 'đầu lâu dưới ao', 'Sở Sở cầm lên'; không được thay bằng 'chi tiết này' hoặc 'manh mối đó'.\n"
                "- Nếu SRT/anchor nguồn là tiếng Trung/Anh/ngôn ngữ khác, hãy dịch đúng Ý của anchor sang tiếng Việt có dấu rồi mới viết review.\n"
                "- Nếu một book thiếu cả SRT và visual anchor thật, chỉ viết câu nối trung tính theo hình/nhịp, không bịa plot mới.\n"
                "- Không tự bịa tên nhân vật, quan hệ, động cơ, hung thủ, vật chứng, địa điểm hoặc kết cục nếu evidence không nói rõ.\n"
                "- Nếu evidence ít thoại, chỉ mô tả điều nhìn thấy/hệ quả cảm xúc; không suy diễn thành plot mới.\n"
                "- Nếu chưa chắc, viết trung tính: 'cảnh này cho thấy...', 'manh mối bắt đầu lộ ra...', thay vì khẳng định sai.\n"
            )
        if cut_context:
            prompt += f"\nNGỮ CẢNH CẮT VIDEO:\n{cut_context}\n"
        if movie_description:
            prompt += f"\nTƯ LIỆU PHIM (bám sát, không bịa thêm):\n{movie_description[:7000]}\n"
        if subtitle_context:
            prompt += f"\nNGỮ CẢNH SRT:\n{subtitle_context[:3500]}\n"
        if timed_subtitles:
            prompt += (
                "\nSRT THOẠI NGUỒN CÓ MỐC THỜI GIAN:\n"
                "Dùng để hiểu diễn biến và nhân vật. Viết đầu ra bằng tiếng Việt.\n"
                f"{timed_subtitles[:8000]}\n"
            )
        if mode != "revise":
            prompt += (
                "\n━━━ NÂNG CẤP CHẤT LƯỢNG ━━━\n"
                "- Viết như biên kịch review cao cấp để xuất bản YouTube/TikTok/Facebook Việt Nam.\n"
                "- Khung 5 nhịp bắt buộc: Hook mở, Setup, Escalation, Climax, Ending/hậu vị.\n"
                "- Hook 1-2 câu đầu PHẢI gây tò mò bằng biến cố thật trong phim, không mở đầu chung chung.\n"
                "- Không mở kiểu 'Hôm nay mình sẽ review...' hoặc 'Phim này kể về...'.\n"
                "- Mỗi đoạn phải rõ: đang thấy gì trên hình, nhân vật nào gặp vấn đề gì, hệ quả là gì.\n"
                "- Giọng review gay cấn, có cảm xúc, có phân tích động cơ nhân vật, không liệt kê khô.\n"
                "- Thứ tự bắt buộc phải theo thứ tự thời gian của video đã băm, không được mở đầu bằng cảnh ở giữa/phần sau.\n"
            )
        prompt_pack = recap_prompt_pack_text()
        block_prompt_rules = recap_block_prompt_rules()
        role_rules = narrative_role_rules_text()
        if prompt_pack or block_prompt_rules:
            prompt += (
                "\nRECAP PROMPT PACK (APPLY TO AI REVIEW):\n"
                f"{prompt_pack}\n\n"
                "BLOCK PROMPT RULES (APPLY TO EVERY BLOCK):\n"
                f"{block_prompt_rules}\n\n"
                "NARRATIVE ROLE MAP (OPENING / BODY / ENDING):\n"
                f"{role_rules}\n"
            )

        if visual_context:
            prompt += (
                "\nVISUAL TIMELINE VIDEO ĐÃ BĂM:\n"
                f"{visual_context[:5000]}\n"
            )
        if block_context:
            prompt += (
                "\n━━━ BLOCK SCENE CARDS — BẮT BUỘC BÁM THEO ━━━\n"
                "Mỗi dòng = 1 block video đã băm với thoại SRT tương ứng.\n"
                "Viết nội dung voice của TỪNG block theo đúng thứ tự, bám cảnh đó.\n"
                "KHÔNG nhảy cảnh, KHÔNG trộn lẫn nội dung giữa các block.\n"
                "Nếu block có MUST_MENTION thì text của block phải nhắc cụ thể chi tiết đó, không dùng câu thay thế chung chung.\n\n"
                f"{block_context[:32000]}\n"
            )
        if mode != "revise" and quality_report:
            issues = [str(item).strip() for item in (quality_report.get("issues") or []) if str(item).strip()]
            problem_books = [str(item).strip() for item in (quality_report.get("problem_books") or []) if str(item).strip()]
            suggestions = [str(item).strip() for item in (quality_report.get("suggestions") or []) if str(item).strip()]
            prompt += (
                "\nSTORY POLISH ENGINE:\n"
                f"- Base quality issues: {', '.join(issues[:12]) if issues else 'none'}.\n"
                f"- Problem books: {', '.join(problem_books[:12]) if problem_books else 'none'}.\n"
            )
            if suggestions:
                prompt += f"- Suggested fixes: {' | '.join(suggestions[:6])}.\n"
            prompt += (
                "- Rewrite the affected books so the review opens stronger, flows smoother, and avoids flat or repeated openings.\n"
                "- Keep the same timeline order and keep each book anchored to its own visual/SRT evidence.\n"
                "- If a book feels weak, open it with conflict, consequence, or a vivid detail instead of a generic recap.\n"
                "- End each book with a bridge line that pulls naturally into the next book.\n"
                "- Prefer 2-5 voice lines per book with a clear beat shift: observe -> explain -> connect -> bridge.\n"
            )
        if render_blocks:
            beat_mode = any(isinstance(block, dict) and block.get("recap2_beat_mode") for block in render_blocks)
            if beat_mode:
                prompt += (
                    "\nSCRIPT BLOCK STRATEGY - RECAP2 BEAT-FIRST:\n"
                    "- Mỗi render_block bên dưới là một beat recap có thể gom nhiều cảnh nhỏ, không phải một dòng phụ đề lẻ.\n"
                    "- Hãy viết mỗi beat thành một đoạn voiceover liền mạch, phủ đủ timeline start-end của beat đó.\n"
                    "- source_block_ids/source_block_count cho biết các cảnh gốc đã được gom vào beat; dùng SRT/visual/character_focus để nối chúng thành review tự nhiên.\n"
                    "- Dùng thời lượng beat như gợi ý nhịp kể; ưu tiên bám anchor, nối mạch tự nhiên và không ép từng beat phải khớp giây tuyệt đối.\n"
                )
            prompt += "\nVIDEO ĐÃ BĂM ĐƯỢC CHIA THÀNH CÁC BLOCK NỘI DUNG NHỎ:\n"
            for block_index, block in enumerate(render_blocks, 1):
                duration = float(block.get('duration', 0.0))
                block_id = block.get("block_id") or block.get("book_id") or "?"
                start_in_final = float(block.get("start_in_final_video") or 0.0)
                end_in_final = float(block.get("end_in_final_video") or (start_in_final + duration) or 0.0)
                target = int(block.get("target_words") or max(8, int(duration * 3.0) + 4))
                min_target = max(8, int(target * 0.70))
                max_target = max(min_target + 4, int(target * 1.30))
                reason = block.get('cut_reason') or block.get('reason') or ''
                reason_text = f"Ly do chon block: {reason}. " if reason else ""
                srt_range = block.get('srt_range') or block.get('original_srt_range')
                srt_timeline = block.get('srt_timeline') or "cut/source"
                srt_anchor = block.get('srt_anchor') or block.get('scene_anchor') or ''
                role_meta = narrative_role_for_position(block_index, len(render_blocks))
                scene_role = block.get('scene_role_label') or block.get('scene_role') or role_meta.get('scene_role_label') or role_meta.get('scene_role') or ''
                book_title = block.get('book_title') or scene_role or ''
                narrative_goal = block.get('narrative_goal') or ''
                bridge_hint = block.get('bridge_line') or block.get('bridge_to_next') or ''
                beat = block.get('beat') or ''
                scene_ids = block.get('scene_ids') or ([block.get('scene_id')] if block.get('scene_id') is not None else [])
                dialogue_text = block.get('dialogue_text') or ''
                visual_anchor = block.get('visual_anchor') or block.get('visual_hint') or ''
                visual_notes = block.get('visual_notes') or ''
                visual_source = block.get('visual_evidence_source') or ''
                block_character_focus = (
                    block.get('character_focus')
                    or block.get('focus_characters')
                    or block.get('main_characters')
                    or ''
                )
                semantic_reason = block.get('semantic_reason') or block.get('source') or reason
                must_mention = PremiumReviewPipeline._anchor_phrase(
                    block.get('must_mention'),
                    visual_anchor,
                    visual_notes,
                    srt_anchor,
                    dialogue_text,
                    reason,
                    limit=160,
                )
                prompt += (
                    f"- Block {block_id} ({start_in_final:.1f}s - "
                    f"{end_in_final:.1f}s): "
                    f"SRT/time neo ({srt_timeline}): {srt_range}. "
                    f"Scene_ids: {scene_ids or '[unknown]'}. "
                    f"Source_block_ids: {block.get('source_block_ids') or '[none]'}. "
                    f"Source_block_count: {block.get('source_block_count') or 1}. "
                    f"Thoai neo cua chinh block: {str(srt_anchor or dialogue_text)[:420] or '[khong co thoai]'}. "
                    f"Dialogue_text semantic: {str(dialogue_text)[:420] or '[khong co]'}. "
                    f"Visual_anchor: {str(visual_anchor)[:240] or '[khong co]'}. "
                    f"Visual_notes: {str(visual_notes)[:280] or '[khong co]'}. "
                    f"Visual_source: {str(visual_source)[:80] or '[khong co]'}. "
                    f"Character_focus: {str(block_character_focus)[:180] or '[khong co]'}. "
                    f"MUST_MENTION: {str(must_mention)[:180] or '[khong co]'}. "
                    f"Semantic/source: {str(semantic_reason)[:160] or '[khong co]'}. "
                    f"Vai trò cảnh/kịch bản: {scene_role}. "
                    f"Book title: {book_title}. "
                    f"Beat: {beat}. "
                    f"Goal: {narrative_goal}. "
                    f"Bridge: {bridge_hint}. "
                    f"Viet noi dung khop canh/book nay: target_words={target}, min_words={min_target}, max_words={max_target}. "
                    "Neu viet thieu min_words thi voice se ngan va bi lech video. Khong de noi dung tran sang block khac.\n"
                )
            prompt += (
                "Mỗi script_blocks item nên có thêm: book_id, book_title, beat, scene_role, narrative_goal, bridge_line, duration_hint_seconds, visual_anchor.\n"
                "Mỗi script_blocks item nên giữ lại scene_ids, dialogue_text hoặc srt_anchor nếu có để hậu kiểm bám phim.\n"
                "story_beats là optional nhưng được khuyến nghị; nếu có thì phải là một mảng các beat theo thứ tự timeline.\n"
                "\nYÊU CẦU TRẢ VỀ SCRIPT THEO DẠNG:\n"
                "Khong tra script theo text tu do kieu 'Block 1:'.\n"
                "Phai tra JSON hop le va dat cac doan voice vao khoa script_blocks.\n"
                "Moi script_blocks item phai dung block_id tu danh sach block.\n"
                "Không thêm giải thích, không thêm markdown, chỉ trả về text review voiceover.\n"
            )
        if render_blocks:
            prompt += (
                "\nBOOK TIMELINE LOCK:\n"
                "- Follow the exact book order from the cut video. Book 1 opens first; do not start from a later scene.\n"
                "- Each block must stay inside its own SRT anchor and visual anchor. Do not borrow the next book's scene.\n"
                "- If a block feels generic, rewrite it using that book's own SRT anchor and a forward bridge only.\n"
                "- Only Book 1 may behave like hook_intro; only the final book may behave like ending_aftertaste. Body books must never restart the recap.\n"
                "- The final book/ending_aftertaste must end with exactly ONE short Vietnamese CTA asking viewers to follow the channel and watch the newest/next episode. Body books must not contain CTA.\n"
            )
        if character_focus:
            prompt += f"\nNhân vật trọng tâm: {character_focus[:400]}\n"
        if target_duration_seconds:
            prompt += f"\nMục tiêu thời lượng audio: khoảng {int(target_duration_seconds)} giây.\n"
        if mode != "revise" and timing_hint:
            prompt += f"\nTIMING REWRITE HINT:\n{timing_hint}\n"
        if mode != "revise" and existing_script:
            prompt += (
                "\nBẢN NHÁP HIỆN TẠI CẦN VIẾT LẠI HOẶC MỞ RỘNG:\n"
                f"{existing_script[:12000]}\n"
            )
        if mode != "revise" and render_blocks:
            prompt += (
                "\nSCENE-SCRIPT SYNC ENGINE:\n"
                f"- Bat buoc tra ve dung {len(render_blocks)} script_blocks, khop 1-1 voi render_blocks theo thu tu block_id.\n"
                "- Neu schema phia tren noi 3 khoa thi bo qua, output cuoi cung BAT BUOC co 4 khoa: summary, script, subtitle_chunks, script_blocks.\n"
                "- Moi block chi duoc bam mot canh. Khong nhay sang canh khac trong cung mot block.\n"
                "- Block 1 phai la mo canh / gioi thieu boi canh va nhan vat, khong duoc lao thang vao cao trao.\n"
                "- Doan voice cua block phai dua tren render block, visual_context va SRT cua dung block do.\n"
                "- Moi script_blocks item phai co: block_id, text, target_words, visual_hint, scene_anchor, scene_role, pace, emotion.\n"
                "- Moi script_blocks item nen co them book_id, book_title, beat, narrative_goal, bridge_line, duration_hint_seconds, visual_anchor.\n"
                "- Moi book chi 1 y chinh. Phai co 1 bang chung hinh anh hoac thoai that tu SRT de lam moc neo.\n"
                "- Moi block phai co evidence noi dung: dung tu/chi tiet trong Thoai neo, Dialogue_text semantic hoac Visual_anchor cua block do.\n"
                "- Cam viet claim khong co trong evidence cua block. Dac biet cam tu bia hung thu, quan he, bi mat, dia diem, dong co.\n"
                "- Neu block khong co thoai, chi mo ta hanh dong/khong khi/phan ung nhin thay va noi cau cau noi ngan.\n"
                "- Cau cuoi moi book phai co cau noi sang book ke tiep.\n"
                "- Nhip doc: setup cham hon, tension nhanh hon, climax doan len, payoff ha nhiet.\n"
                "- Moi block/book phai du 70%-130% target_words da yeu cau; day la rang buoc bat buoc de voice du thoi luong.\n"
                "- Neu book co target_words 50 thi text cua book phai khoang 35-65 tu, khong duoc viet 1-2 cau ngan.\n"
                "- Moi text trong script_blocks phai la 2-5 cau voiceover hoan chinh, co cau noi voi book truoc/sau.\n"
                "- Khong lap cac cau mau nhu 'chi tiet nay', 'ngay trong khoanh khac do', 'mach phim o doan nay' qua nhieu block.\n"
                "- Moi block phai co it nhat 1 danh tu hoac hanh dong cu the lay tu MUST_MENTION/SRT/Visual_anchor cua chinh block.\n"
                "- Neu block co Character_focus, phai bam nhan vat do va nhac ten/hanh dong/cam xuc cua ho tu nhien trong text; khong doi sang nhan vat khac neu evidence khong cho phep.\n"
                "- Neu MUST_MENTION la tieng Trung/Anh, dich y do sang tieng Viet co dau trong text, khong che thanh noi dung khac.\n"
                "- Viet theo cong thuc canh thay gi -> y nghia voi nhan vat -> he qua cho canh sau, de voice lien mach nhu mot bai review chuyen nghiep.\n"
                "- Phong cach bat buoc: review phim YouTube hien dai, co dan chuyen, phan tich, cam xuc va luc keo giu chan nguoi xem; khong phai phu de dich.\n"
                "- Moi block can co y kien bien kich: vi sao canh nay dang so/dang nghi/dang buon cuoi/dang nguy hiem, va no day nhan vat den dau.\n"
                "- Khong copy nguyen loi thoai dai. Hay bien loi thoai thanh loi ke review muot, chi giu nhung cum quan trong lam bang chung.\n"
                "- Neu block la canh hanh dong, noi ve hanh dong nhin thay tren man hinh. Neu la hoi thoai, bam sat loi thoai va phan ung cua nhan vat.\n"
                "- Cac block phai noi tiep nhau tu nhien: mo canh -> setup -> day xung dot -> cao trao -> ket/hau vi.\n"
                "- Moi block cuoi phai co cau noi sang block ke tiep, de voice di lien mach khong bi dut doan.\n"
            )
        return prompt

    def generate_review_package(
        self,
        movie_name,
        movie_description="",
        subtitle_context="",
        timed_subtitles="",
        language="Vietnamese",
        target_words=None,
        target_duration_seconds=None,
        character_focus=None,
        keep_seconds=3,
        skip_seconds=10,
        source_duration_seconds=None,
        render_blocks=None,
        visual_context="",
        block_context="",
    ):
        """Generate a review script plus subtitle chunks for app auto-fill."""
        if not self.api_key:
            raise ValueError("Vui lòng nhập API Key")

        if target_words is None:
            if target_duration_seconds and target_duration_seconds > 0:
                target_words = int(target_duration_seconds * 3.0)
            else:
                target_words = 800
        prompt = self._build_review_prompt(
            mode="generate",
            movie_name=movie_name,
            language=language,
            target_words=target_words,
            movie_description=movie_description,
            subtitle_context=subtitle_context,
            timed_subtitles=timed_subtitles,
            character_focus=character_focus,
            target_duration_seconds=target_duration_seconds,
            keep_seconds=keep_seconds,
            skip_seconds=skip_seconds,
            source_duration_seconds=source_duration_seconds,
            render_blocks=render_blocks,
            visual_context=visual_context,
            block_context=block_context,
        )

        try:
            raw = self._try_generate(prompt)
            package = self._parse_review_package_raw(raw, render_blocks)
            package["provider_used"] = getattr(self, "last_provider_used", "") or "gemini_api"
            package["fallback_used"] = bool(getattr(self, "last_fallback_used", False))
            package["voice_narration_style"] = normalize_review_style(os.environ.get("AUTORECAP_REVIEW_STYLE"))
            package["review_style_profile"] = package["voice_narration_style"]
        except Exception as exc:
            if self._is_quota_error(exc) and render_blocks:
                allow_fallback = str(os.environ.get("AUTORECAP_ALLOW_AI_QUOTA_FALLBACK", "") or "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                if not allow_fallback:
                    raise RuntimeError(
                        "AI quota/API provider đang lỗi nên không tạo kịch bản fallback. "
                        "Fallback sẽ làm recap lặp, không bám phim và không đủ chuẩn xuất bản. "
                        "Hãy đổi API key/OpenRouter model hoặc chờ quota hồi rồi chạy lại AI_FULL."
                    ) from exc
                return self._build_quota_fallback_package(
                    render_blocks,
                    target_duration_seconds=target_duration_seconds,
                    reason=str(exc)[:500],
                )
            raise

        render_block_count = len(render_blocks or [])
        target_seconds = float(target_duration_seconds or 0.0)
        if target_seconds <= 0 and render_block_count:
            target_seconds = sum(float(block.get("duration", 0.0) or 0.0) for block in render_blocks)

        script_words, script_seconds = self._estimate_script_metrics(package.get("script", ""))
        script_block_count = len(package.get("script_blocks") or [])
        ratio = script_seconds / target_seconds if target_seconds > 0 else 1.0
        base_sync_report = PremiumReviewPipeline.validate_sync(
            package.get("script_blocks"),
            render_blocks or [],
            target_seconds if target_seconds > 0 else None,
        )
        base_quality_report = PremiumReviewPipeline.validate_story_quality(
            package.get("script_blocks"),
            render_blocks or [],
        )
        base_grounding_report = ScriptGroundingValidator.validate(
            package.get("script_blocks"),
            render_blocks or [],
        )

        def _sync_score(seconds_value, block_count_value, sync_report=None):
            if target_seconds <= 0 and not sync_report:
                return 0.0
            duration_ratio = (
                float(sync_report.get("duration_ratio", 0.0) or 0.0)
                if sync_report
                else ((seconds_value / target_seconds) if target_seconds > 0 and seconds_value > 0 else 0.0)
            )
            word_ratio = float(sync_report.get("word_ratio", duration_ratio) or duration_ratio) if sync_report else duration_ratio
            ratio_gap = max(abs(duration_ratio - 1.0), abs(word_ratio - 1.0)) if duration_ratio > 0 else 1.0
            block_gap = abs(block_count_value - render_block_count) if render_block_count else 0
            quota_issues = 0
            problem_books = 0
            if sync_report:
                issues = sync_report.get("issues") or []
                quota_issues = sum(
                    1
                    for issue in issues
                    if str(issue).startswith(("under_target_words", "over_target_words", "too_short", "missing_script_block"))
                )
                problem_books = len(sync_report.get("problem_books") or [])
            return (ratio_gap * 3.0) + (block_gap * 0.40) + (quota_issues * 0.25) + (problem_books * 0.10)

        def _grounding_penalty(grounding_report=None):
            if not grounding_report:
                return 0.0
            return (
                float(grounding_report.get("weak_block_count", 0) or 0) * 0.22
                + float(grounding_report.get("issue_count", 0) or 0) * 0.08
                + max(0.0, 0.35 - float(grounding_report.get("average_confidence", 0.0) or 0.0)) * 0.5
            )

        should_rewrite = (
            bool(render_block_count)
            and (
                base_sync_report.get("needs_rewrite")
                or script_block_count != render_block_count
                or base_quality_report.get("needs_rewrite")
                or base_grounding_report.get("needs_rewrite")
            )
        )

        sync_rewrite_used = False
        quality_rewrite_used = False
        length_rewrite_used = False
        initial_sync_report = base_sync_report
        initial_quality_report = base_quality_report
        initial_grounding_report = base_grounding_report
        if should_rewrite:
            quality_issue_text = ", ".join(base_quality_report.get("issues", [])[:8]) if base_quality_report.get("issues") else ""
            quality_problem_text = ", ".join(str(book_id) for book_id in (base_quality_report.get("problem_books") or [])[:8])
            if ratio < 0.88:
                repair_target_words = max(int(target_words or 0), int(target_seconds * 2.8))
                timing_hint_parts = [
                    f"Kịch bản hiện tại chỉ ước tính {script_seconds:.1f}s trong khi mục tiêu là "
                    f"{target_seconds:.1f}s. Hãy mở rộng từng block bằng quan sát hình ảnh, phản ứng nhân vật, "
                    "chuyển cảnh và câu nối ngắn để voice đi xuyên suốt video băm."
                ]
            elif ratio > 1.12:
                repair_target_words = max(100, int(target_seconds * 2.3))
                timing_hint_parts = [
                    f"Kịch bản hiện tại ước tính {script_seconds:.1f}s trong khi mục tiêu là "
                    f"{target_seconds:.1f}s. Hãy rút gọn câu, giữ ý chính và vẫn bám đúng thứ tự block."
                ]
            else:
                repair_target_words = max(100, int(target_words or 0))
                timing_hint_parts = [
                    f"Script_blocks hiện chưa khớp số block render ({script_block_count}/{render_block_count}). "
                    "Hãy viết lại để mỗi render block có đúng một script block và nối mạch tự nhiên giữa các đoạn."
                ]
            if base_quality_report.get("needs_rewrite"):
                timing_hint_parts.append(
                    "Story quality report đang báo đoạn mở, chuyển mạch hoặc nhịp kể còn phẳng. "
                    f"Các lỗi chính: {quality_issue_text or 'flat flow'}."
                )
                if quality_problem_text:
                    timing_hint_parts.append(f"Book cần polish mạnh nhất: {quality_problem_text}.")
            if base_grounding_report.get("needs_rewrite"):
                grounding_issues = ", ".join(base_grounding_report.get("issues", [])[:10])
                timing_hint_parts.append(
                    "Factual grounding report báo một số block chưa bám bằng chứng phim. "
                    f"Lỗi chính: {grounding_issues or 'weak evidence overlap'}. "
                    "Viết lại từng block bằng đúng SRT/visual_anchor/dialogue_text của block đó; không dùng claim ngoài evidence."
                )
            if any(
                str(issue).startswith(("timeline_drift", "cross_book_drift", "intro_off_timeline"))
                for issue in (base_quality_report.get("issues") or [])
            ):
                timing_hint_parts.append(
                    "Timeline drift detected: rewrite lại theo đúng thứ tự book của video đã băm. "
                    "Book 1 phải mở trước, và mỗi block chỉ được bám SRT anchor của chính book đó, không được kéo cảnh/book sau lên trước."
                )
            timing_hint = " ".join(timing_hint_parts)

            repair_prompt = self._build_review_prompt(
                mode="generate",
                movie_name=movie_name,
                language=language,
                target_words=repair_target_words,
                movie_description=movie_description,
                subtitle_context=subtitle_context,
                timed_subtitles=timed_subtitles,
                character_focus=character_focus,
                target_duration_seconds=target_duration_seconds or target_seconds,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
                render_blocks=render_blocks,
                visual_context=visual_context,
                block_context=block_context,
                timing_hint=timing_hint,
                existing_script=package.get("script", ""),
                quality_report=base_quality_report,
            )
            try:
                repair_raw = self._try_generate(repair_prompt)
            except Exception as exc:
                if self._is_quota_error(exc):
                    repair_raw = None
                else:
                    raise
            if not repair_raw:
                repair_package = {}
                repair_words, repair_seconds = 0, 0.0
                repair_sync_report = {"needs_rewrite": True}
                repair_quality_report = {"issue_count": 999, "needs_rewrite": True}
                repair_grounding_report = {"issue_count": 999, "needs_rewrite": True}
                repair_sync_score = float("inf")
                base_sync_score = _sync_score(script_seconds, script_block_count, base_sync_report)
                repair_score = float("inf")
                base_score = base_sync_score + (float(base_quality_report.get("issue_count", 0)) * 0.08) + _grounding_penalty(base_grounding_report)
                can_accept_repair = False
            else:
                repair_package = self._parse_review_package_raw(repair_raw, render_blocks)
                repair_words, repair_seconds = self._estimate_script_metrics(repair_package.get("script", ""))
                repair_sync_report = PremiumReviewPipeline.validate_sync(
                    repair_package.get("script_blocks"),
                    render_blocks or [],
                    target_seconds if target_seconds > 0 else None,
                )
                repair_quality_report = PremiumReviewPipeline.validate_story_quality(
                    repair_package.get("script_blocks"),
                    render_blocks or [],
                )
                repair_grounding_report = ScriptGroundingValidator.validate(
                    repair_package.get("script_blocks"),
                    render_blocks or [],
                )
                repair_sync_score = _sync_score(
                    repair_seconds,
                    len(repair_package.get("script_blocks") or []),
                    repair_sync_report,
                )
                base_sync_score = _sync_score(script_seconds, script_block_count, base_sync_report)
                repair_score = (
                    repair_sync_score
                    + (float(repair_quality_report.get("issue_count", 0)) * 0.08)
                    + _grounding_penalty(repair_grounding_report)
                )
                base_score = (
                    base_sync_score
                    + (float(base_quality_report.get("issue_count", 0)) * 0.08)
                    + _grounding_penalty(base_grounding_report)
                )
                sync_was_bad = bool(base_sync_report.get("needs_rewrite"))
                can_accept_repair = (
                    repair_package.get("script")
                    and (
                        (not sync_was_bad and repair_score <= base_score)
                        or (sync_was_bad and (not repair_sync_report.get("needs_rewrite") or repair_sync_score + 0.05 < base_sync_score))
                        or (base_grounding_report.get("needs_rewrite") and repair_score + 0.05 < base_score)
                    )
                )
            if can_accept_repair:
                package = repair_package
                raw = repair_raw
                script_words, script_seconds = repair_words, repair_seconds
                script_block_count = len(package.get("script_blocks") or [])
                ratio = repair_seconds / target_seconds if target_seconds > 0 else 1.0
                sync_rewrite_used = True
                quality_rewrite_used = bool(base_quality_report.get("needs_rewrite"))
                base_sync_report = repair_sync_report
                base_quality_report = repair_quality_report
                base_grounding_report = repair_grounding_report

        final_sync_report = PremiumReviewPipeline.validate_sync(
            package.get("script_blocks"),
            render_blocks or [],
            target_seconds if target_seconds > 0 else None,
        )
        length_issue = (
            bool(render_block_count)
            and final_sync_report.get("needs_rewrite")
            and (
                float(final_sync_report.get("duration_ratio", 1.0) or 1.0) < 0.88
                or float(final_sync_report.get("word_ratio", 1.0) or 1.0) < 0.88
                or any(str(issue).startswith(("under_target_words", "too_short")) for issue in final_sync_report.get("issues", []))
            )
        )
        if length_issue:
            short_books = [
                item for item in final_sync_report.get("block_word_counts", [])
                if int(item.get("words", 0) or 0) < int(item.get("min_words", 0) or 0)
            ][:16]
            short_book_text = "; ".join(
                f"Book {item.get('book_id')}: {item.get('words')} words, min {item.get('min_words')}, target {item.get('target_words')}"
                for item in short_books
            )
            rescue_target_words = max(
                int(target_words or 0),
                int(final_sync_report.get("target_total_words") or 0),
                int(target_seconds * 2.5) if target_seconds > 0 else 0,
                100,
            )
            length_hint = (
                f"Ban hien tai van qua ngan: duration_ratio={float(final_sync_report.get('duration_ratio', 0.0) or 0.0):.2f}, "
                f"word_ratio={float(final_sync_report.get('word_ratio', 0.0) or 0.0):.2f}. "
                f"Tong target_words bat buoc khoang {rescue_target_words}. "
                "Hay EXPAND dung cac book thieu chu, khong rut gon. "
                "Moi book phai dat min_words theo danh sach va van bam visual_anchor/SRT cua book do. "
                f"Underfilled books: {short_book_text or 'see block targets'}."
            )
            rescue_prompt = self._build_review_prompt(
                mode="generate",
                movie_name=movie_name,
                language=language,
                target_words=rescue_target_words,
                movie_description=movie_description,
                subtitle_context=subtitle_context,
                timed_subtitles=timed_subtitles,
                character_focus=character_focus,
                target_duration_seconds=target_duration_seconds or target_seconds,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
                render_blocks=render_blocks,
                visual_context=visual_context,
                block_context=block_context,
                timing_hint=length_hint,
                existing_script=package.get("script", ""),
                quality_report=base_quality_report,
            )
            try:
                rescue_raw = self._try_generate(rescue_prompt)
            except Exception as exc:
                if self._is_quota_error(exc):
                    rescue_raw = None
                else:
                    raise
            if rescue_raw:
                rescue_package = self._parse_review_package_raw(rescue_raw, render_blocks)
                rescue_words, rescue_seconds = self._estimate_script_metrics(rescue_package.get("script", ""))
                rescue_sync_report = PremiumReviewPipeline.validate_sync(
                    rescue_package.get("script_blocks"),
                    render_blocks or [],
                    target_seconds if target_seconds > 0 else None,
                )
                rescue_quality_report = PremiumReviewPipeline.validate_story_quality(
                    rescue_package.get("script_blocks"),
                    render_blocks or [],
                )
                rescue_grounding_report = ScriptGroundingValidator.validate(
                    rescue_package.get("script_blocks"),
                    render_blocks or [],
                )
                current_sync_score = _sync_score(script_seconds, script_block_count, final_sync_report)
                rescue_sync_score = _sync_score(
                    rescue_seconds,
                    len(rescue_package.get("script_blocks") or []),
                    rescue_sync_report,
                )
                current_total_score = current_sync_score + _grounding_penalty(base_grounding_report)
                rescue_total_score = rescue_sync_score + _grounding_penalty(rescue_grounding_report)
                if rescue_package.get("script") and (
                    not rescue_sync_report.get("needs_rewrite")
                    or rescue_total_score + 0.05 < current_total_score
                ):
                    package = rescue_package
                    raw = rescue_raw
                    script_words, script_seconds = rescue_words, rescue_seconds
                    script_block_count = len(package.get("script_blocks") or [])
                    ratio = rescue_seconds / target_seconds if target_seconds > 0 else 1.0
                    sync_rewrite_used = True
                    length_rewrite_used = True
                    base_sync_report = rescue_sync_report
                    base_quality_report = rescue_quality_report
                    base_grounding_report = rescue_grounding_report

        current_sync_report = PremiumReviewPipeline.validate_sync(
            package.get("script_blocks"),
            render_blocks or [],
            target_seconds if target_seconds > 0 else None,
        )
        current_quality_report = PremiumReviewPipeline.validate_story_quality(
            package.get("script_blocks"),
            render_blocks or [],
        )
        timeline_drift_issues = [
            issue
            for issue in (current_quality_report.get("issues") or [])
            if str(issue).startswith(("timeline_drift", "cross_book_drift", "intro_off_timeline"))
        ]
        if timeline_drift_issues:
            drift_books = ", ".join(str(book_id) for book_id in (current_quality_report.get("problem_books") or [])[:12])
            drift_hint = (
                "Timeline drift detected. Rewrite the review in exact book order. "
                "Book 1 must open first, and each block must stay on its own SRT anchor and visual anchor. "
                f"Affected books: {drift_books or 'see quality report'}. "
                f"Current issues: {', '.join(timeline_drift_issues[:6])}. "
                "Do not borrow the next book's scene into the current one."
            )
            drift_target_words = max(int(target_words or 0), int(current_sync_report.get("target_total_words") or 0), 100)
            drift_prompt = self._build_review_prompt(
                mode="generate",
                movie_name=movie_name,
                language=language,
                target_words=drift_target_words,
                movie_description=movie_description,
                subtitle_context=subtitle_context,
                timed_subtitles=timed_subtitles,
                character_focus=character_focus,
                target_duration_seconds=target_duration_seconds or target_seconds,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
                render_blocks=render_blocks,
                visual_context=visual_context,
                block_context=block_context,
                timing_hint=drift_hint,
                existing_script=package.get("script", ""),
                quality_report=current_quality_report,
            )
            try:
                drift_raw = self._try_generate(drift_prompt)
            except Exception as exc:
                if self._is_quota_error(exc):
                    drift_raw = None
                else:
                    raise
            if drift_raw:
                drift_package = self._parse_review_package_raw(drift_raw, render_blocks)
                drift_words, drift_seconds = self._estimate_script_metrics(drift_package.get("script", ""))
                drift_sync_report = PremiumReviewPipeline.validate_sync(
                    drift_package.get("script_blocks"),
                    render_blocks or [],
                    target_seconds if target_seconds > 0 else None,
                )
                drift_quality_report = PremiumReviewPipeline.validate_story_quality(
                    drift_package.get("script_blocks"),
                    render_blocks or [],
                )
                drift_grounding_report = ScriptGroundingValidator.validate(
                    drift_package.get("script_blocks"),
                    render_blocks or [],
                )
                drift_quality_score = _sync_score(
                    drift_seconds,
                    len(drift_package.get("script_blocks") or []),
                    drift_sync_report,
                ) + (float(drift_quality_report.get("issue_count", 0)) * 0.08) + _grounding_penalty(drift_grounding_report)
                current_quality_score = _sync_score(
                    script_seconds,
                    script_block_count,
                    current_sync_report,
                ) + (float(current_quality_report.get("issue_count", 0)) * 0.08) + _grounding_penalty(base_grounding_report)
                if drift_package.get("script") and (
                    drift_quality_score + 0.05 < current_quality_score
                    or not drift_quality_report.get("needs_rewrite")
                ):
                    package = drift_package
                    raw = drift_raw
                    script_words, script_seconds = drift_words, drift_seconds
                    script_block_count = len(package.get("script_blocks") or [])
                    ratio = drift_seconds / target_seconds if target_seconds > 0 else 1.0
                    sync_rewrite_used = True
                    quality_rewrite_used = True
                    current_sync_report = drift_sync_report
                    current_quality_report = drift_quality_report
                    base_grounding_report = drift_grounding_report

        package["book_map"] = package.get("render_blocks", []) or []
        package["beat_plan"] = package.get("beat_plan", []) if isinstance(package.get("beat_plan"), list) else []
        timing_padding_used = False
        if package["book_map"]:
            enforced_blocks = PremiumReviewPipeline.enforce_block_word_targets(
                package.get("script_blocks"),
                package.get("script", ""),
                package["book_map"],
            )
            if enforced_blocks:
                timing_padding_used = any(block.get("timing_padded") for block in enforced_blocks)
                package["script_blocks"] = enforced_blocks
                package["script"] = "\n\n".join(
                    block.get("text", "") for block in enforced_blocks if block.get("text")
                ).strip() or package.get("script", "")
                package["subtitle_chunks"] = [
                    block.get("text", "") for block in enforced_blocks if block.get("text")
                ]
                script_words, script_seconds = self._estimate_script_metrics(package.get("script", ""))
                ratio = script_seconds / target_seconds if target_seconds > 0 else 1.0

        try:
            pre_vi_report = VietnameseTextGuard.report_blocks(package.get("script_blocks") or [])
            if pre_vi_report.get("needs_repair") or VietnameseTextGuard.needs_diacritic_repair(package.get("script", "")):
                package, vi_report = self.repair_vietnamese_diacritics_package(package, package["book_map"] or render_blocks or [])
                script_words, script_seconds = self._estimate_script_metrics(package.get("script", ""))
                ratio = script_seconds / target_seconds if target_seconds > 0 else 1.0
            else:
                package["vietnamese_diacritics_report"] = pre_vi_report
                package["vietnamese_diacritics_repair_used"] = False
        except Exception as exc:
            package["vietnamese_diacritics_report"] = VietnameseTextGuard.report_blocks(package.get("script_blocks") or [])
            package["vietnamese_diacritics_repair_used"] = False
            package["vietnamese_diacritics_repair_error"] = str(exc)[:500]

        package["sync_report_before_rewrite"] = initial_sync_report
        package["quality_report_before_rewrite"] = initial_quality_report
        package["context_coverage_report"] = PremiumReviewPipeline.render_context_coverage(
            package.get("book_map") or render_blocks or []
        )
        package["target_duration_seconds"] = target_seconds if target_seconds > 0 else None
        package["estimated_script_words"] = script_words
        package["estimated_script_seconds"] = script_seconds
        package["script_sync_ratio"] = ratio
        package["sync_rewrite_used"] = sync_rewrite_used
        package["quality_rewrite_used"] = quality_rewrite_used
        package["length_rewrite_used"] = length_rewrite_used
        package["timing_padding_used"] = timing_padding_used
        package["story_polish_rewrite_used"] = quality_rewrite_used
        package["sync_report"] = PremiumReviewPipeline.validate_sync(
            package.get("script_blocks"),
            package["book_map"],
            target_seconds if target_seconds > 0 else None,
        )
        package["quality_report"] = PremiumReviewPipeline.validate_story_quality(
            package.get("script_blocks"),
            package["book_map"],
        )
        package, grounding_report = ScriptGroundingValidator.annotate_package(
            package,
            package["book_map"] or render_blocks or [],
        )
        package["script_grounding_rewrite_used"] = bool(
            sync_rewrite_used and initial_grounding_report.get("needs_rewrite")
        )
        package["script_grounding_report"] = grounding_report
        return package

    def revise_review_script_with_dialogue(
        self,
        movie_name,
        existing_script,
        timed_subtitles,
        movie_description="",
        language="Vietnamese",
        target_words=None,
        target_duration_seconds=None,
        keep_seconds=3,
        skip_seconds=10,
        source_duration_seconds=None,
    ):
        """Rewrite an existing review script so key claims are anchored to timed SRT dialogue."""
        if not self.api_key:
            raise ValueError("Vui lòng nhập API Key")
        if not existing_script or not existing_script.strip():
            raise ValueError("Chưa có kịch bản để AI sửa")
        if not timed_subtitles or not timed_subtitles.strip():
            raise ValueError("Cần chọn file SRT đã dịch có mốc thời gian")
        prompt = self._build_review_prompt(
            mode="revise",
            movie_name=movie_name,
            language=language,
            target_words=target_words,
            movie_description=movie_description,
            timed_subtitles=timed_subtitles,
            target_duration_seconds=target_duration_seconds,
            keep_seconds=keep_seconds,
            skip_seconds=skip_seconds,
            source_duration_seconds=source_duration_seconds,
            existing_script=existing_script,
        )

        raw = self._try_generate(prompt)
        data = self._extract_json_payload(raw) or {}
        summary = self._clean_review_script_text(data.get("summary", "").strip())
        script = self._clean_review_script_text(data.get("script") or raw.strip())
        subtitle_chunks = data.get("subtitle_chunks") or []
        if not script:
            script = self._clean_review_script_text(raw)
        if not isinstance(subtitle_chunks, list) or not subtitle_chunks:
            subtitle_chunks = self._split_subtitle_chunks(script)
        package = {
            "summary": summary,
            "script": script,
            "subtitle_chunks": [str(chunk).strip() for chunk in subtitle_chunks if str(chunk).strip()],
            "raw": raw,
        }
        try:
            if VietnameseTextGuard.needs_diacritic_repair(package.get("script", "")):
                package, _ = self.repair_vietnamese_diacritics_package(package, [])
        except Exception as exc:
            package["vietnamese_diacritics_repair_error"] = str(exc)[:500]
        return package

    @staticmethod
    def _clean_tts_text(text):
        if not text:
            return ""
        text = unicodedata.normalize("NFC", text)

        # ── 1. Xóa markers kỹ thuật ──────────────────────────────────────────
        text = re.sub(r"\[PACE\s*:?\s*(normal|fast|urgent|slow|calm)\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[(KEEP|CUT)\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[DIALOGUE[^\]]*\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[SCENE[^\]]*\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[B-ROLL[^\]]*\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[[A-Z_][A-Z0-9_:.,\s\-/]*\]?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[\[\]]", "", text)

        # ── 2. Xóa timestamps SRT ──────────────────────────────────────────
        text = re.sub(
            r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}",
            "", text
        )
        text = re.sub(r"\b\d{1,2}:\d{2}:\d{2}[,.]\d{3}\b", "", text)
        text = re.sub(r"\b\d{2}:\d{2}:\d{3}\b", "", text)
        text = re.sub(r"\b\d{1,2}:\d{2}:\d{2}\b", "", text)
        text = re.sub(r"\b\d{1,2}:\d{2}:?\b", "", text)

        # ── 3. Xóa số thứ tự SRT ──────────────────────────────────────────
        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\s*\n\s*\d{1,2}:\d{2}", "", text, flags=re.MULTILINE)
        text = re.sub(r"(\D)\d{1,3}(?=\s*\d{2}:\d{2}:\d{2})", r"\1", text)

        # ── 4. Xóa thời gian ngắn dạng 1s, 3.5s ──
        text = re.sub(r"\b\d+\.?\d*s(?:-\d+\.?\d*s)?\b", "", text)

        # ── 5. Xóa ký tự điều khiển, emoji, URL ──
        text = re.sub(r"[\U00010000-\U0010FFFF]", "", text)
        text = re.sub(r"[\u2600-\u27BF\u2B00-\u2BFF]", "", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\ufeff]", "", text)
        text = re.sub(r"https?://\S+", "", text)

        # ── 6. Xóa CJK/Hangul/Kana/dấu câu CJK ───────────────────────────
        # Mở rộng range để bắt mọi ký tự CJK còn sót (vd: U+6170 "慰")
        text = re.sub(
            r"[\u2E80-\u2EFF\u2F00-\u2FDF\u3000-\u303F"   # CJK Radicals, Kangxi, CJK Symbols
            r"\u3040-\u30FF\u3100-\u312F\u3200-\u32FF"    # Hiragana, Katakana, Bopomofo
            r"\u3300-\u33FF\u3400-\u4DBF\u4E00-\u9FFF"    # CJK Unified + Extension A
            r"\uF900-\uFAFF\uFE30-\uFE4F"                 # CJK Compatibility
            r"\uAC00-\uD7AF"                               # Hangul
            r"\uFF00-\uFFEF"                               # Fullwidth + Halfwidth
            r"，。；：！？、…—～【】《》「」『』〔〕〈〉]+",  # Dấu câu CJK
            " ", text
        )

        # ── 7. Xóa ghi chú đạo diễn / metadata AI ──────────────────────
        director_patterns = [
            r"(?:^|\.\s+|\n)(?:đây là|đoạn này|cảnh này|vì thế|do đó|vì vậy)\s+(?:cần|nên|phải|là)\s+(?:làm rõ|setup|xây dựng|highlight|nhấn mạnh|chuyển)[^.!?\n]*[.!?]?",
            r"\bsetup\s*/\s*xây\s+dựng\b[^.!?\n]*",
            r"\bthay\s+vì\s+lướt\s+qua\s+như\s+một\s+cảnh\s+chuyển\b[^.!?\n]*",
            r"\blàm\s+rõ\s+(?:bước\s+ngoặt|setup|cảnh|đoạn)\s+[^.!?\n]*",
            r"\bđẩy\s+mạch\s+phim\s+sang\s+một\s+bước\s+ngoặt\s+mới\b[^.!?\n]*",
            r"\bcần\s+làm\s+rõ\s+[^.!?\n]*",
            r"\b(?:cảnh này|đoạn này)\s+(?:cần|là|để)\s+(?:highlight|nhấn mạnh|làm rõ|setup|xây dựng)[^.!?\n]*",
            # Pattern "Vì thế đoạn này..." dạng câu độc lập
            r"Vì\s+thế\s+(?:đoạn|cảnh)\s+này[^.!?\n]*[.!?]?",
            # Pattern "Chi tiết" đứng đầu câu hoặc đứng riêng (từ CapCut filter)
            r"(?:^|\s)Chi\s+tiết\s+(?=[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯẠ-Ỹ])",
        ]
        for pattern in director_patterns:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE | re.MULTILINE)

        # Xóa "Chi tiết" đứng trước từ thường khi không có nghĩa (CapCut annotation)
        # Chỉ xóa khi "Chi tiết" ở đầu block hoặc sau dấu chấm, không phải giữa câu có nghĩa
        text = re.sub(r"(?:^|(?<=[.!?])\s*)Chi\s+tiết\s+", " ", text, flags=re.IGNORECASE | re.MULTILINE)
        # Xóa dòng toàn thoại trực tiếp dạng hỏi đáp ngắn (< 8 từ, nhiều dấu ?)
        lines_out = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                lines_out.append("")
                continue
            lower = stripped.lower()
            # Bỏ ghi chú kỹ thuật
            if lower.startswith("(video:") or lower.startswith("video:"):
                continue
            if lower.startswith("(nhac") or lower.startswith("[nhac"):
                continue
            # Bỏ dòng chỉ có ký tự đặc biệt
            if re.match(r"^[\W\d]+$", stripped) and not re.search(
                r"[\w\u00C0-\u024F\u1E00-\u1EFF]", stripped
            ):
                continue
            # Bỏ dòng là ghi chú đạo diễn (bắt đầu bằng từ chỉ hướng dẫn kỹ thuật)
            director_starts = (
                "setup", "xây dựng", "highlight", "nhấn mạnh cảnh",
                "cần làm rõ", "đoạn này cần", "cảnh này cần",
                "vì thế đoạn", "thay vì lướt",
            )
            if any(lower.startswith(ds) for ds in director_starts):
                continue
            lines_out.append(stripped)
        text = "\n".join(lines_out).strip()

        # ── 9. Chuẩn hóa dấu ngoặc kép, HTML entities, markdown ──
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u00ab", '"').replace("\u00bb", '"')
        text = re.sub(r"&[a-zA-Z]+;", " ", text)
        text = re.sub(r"&#\d+;", " ", text)
        text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
        text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
        text = re.sub(r"#{1,6}\s*", "", text)

        # ── 10. Chuẩn hóa khoảng trắng ──
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"^[\s,;.!?–—\-]+", "", text)
        text = re.sub(r"\s+([,;])", r"\1", text)

        return text

    @staticmethod
    def _split_tts_chunks(text, max_chars=1800):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks = []
        current = ""

        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            else:
                sentences = [paragraph]

            for sentence in sentences:
                if not sentence:
                    continue
                if len(sentence) > max_chars:
                    if current.strip():
                        chunks.append(current.strip())
                        current = ""
                    words = sentence.split()
                    piece = ""
                    for word in words:
                        if piece and len(piece) + len(word) + 1 > max_chars:
                            chunks.append(piece.strip())
                            piece = word
                        else:
                            piece = f"{piece} {word}".strip()
                    if piece:
                        current = piece
                    continue
                if current and len(current) + len(sentence) + 2 > max_chars:
                    chunks.append(current.strip())
                    current = sentence
                else:
                    current = f"{current}\n\n{sentence}" if current else sentence

        if current.strip():
            chunks.append(current.strip())
        return chunks or [text]

    @staticmethod
    def _build_cut_timing_context(
        source_duration_seconds=None,
        keep_seconds=3,
        skip_seconds=10,
        max_segments=12,
    ) -> str:
        try:
            keep_seconds = float(keep_seconds or 0)
            skip_seconds = float(skip_seconds or 0)
        except Exception:
            keep_seconds = 0
            skip_seconds = 0

        if source_duration_seconds is None:
            return f"Công thức cắt video: Giữ {keep_seconds:g}s, bỏ {skip_seconds:g}s theo chu kỳ."

        try:
            source_duration_seconds = float(source_duration_seconds or 0)
        except Exception:
            source_duration_seconds = 0

        segments = VideoCutter.get_keep_segments(
            source_duration_seconds,
            keep_seconds,
            skip_seconds,
        )
        if not segments:
            return f"Công thức cắt video: Giữ {keep_seconds:g}s, bỏ {skip_seconds:g}s theo chu kỳ."

        lines = [
            f"Công thức cắt video: Giữ {keep_seconds:g}s, bỏ {skip_seconds:g}s.",
            f"Video nguồn: {source_duration_seconds:.1f}s.",
            f"Tổng số đoạn giữ lại: {len(segments)}.",
            "Mốc giữ mẫu:",
        ]
        if len(segments) <= max_segments:
            sample_segments = segments
        else:
            head_count = max(1, max_segments // 2)
            tail_count = max(1, max_segments - head_count)
            sample_segments = segments[:head_count] + segments[-tail_count:]

        for idx, segment in enumerate(sample_segments, 1):
            lines.append(
                f"- Đoạn {idx}: {segment['start']:.1f}s -> {segment['end']:.1f}s"
            )

        hidden_count = len(segments) - len(sample_segments)
        if hidden_count > 0:
            lines.append(f"... và {hidden_count} đoạn khác theo cùng công thức.")

        return "\n".join(lines)

    @staticmethod
    def _split_text_for_tts_retry(text, min_chars=500):
        text = (text or "").strip()
        if len(text) <= min_chars:
            return [text] if text else []

        sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", text) if s.strip()]
        if len(sentences) > 1:
            midpoint = len(text) // 2
            pieces = []
            current = ""
            for sentence in sentences:
                next_piece = f"{current} {sentence}".strip() if current else sentence
                if current and len(next_piece) > midpoint:
                    pieces.append(current)
                    current = sentence
                else:
                    current = next_piece
            if current:
                pieces.append(current)
            if len(pieces) > 1:
                return pieces

        words = text.split()
        if len(words) < 8:
            return [text]
        midpoint = len(words) // 2
        return [" ".join(words[:midpoint]).strip(), " ".join(words[midpoint:]).strip()]

    @staticmethod
    def _rate_to_int(rate):
        if rate is None:
            return 0
        if isinstance(rate, int):
            return rate
        match = re.search(r"([+-]?\d+)", str(rate))
        return int(match.group(1)) if match else 0

    @staticmethod
    def _int_to_rate(rate_value):
        rate_value = int(rate_value)
        if rate_value > 0:
            return f"+{rate_value}%"
        if rate_value < 0:
            return f"{rate_value}%"
        return "+0%"

    @classmethod
    def _pace_rate(cls, pace, base_rate):
        base = cls._rate_to_int(base_rate)
        # Clamp base rate to safe maximum for Vietnamese voices
        base = max(-25, min(25, base))
        pace = (pace or "normal").lower()
        add = {
            "normal": 0,
            "slow": -5,
            "calm": -8,
            "fast": 5,
            "urgent": 8,
        }.get(pace, 0)
        return cls._int_to_rate(max(-25, min(25, base + add)))

    @classmethod
    def _parse_paced_segments(cls, text, base_rate=None):
        pattern = re.compile(r"\[PACE\s*:\s*(normal|fast|urgent|slow|calm)\]", re.IGNORECASE)
        parts = pattern.split(text or "")
        # Guard: neu text rong sau khi split thi tra ve segment rong
        if not text or not text.strip():
            return []
        segments = []
        current_pace = "normal"

        if parts and parts[0].strip():
            cleaned = cls._clean_tts_text(parts[0])
            if cleaned:
                segments.append((cleaned, cls._pace_rate(current_pace, base_rate)))

        index = 1
        while index < len(parts):
            current_pace = parts[index].strip().lower()
            segment_text = parts[index + 1] if index + 1 < len(parts) else ""
            cleaned = cls._clean_tts_text(segment_text)
            if cleaned:
                segments.append((cleaned, cls._pace_rate(current_pace, base_rate)))
            index += 2

        if not segments:
            cleaned = cls._clean_tts_text(text)
            if cleaned:
                segments.append((cleaned, cls._pace_rate("normal", base_rate)))
        merged = []
        for segment_text, segment_rate in segments:
            if merged and merged[-1][1] == segment_rate:
                merged[-1] = (f"{merged[-1][0]}\n\n{segment_text}", segment_rate)
            else:
                merged.append((segment_text, segment_rate))
        return merged

    @staticmethod
    def _concat_audio_files(part_files, output_path):
        concat_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            ffmpeg_bin = FFmpegUtils.ffmpeg_executable()
            for part in part_files:
                safe_part = part.replace("\\", "/").replace("'", "'\\''")
                concat_file.write(f"file '{safe_part}'\n")
            concat_file.close()
            result = subprocess.run(
                [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat_file.name, "-c", "copy", output_path],
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True
                ),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "Failed to concatenate TTS chunks")
        finally:
            try:
                os.unlink(concat_file.name)
            except Exception:
                pass

    async def text_to_speech_blocks(self, blocks, output_path="temp_v2_blocks.mp3", voice=None, rate=None, progress_callback=None):
        """Generate speech audio from text blocks and concatenate them into one file.

        Args:
            blocks: List of dicts or strings. If dict, expects {'text': ..., 'block_id': ...}.
        """
        if not blocks or not isinstance(blocks, (list, tuple)):
            raise ValueError("Blocks must be a non-empty list")

        segment_files = []
        temp_paths = []
        try:
            for index, block in enumerate(blocks):
                text = block.get("text") if isinstance(block, dict) else str(block)
                text = str(text or "").strip()
                if not text:
                    continue
                part_path = _closed_temp_path(f"_block_{index}.mp3")
                segment_files.append(part_path)
                await self.text_to_speech(text, part_path, voice=voice, rate=rate)
            if not segment_files:
                raise ValueError("No speakable blocks found for TTS")
            self._concat_audio_files(segment_files, output_path)
            return output_path
        finally:
            for path in segment_files:
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass

    @staticmethod
    def calculate_tts_rate(target_duration_seconds, estimated_duration_seconds, min_rate=-25, max_rate=25):
        if not target_duration_seconds or not estimated_duration_seconds:
            return "+0%"

        if target_duration_seconds <= 0 or estimated_duration_seconds <= 0:
            return "+0%"

        ratio = estimated_duration_seconds / target_duration_seconds
        rate = int(round((ratio - 1.0) * 100))
        rate = max(min_rate, min(max_rate, rate))
        return f"{rate:+d}%"

    @classmethod
    def _fallback_rates(cls, rate):
        requested = max(-25, min(25, cls._rate_to_int(rate)))
        # Always include 0% as a safe fallback early in the list
        candidates = [requested]
        if requested != 0:
            candidates.append(0)
        # Add progressively lower rates to try before higher ones
        # This handles "No audio received" which is caused by too-high rates
        for delta in (-5, -10, -15, -20, -25, 5, 10, 15, 20):
            candidate = max(-25, min(25, requested + delta))
            if candidate not in candidates:
                candidates.append(candidate)
        rates = []
        for candidate in candidates:
            if candidate not in rates:
                rates.append(candidate)
        return [cls._int_to_rate(candidate) for candidate in rates]

    @staticmethod
    def _tts_timeout_for_text(text):
        # Edge returns streamed audio; a long narration naturally takes longer to arrive.
        return max(45.0, min(240.0, len((text or "").strip()) * 0.12))

    @staticmethod
    def _speakable_word_count(text):
        clean = re.sub(r"[^\w\u00C0-\u024F\u1E00-\u1EFF]", " ", text or "").strip()
        return len(clean.split())

    @staticmethod
    def _probe_tts_audio_duration(path):
        """Return the real audio duration without flashing an ffprobe window."""
        if not path or not os.path.exists(path):
            return 0.0
        try:
            ffprobe_bin = FFmpegUtils.ffprobe_executable()
            result = subprocess.run(
                [
                    ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", path,
                ],
                **FFmpegUtils.subprocess_kwargs(
                    check=False, capture_output=True, text=True,
                ),
            )
            if result.returncode == 0:
                return max(0.0, float((result.stdout or "0").strip() or 0.0))
        except Exception:
            pass
        return 0.0

    @classmethod
    def _tts_audio_is_complete(cls, text, path, rate):
        """Reject non-empty Edge files that contain only a truncated response."""
        words = cls._speakable_word_count(text)
        duration = cls._probe_tts_audio_duration(path)
        if words < 10 or duration <= 0:
            return True, duration, 0.0
        rate_factor = max(1.0, 1.0 + max(0, cls._rate_to_int(rate)) / 100.0)
        # Vietnamese narration above this density is almost certainly truncated.
        max_plausible_words_per_second = 5.2 * rate_factor
        minimum_duration = words / max_plausible_words_per_second
        return duration + 0.15 >= minimum_duration, duration, minimum_duration

    async def _save_tts_chunk_with_rate_fallbacks(self, text, voice, output_path, rate):
        import asyncio as _asyncio
        # Guard: text phai co it nhat 1 tu co nghia
        if self._speakable_word_count(text) < 1:
            raise ValueError(f"TTS text too short or empty after cleaning: {repr(text[:50])}")
        last_exc = None
        for safe_rate in self._fallback_rates(rate):
            # Retry up to 2 times per rate to handle transient "No audio received" errors.
            for attempt in range(2):
                try:
                    communicate = edge_tts.Communicate(
                        text, voice, rate=safe_rate, boundary="WordBoundary"
                    )
                    boundaries = []

                    async def _stream_to_file():
                        audio_received = False
                        with open(output_path, "wb") as audio_file:
                            async for event in communicate.stream():
                                event_type = str(event.get("type") or "")
                                if event_type == "audio":
                                    data = event.get("data") or b""
                                    if data:
                                        audio_file.write(data)
                                        audio_received = True
                                elif event_type == "WordBoundary":
                                    try:
                                        boundaries.append({
                                            "offset": float(event.get("offset") or 0) / 10_000_000.0,
                                            "duration": float(event.get("duration") or 0) / 10_000_000.0,
                                            "text": str(event.get("text") or ""),
                                        })
                                    except Exception:
                                        pass
                        if not audio_received:
                            raise RuntimeError("TTS stream completed without audio")

                    await _asyncio.wait_for(
                        _stream_to_file(),
                        timeout=self._tts_timeout_for_text(text),
                    )
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        complete, duration, minimum_duration = self._tts_audio_is_complete(
                            text, output_path, safe_rate,
                        )
                        if complete:
                            if boundaries:
                                with open(output_path + ".timing.json", "w", encoding="utf-8") as timing_file:
                                    json.dump(boundaries, timing_file, ensure_ascii=False)
                            return safe_rate
                        raise RuntimeError(
                            "TTS audio incomplete: "
                            f"{duration:.2f}s < {minimum_duration:.2f}s for "
                            f"{self._speakable_word_count(text)} words"
                        )
                    last_exc = RuntimeError("TTS save completed but output file is empty")
                except Exception as e:
                    last_exc = e
                    try:
                        if os.path.exists(output_path):
                            os.unlink(output_path)
                        if os.path.exists(output_path + ".timing.json"):
                            os.unlink(output_path + ".timing.json")
                    except Exception:
                        pass
                    err_str = f"{type(e).__name__}: {e}".lower()
                    if any(k in err_str for k in ("no audio", "noaudio", "websocket", "connection", "timeout")):
                        # Longer backoff on "no audio": edge-tts needs time before retry
                        wait = 2.0 * (attempt + 1)
                        await _asyncio.sleep(wait)
                        continue
                    break
            # After exhausting retries for this rate, add a small delay before trying next rate
            await _asyncio.sleep(1.0)
        raise last_exc

    async def _save_tts_chunk_resilient(self, text, voices, output_path, rate, depth=0):
        last_exc = None
        voices = [voice for voice in voices if voice]
        for voice in voices:
            try:
                used_rate = await self._save_tts_chunk_with_rate_fallbacks(text, voice, output_path, rate)
                return voice, used_rate
            except Exception as exc:
                last_exc = exc

        retry_pieces = self._split_text_for_tts_retry(text, min_chars=180)
        if depth < 2 and len(retry_pieces) > 1:
            part_files = []
            voices_used = []
            try:
                for piece_index, piece in enumerate(retry_pieces):
                    part_path = _closed_temp_path(f"_retry_{depth}_{piece_index}.mp3")
                    part_files.append(part_path)
                    used_voice, _used_rate = await self._save_tts_chunk_resilient(
                        piece, voices, part_path, rate, depth + 1
                    )
                    voices_used.append(used_voice)
                self._concat_audio_files(part_files, output_path)
                self._merge_tts_timing_sidecars(part_files, output_path)
                return (voices_used[0] if voices_used else voices[0]), rate
            except Exception as split_exc:
                last_exc = split_exc
            finally:
                for part_path in part_files:
                    try:
                        if os.path.exists(part_path):
                            os.unlink(part_path)
                        if os.path.exists(part_path + ".timing.json"):
                            os.unlink(part_path + ".timing.json")
                    except Exception:
                        pass

        raise last_exc or RuntimeError("Unable to synthesize TTS chunk")

    @staticmethod
    def _clean_output_file(path):
        try:
            if path and os.path.exists(path):
                os.unlink(path)
            if path and os.path.exists(path + ".timing.json"):
                os.unlink(path + ".timing.json")
        except Exception:
            pass

    @classmethod
    def _merge_tts_timing_sidecars(cls, part_files, output_path):
        merged = []
        offset = 0.0
        for part_path in part_files or []:
            sidecar = str(part_path) + ".timing.json"
            try:
                with open(sidecar, "r", encoding="utf-8") as timing_file:
                    entries = json.load(timing_file)
            except Exception:
                entries = []
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                item = dict(entry)
                item["offset"] = round(offset + float(item.get("offset") or 0.0), 6)
                merged.append(item)
            offset += cls._probe_tts_audio_duration(part_path)
        if merged:
            with open(str(output_path) + ".timing.json", "w", encoding="utf-8") as timing_file:
                json.dump(merged, timing_file, ensure_ascii=False)

    async def text_to_speech(self, text, output_path="temp_v2.mp3", voice=None, rate=None, progress_callback=None):
        """Generate speech audio file; verify voice availability first.
        
        Voice priority:
        1. Piper offline (if voice starts with 'piper:')
        2. User-specified edge-tts voice
        3. Vietnamese voices (vi-VN-*)
        4. Any available voice (fallback)
        """
        # ── Piper TTS offline ────────────────────────────────────────────────
        v_str = str(voice or "").strip()
        try:
            from engine.piper_tts import is_piper_voice, synthesize_piper
            if is_piper_voice(v_str):
                import asyncio as _aio
                import os as _os2
                import subprocess as _sp2

                out_p = output_path
                # Tạo wav path an toàn — thêm _piper suffix để tránh trùng
                base_no_ext = _os2.path.splitext(out_p)[0]
                wav_p = base_no_ext + "_piper.wav"
                is_mp3 = out_p.lower().endswith(".mp3")

                # Parse speed từ rate (e.g. "+15%" → 1.15)
                piper_speed = 1.0
                try:
                    r_str = str(rate or "+0%").replace("%", "").strip()
                    piper_speed = max(0.5, min(2.0, 1.0 + float(r_str) / 100.0))
                except Exception:
                    pass

                # Chạy Piper trong executor (blocking → non-blocking)
                loop = _aio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: synthesize_piper(text, wav_p, v_str, piper_speed)
                )

                # Verify WAV tạo thành công
                if not _os2.path.exists(wav_p) or _os2.path.getsize(wav_p) == 0:
                    raise RuntimeError(f"Piper không tạo được WAV: {wav_p}")

                if is_mp3:
                    # Convert WAV → MP3 bằng ffmpeg
                    try:
                        from utils.helpers import FFmpegUtils
                        ffmpeg_exe = FFmpegUtils.ffmpeg_executable()
                    except Exception:
                        ffmpeg_exe = None
                    if not ffmpeg_exe:
                        import shutil as _sh2
                        ffmpeg_exe = _sh2.which("ffmpeg") or "ffmpeg"

                    ret = _sp2.run(
                        [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
                         "-i", wav_p, "-c:a", "libmp3lame", "-b:a", "192k", out_p],
                        check=False, capture_output=True
                    )
                    # Xóa WAV tạm
                    try:
                        _os2.unlink(wav_p)
                    except Exception:
                        pass
                    # Verify MP3
                    if not _os2.path.exists(out_p) or _os2.path.getsize(out_p) == 0:
                        raise RuntimeError(f"ffmpeg convert WAV→MP3 thất bại (code {ret.returncode})")
                else:
                    # Output là WAV — rename
                    if wav_p != out_p:
                        try:
                            _os2.replace(wav_p, out_p)
                        except Exception:
                            pass

                return v_str  # Trả về voice ID đã dùng
        except ImportError:
            pass  # piper_tts module chưa có → fallback edge-tts
        except Exception as piper_e:
            # Chỉ raise nếu đây thật sự là Piper voice, không fallback sang edge-tts
            try:
                from engine.piper_tts import is_piper_voice as _ipv
                if _ipv(v_str):
                    raise RuntimeError(f"Piper TTS lỗi ({v_str}): {piper_e}") from piper_e
            except ImportError:
                pass
        # ── end Piper ─────────────────────────────────────────────────────────
        segments = self._parse_paced_segments(text, rate)
        if not segments:
            raise ValueError("Text for TTS is empty after cleaning")
        # Loc bo segment rong; cho phep segment 1 tu de block ngan van doc duoc
        segments = [
            (seg_text, seg_rate) for seg_text, seg_rate in segments
            if self._speakable_word_count(seg_text) >= 1
        ]
        if not segments:
            raise ValueError("Text for TTS has no speakable content after cleaning")

        import time as _time
        import asyncio as _asyncio_lv
        # Dung cache neu con hieu luc (tranh goi network moi lan)
        now = _time.monotonic()
        if AIEngine._voice_cache and (now - AIEngine._voice_cache_time) < AIEngine._VOICE_CACHE_TTL:
            available = AIEngine._voice_cache
        else:
            try:
                # Timeout 8 giay cho list_voices
                available = await _asyncio_lv.wait_for(edge_tts.list_voices(), timeout=8.0)
                AIEngine._voice_cache = available
                AIEngine._voice_cache_time = now
            except Exception:
                # Neu loi, dung cache cu hoac danh sach mac dinh
                available = AIEngine._voice_cache or []
                if not available:
                    # Fallback: dung 2 voices Viet Nam mac dinh, khong can list
                    available = [
                        {"ShortName": "vi-VN-HoaiMyNeural"},
                        {"ShortName": "vi-VN-NamMinhNeural"},
                    ]

        available_names = [v.get("ShortName") for v in available if v.get("ShortName")]
        
        # Prioritize Vietnamese voices
        vi_voices = [name for name in available_names if name.startswith("vi-")]
        
        # Build list of voices to try
        voices_to_try = []
        
        preferred_voice = voice or "vi-VN-HoaiMyNeural"
        force_vietnamese = preferred_voice.startswith("vi-")

        # Preserve one narrator voice for the complete output. Fall back only when
        # the chosen voice is not advertised by Edge, never between audio chunks.
        if not available_names or preferred_voice in available_names:
            voices_to_try = [preferred_voice]
        elif force_vietnamese:
            voices_to_try = [
                candidate for candidate in ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")
                if candidate in vi_voices
            ][:1]
        else:
            voices_to_try = available_names[:1]

        if not voices_to_try:
            voices_to_try = [preferred_voice]

        tts_units = []
        for segment_text, segment_rate in segments:
            for chunk in self._split_tts_chunks(segment_text):
                if self._speakable_word_count(chunk) >= 1:
                    tts_units.append((chunk, segment_rate))
        if not tts_units:
            raise ValueError("Text for TTS has no speakable chunks after cleaning")

        last_exc = None
        voices_used = []
        total_units = len(tts_units)

        def _emit_progress(done_units):
            if not progress_callback:
                return
            try:
                percent = int(round((done_units / max(total_units, 1)) * 100))
                percent = max(0, min(100, percent))
                progress_callback(percent, done_units, total_units)
            except Exception:
                pass

        try:
            _emit_progress(0)
            if len(tts_units) == 1:
                self._clean_output_file(output_path)
                used_voice, _used_rate = await self._save_tts_chunk_resilient(
                    tts_units[0][0], voices_to_try, output_path, tts_units[0][1]
                )
                voices_used.append(used_voice)
                _emit_progress(1)
            else:
                part_files = [
                    _closed_temp_path(f"_{part_index}.mp3")
                    for part_index in range(len(tts_units))
                ]
                try:
                    voices_used = [None] * len(tts_units)
                    completed_units = 0

                    max_parallel = min(2, len(tts_units))
                    semaphore = _asyncio_lv.Semaphore(max_parallel)

                    async def _run_tts_part(part_index, chunk, chunk_rate):
                        nonlocal completed_units
                        async with semaphore:
                            try:
                                used_voice, _used_rate = await self._save_tts_chunk_resilient(
                                    chunk, voices_to_try, part_files[part_index], chunk_rate
                                )
                                voices_used[part_index] = used_voice
                                completed_units += 1
                                _emit_progress(completed_units)
                            except Exception as chunk_exc:
                                raise RuntimeError(
                                    f"L?i TTS t?i ?o?n {part_index + 1}/{len(tts_units)}: {repr(chunk_exc)}"
                                ) from chunk_exc

                    await _asyncio_lv.gather(*[
                        _run_tts_part(part_index, chunk, chunk_rate)
                        for part_index, (chunk, chunk_rate) in enumerate(tts_units)
                    ])

                    self._concat_audio_files(part_files, output_path)
                    self._merge_tts_timing_sidecars(part_files, output_path)
                finally:
                    for part_path in part_files:
                        try:
                            if os.path.exists(part_path):
                                os.unlink(part_path)
                            if os.path.exists(part_path + ".timing.json"):
                                os.unlink(part_path + ".timing.json")
                        except Exception:
                            pass
            if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
                raise RuntimeError("TTS save completed but output file is empty")
            return voices_used[0] if voices_used else preferred_voice
        except Exception as e:
            last_exc = e

        voice_list_str = ", ".join(available_names[:10]) + ("..." if len(available_names) > 10 else "")
        raise RuntimeError(
            f"No audio was received from TTS. Requested voice: {preferred_voice}. "
            f"Force Vietnamese: {force_vietnamese}. Tried {len(voices_to_try)} voices: {voices_to_try}. "
            f"Vietnamese voices available: {len(vi_voices)}. "
            f"Sample available: {voice_list_str}. Last error: {repr(last_exc)}"
        )




