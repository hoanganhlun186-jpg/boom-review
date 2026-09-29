"""Offline regression tests for the third-party 11labs.id.vn adapter."""
import asyncio
import ast
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import elevenlabs_vn_tts as provider


def audio_fixture():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    return data.getvalue()


def response(status=200, payload=None, content=b""):
    return Mock(status_code=status, json=Mock(return_value=payload or {}), content=content)


class ElevenLabsVnTests(unittest.TestCase):
    def test_ui_shows_only_selected_provider_voice_names(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "main.py").read_text(encoding="utf-8-sig"))
        names = {"get_voice_options", "_resolve_voice_id", "_is_voice_id", "_is_vietnamese_language"}
        methods = [
            node for cls in tree.body if isinstance(cls, ast.ClassDef)
            for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        cls = ast.ClassDef(name="VoiceUI", bases=[], keywords=[], body=methods, decorator_list=[])
        namespace = {"re": re}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "main.py", "exec"), namespace)
        ui = namespace["VoiceUI"]()
        ui.tts_provider = Mock()
        ui.tts_provider.get.return_value = "11LABS VN"
        ui._elevenlabs_vn_voices = [
            {"id": "voice_a", "name": "Giọng Một"},
            {"id": "voice_b", "name": "Giọng Hai"},
        ]
        choices = ui.get_voice_options("Tiếng Việt")
        self.assertEqual(choices, ["Giọng Một", "Giọng Hai"])
        self.assertNotIn("11labsvn:", " ".join(choices))
        self.assertEqual(ui._resolve_voice_id("Giọng Hai", "Tiếng Việt"), "11labsvn:voice_b")

    def test_fetch_voices_normalizes_schema(self):
        payload = {"status": "success", "voices": [
            {"voice_id": "voice_a", "voice_name": "Giọng A", "category": "vbee"},
            {"id": "voice_b", "name": "Giọng B", "provider": "elevenlabs"},
        ]}
        with patch.object(provider.requests, "get", return_value=response(payload=payload)) as get:
            voices = provider.fetch_voices("test")
        self.assertEqual([v["id"] for v in voices], ["voice_a", "voice_b"])
        self.assertEqual(voices[0]["name"], "Giọng A")
        self.assertEqual(get.call_args.kwargs["headers"], {"x-api-key": "test"})

    def test_job_is_resumed_without_second_paid_post(self):
        completed = {"status": "completed", "download_url": "https://11labs.id.vn/audio/a.mp3"}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider.requests, "post", return_value=response(payload={"job_id": "JOB1"})) as post, \
                patch.object(provider.requests, "get", side_effect=[response(payload=completed), response(content=audio_fixture())]) as get:
            target = Path(folder) / "voice.mp3"
            provider.synthesize_11labs_vn("Xin chào.", target, "11labsvn:voice_a", api_key="test", poll_interval=0)
            target.unlink()
            provider.synthesize_11labs_vn("Xin chào.", target, "11labsvn:voice_a", api_key="test", poll_interval=0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(get.call_count, 2)

    def test_uncertain_submission_is_not_reposted(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider.requests, "post", side_effect=provider.requests.Timeout()) as post:
            target = Path(folder) / "voice.mp3"
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "trừ quota hai lần"):
                    provider.synthesize_11labs_vn("Xin chào.", target, "11labsvn:voice_a", api_key="test")
        self.assertEqual(post.call_count, 1)

    def test_parallel_calls_keep_jobs_and_outputs_separate(self):
        counter = 0
        counter_lock = threading.Lock()

        def create_job(*args, **kwargs):
            nonlocal counter
            with counter_lock:
                counter += 1
                job_id = f"JOB{counter}"
            return response(payload={"status": "success", "job_id": job_id})

        def get_result(url, **kwargs):
            if url.endswith("status.php"):
                job_id = kwargs["params"]["job_id"]
                return response(payload={
                    "status": "completed",
                    "download_url": f"https://11labs.id.vn/audio/{job_id}.mp3",
                })
            return response(content=audio_fixture())

        async def run(folder):
            await asyncio.gather(*[
                asyncio.to_thread(
                    provider.synthesize_11labs_vn,
                    f"Đoạn thử {i}.",
                    Path(folder) / f"voice_{i}.mp3",
                    "11labsvn:voice_a",
                    "+0%",
                    "test",
                    None,
                    60,
                    0,
                )
                for i in range(3)
            ])

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider.requests, "post", side_effect=create_job) as post, \
                patch.object(provider.requests, "get", side_effect=get_result):
            asyncio.run(run(folder))
            self.assertEqual(post.call_count, 3)
            self.assertTrue(all((Path(folder) / f"voice_{i}.mp3").stat().st_size > 100 for i in range(3)))

    def test_dispatchers_do_not_fall_through_to_edge(self):
        from engine.ai_engine import AIEngine
        from core.voice_segments import VoiceSegmentsGenerator

        async def fake_synthesize(text, output, voice, rate="+0%", api_key=None, **kwargs):
            Path(output).write_bytes(audio_fixture())

        def blocking_fake(text, output, voice, rate="+0%", api_key=None, **kwargs):
            Path(output).write_bytes(audio_fixture())

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider, "get_api_key", return_value="test"), \
                patch.object(provider, "synthesize_11labs_vn", side_effect=blocking_fake), \
                patch("edge_tts.Communicate", side_effect=AssertionError("Must not use Edge")):
            ai = AIEngine.__new__(AIEngine)
            target = str(Path(folder) / "engine.mp3")
            self.assertEqual(
                asyncio.run(ai.text_to_speech("Xin chào.", target, "11labsvn:voice_a")),
                "11labsvn:voice_a",
            )
            generator = VoiceSegmentsGenerator.__new__(VoiceSegmentsGenerator)
            duration = asyncio.run(generator.generate_segment_audio(
                "Xin chào.", str(Path(folder) / "segment.mp3"), "11labsvn:voice_a"
            ))
            self.assertGreater(duration, 0.9)

    def test_config_encrypts_new_key(self):
        from config import ConfigManager

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            manager = ConfigManager(str(path))
            self.assertTrue(manager.save({"elevenlabs_vn_api_key": "PL_secret"}))
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raw["elevenlabs_vn_api_key"].startswith("ENC:"))
            self.assertNotIn("PL_secret", path.read_text(encoding="utf-8"))
            self.assertEqual(manager.load()["elevenlabs_vn_api_key"], "PL_secret")


if __name__ == "__main__":
    unittest.main()
