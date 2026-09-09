"""Offline regression checks for TTS calls after browser automation."""
import ast
import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.async_bridge import run_async_task


class AsyncBridgeTests(unittest.TestCase):
    def test_sync_caller(self):
        async def task(value, *, suffix):
            await asyncio.sleep(0)
            return value + suffix
        self.assertEqual(run_async_task(task, 'voice', suffix='.mp3'), 'voice.mp3')

    def test_active_loop_uses_worker_and_preserves_loop(self):
        async def caller():
            loop = asyncio.get_running_loop()
            owner = threading.get_ident()
            async def task():
                await asyncio.sleep(0)
                return threading.get_ident()
            for _ in range(3):
                self.assertNotEqual(run_async_task(task), owner)
            self.assertIs(asyncio.get_running_loop(), loop)
            await asyncio.sleep(0)
        asyncio.run(caller())

    def test_original_tts_exception_propagates(self):
        error = OSError('TTS output could not be written')
        async def task():
            raise error
        async def caller():
            with self.assertRaises(OSError) as caught:
                run_async_task(task)
            self.assertIs(caught.exception, error)
        asyncio.run(caller())

    def test_failed_tts_blocks_script_repair_before_imports(self):
        # Execute the real method without loading GUI/model dependencies.
        source = Path(__file__).resolve().parents[1] / 'core' / 'full_pipeline.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == '_repair_recorded_voice_alignment')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        pipeline = SimpleNamespace(
            steps={'VOICE_SEGMENTS': SimpleNamespace(status='failed')},
            _log=Mock(), ai_package={'script': 'Keep approved narration'},
        )
        self.assertFalse(namespace[method.name](pipeline))
        self.assertEqual(pipeline.ai_package['script'], 'Keep approved narration')


if __name__ == '__main__':
    unittest.main()
