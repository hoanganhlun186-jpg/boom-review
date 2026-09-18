"""Offline regression checks; no live Pekka calls or credits used."""
import ast
import asyncio
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import pekka_tts as pekka
from utils.helpers import FFmpegUtils


def audio_fixture():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    return data.getvalue()


def response(status=200, payload=None, content=b""):
    return Mock(status_code=status, json=Mock(return_value=payload), content=content)


class PekkaTests(unittest.TestCase):
    def test_all_bundled_samples_are_available(self):
        from engine.pekka_samples import bundled_voices, bundled_sample_path
        voices = bundled_voices()
        self.assertEqual(len(voices), 85)
        self.assertEqual(len({v['id'] for v in voices}), 85)
        for voice in voices:
            path = bundled_sample_path('pekka:' + voice['id'])
            self.assertIsNotNone(path)
            self.assertGreater(Path(path).stat().st_size, 100)
        self.assertIsNone(bundled_sample_path('vi-VN-HoaiMyNeural'))
        self.assertIsNone(bundled_sample_path('pekka:missing'))

    def test_packaged_sample_lookup_uses_exe_directory(self):
        from engine import pekka_samples
        import json
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'assets' / 'pekka_samples'
            directory.mkdir(parents=True)
            (directory / 'test.mp3').write_bytes(b'x' * 101)
            (directory / 'manifest.json').write_text(json.dumps([
                {'id': 'test', 'name': 'Sample', 'file': 'test.mp3'}]))
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(Path(folder) / 'BoomReview.exe')):
                self.assertEqual(pekka_samples.bundled_sample_path('pekka:test'), str(directory / 'test.mp3'))

    def test_preview_plays_bundled_file_without_key_or_network(self):
        from engine.pekka_samples import bundled_voices, bundled_sample_path
        from types import SimpleNamespace
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8-sig'))
        method = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_preview_voice_sample')
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'main.py', 'exec'), namespace)
        voice = 'pekka:' + bundled_voices()[0]['id']
        ui = Mock()
        ui.voice_choice.get.return_value = voice
        ui.tts_language.get.return_value = 'Tiếng Việt'
        ui._resolve_voice_id.return_value = voice
        ui.pekka_api_key.text.return_value = ''
        ui._get_ffplay.return_value = 'ffplay.exe'
        ui._subprocess_startupinfo.return_value = None
        ui.after.side_effect = lambda delay, callback: callback()
        with patch('threading.Thread', side_effect=lambda target, daemon: SimpleNamespace(start=target)), \
                patch('subprocess.run') as playback, \
                patch.object(pekka, 'synthesize_pekka') as synthesize, \
                patch.object(pekka.requests, 'post') as request:
            namespace['_preview_voice_sample'](ui)
            playback.assert_called_once()
            self.assertEqual(playback.call_args.args[0][-1], bundled_sample_path(voice))
            synthesize.assert_not_called()
            request.assert_not_called()
            ui._show_error.assert_not_called()

    def test_long_vietnamese_text_keeps_all_words_and_bounds(self):
        text = "Đây là giọng đọc tiếng Việt. " * 200 + "x" * 1500
        parts = list(pekka.split_text(text))
        self.assertTrue(all(0 < len(p) <= 1000 for p in parts))
        self.assertEqual("".join(parts).replace(" ", ""), text.replace(" ", ""))

    def test_voice_language_metadata(self):
        self.assertTrue(pekka.voice_language_matches({"tags": ["🇻🇳 Tiếng Việt"]}, "vi"))
        self.assertTrue(pekka.voice_language_matches({"languageCode": "en-US"}, "en"))
        self.assertFalse(pekka.voice_language_matches({"name": "Tiếng Việt"}, "vi"))

    def test_paginated_voices(self):
        with patch.object(pekka.requests, "get", side_effect=[
                response(payload={"items": [{"id": "a"}], "hasNext": True}),
                response(payload={"items": [{"id": "b"}], "hasNext": False})]) as get:
            self.assertEqual([v["id"] for v in pekka.fetch_voices("test")], ["a", "b"])
            self.assertEqual(get.call_args.kwargs["params"]["page"], 2)

    def test_invalid_key_stops_without_downloading(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(pekka.requests, "post", return_value=response(401)), \
                patch.object(pekka.requests, "get") as get:
            target = Path(folder) / "test.mp3"
            target.write_bytes(b"previous audio")
            with self.assertRaisesRegex(RuntimeError, "key Pekka"):
                pekka.synthesize_pekka("Xin chào", target, "pekka:test", api_key="invalid")
            get.assert_not_called()
            self.assertEqual(target.read_bytes(), b"previous audio")

    def test_missing_key_before_request(self):
        with patch.object(pekka.requests, "post") as post:
            with self.assertRaises(ValueError):
                pekka.synthesize_pekka("Xin chào", "test.mp3", "pekka:test", api_key="")
            post.assert_not_called()

    def test_missing_audio_url_and_timeout_do_not_retry_paid_request(self):
        for reply in (response(payload={}), pekka.requests.Timeout()):
            with self.subTest(reply=type(reply).__name__), tempfile.TemporaryDirectory() as folder, \
                    patch.object(pekka.requests, "post", side_effect=[reply]) as post:
                with self.assertRaises(RuntimeError):
                    pekka.synthesize_pekka("Xin chào", Path(folder) / "voice.mp3", "pekka:test", api_key="test")
                self.assertEqual(post.call_count, 1)

    def test_refresh_keeps_selected_pekka_voice(self):
        from PySide6.QtWidgets import QApplication, QComboBox
        qt = QApplication.instance() or QApplication([])
        class Combo(QComboBox):
            def get(self): return self.currentText()
            def set(self, value): self.setCurrentText(value)
            def configure(self, values):
                self.clear()
                self.addItems(values)
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8-sig"))
        method = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "on_tts_language_change")
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "main.py", "exec"), namespace)
        from types import SimpleNamespace
        voices = ["vi-VN-HoaiMyNeural"] + pekka.voice_options("vi")
        combo = Combo()
        combo.addItems(voices)
        combo.set(voices[2])
        saved = []
        combo.currentTextChanged.connect(saved.append)
        ui = SimpleNamespace(voice_choice=combo, get_voice_options=lambda lang: voices)
        namespace["on_tts_language_change"](ui, "Tiếng Việt")
        self.assertEqual(combo.get(), voices[2])
        self.assertEqual(saved, [voices[2]])
        combo.deleteLater()

    def test_real_ffmpeg_outputs_and_download_auth_separation(self):
        from core.voice_segments import _probe_audio_duration
        for suffix in (".mp3", ".wav"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder, \
                    patch.object(pekka.requests, "post", return_value=response(payload={"url": "/audio/test"})) as post, \
                    patch.object(pekka.requests, "get", return_value=response(content=audio_fixture())) as get:
                target = Path(folder) / ("voice" + suffix)
                pekka.synthesize_pekka("Xin chào. " * 150, target, "pekka:test", "+15%", "secret")
                self.assertGreater(_probe_audio_duration(str(target)), 1.8)
                self.assertEqual(post.call_count, 2)
                self.assertEqual(post.call_args.kwargs["json"]["speed"], 1.15)
                self.assertEqual(post.call_args.kwargs["json"]["voiceId"], "test")
                self.assertNotIn("headers", get.call_args.kwargs)
                self.assertEqual(get.call_args.args[0], pekka.BASE_URL + "/audio/test")
                self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_invalid_audio_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(pekka.requests, "post", return_value=response(payload={"url": "/audio/test"})), \
                patch.object(pekka.requests, "get", return_value=response(content=b"not audio")):
            target = Path(folder) / "voice.mp3"
            target.write_bytes(b"previous audio")
            with self.assertRaisesRegex(RuntimeError, "âm thanh"):
                pekka.synthesize_pekka("Xin chào", target, "pekka:test", api_key="test")
            self.assertEqual(target.read_bytes(), b"previous audio")

    def test_main_voice_selection(self):
        # Load actual UI helper methods without starting licensing/background UI work.
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "main.py").read_text(encoding="utf-8-sig"))
        names = {"_resolve_voice_id", "_is_voice_id", "_is_vietnamese_language", "get_voice_options"}
        methods = [n for cls in tree.body if isinstance(cls, ast.ClassDef)
                   for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        cls = ast.ClassDef(name="VoiceUI", bases=[], keywords=[], body=methods, decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
        namespace = {"re": re}
        exec(compile(module, "main.py", "exec"), namespace)
        ui = namespace["VoiceUI"]()
        for language in ("Tiếng Việt", "English"):
            choices = ui.get_voice_options(language)
            label = next(v for v in choices if "pekka:" in v)
            self.assertEqual(ui._resolve_voice_id(label, language), label.split(" - ")[-1])
        self.assertEqual(ui._resolve_voice_id("pekka:custom-ID_1"), "pekka:custom-ID_1")
        self.assertEqual(ui._resolve_voice_id("Review nữ - vi-VN-HoaiMyNeural"), "vi-VN-HoaiMyNeural")

    def test_ai_engine_and_segment_dispatch_do_not_call_edge(self):
        from engine.ai_engine import AIEngine
        from core.voice_segments import VoiceSegmentsGenerator
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(pekka, "get_api_key", return_value="test"), \
                patch.object(pekka.requests, "post", return_value=response(payload={"url": "/audio/test"})), \
                patch.object(pekka.requests, "get", return_value=response(content=audio_fixture())), \
                patch("edge_tts.Communicate", side_effect=AssertionError("Must not use Edge")):
            target = str(Path(folder) / "engine.mp3")
            Path(target + ".timing.json").write_text("[]")
            ai = AIEngine.__new__(AIEngine)
            self.assertEqual(asyncio.run(ai.text_to_speech("Xin chào bạn.", target, "pekka:test")), "pekka:test")
            self.assertFalse(Path(target + ".timing.json").exists())
            generator = VoiceSegmentsGenerator.__new__(VoiceSegmentsGenerator)
            duration = asyncio.run(generator.generate_segment_audio("Xin chào bạn.", str(Path(folder) / "segment.wav"), "pekka:test"))
            self.assertGreater(duration, 0.9)


if __name__ == "__main__":
    unittest.main()
