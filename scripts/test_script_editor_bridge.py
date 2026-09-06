"""Exercise the real editor with a hidden Qt parent and temporary script data."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from ui.script_editor import ScriptEditorWindow


class EditorBridgeTest(unittest.TestCase):
    def test_open_edit_worker_callback_confirm_and_cancel(self):
        qt = QApplication.instance() or QApplication([])
        parent = QWidget()
        with tempfile.TemporaryDirectory() as folder:
            pipeline = SimpleNamespace(output_dir=folder,render_blocks=[],
                ai_package={'script_blocks':[{'block_id':1,'text':'Nội dung ban đầu.',
                                             'duration_hint_seconds':10}]})
            confirmed,cancelled = [],[]
            with patch.object(ScriptEditorWindow,'_set_initial_window_size',lambda s:s.withdraw()), \
                 patch.object(ScriptEditorWindow,'focus_force'), \
                 patch.object(ScriptEditorWindow,'deiconify'):
                editor = ScriptEditorWindow(parent,pipeline,
                    lambda:confirmed.append(True),lambda:cancelled.append(True))
                try:
                    self.assertEqual(len(editor._block_entries),1)
                    self.assertEqual(confirmed,[])
                    worker = threading.Thread(target=lambda:editor._safe_status('Worker đã cập nhật'))
                    worker.start(); worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
                    QTest.qWait(100)
                    self.assertEqual(editor._status_lbl.cget('text'),'Worker đã cập nhật')
                    box = editor._block_entries[0][0]
                    box.delete('1.0','end')
                    box.insert('1.0','Nội dung đã sửa để tạo voice.')
                    editor._on_confirm()
                    self.assertEqual(confirmed,[True])
                    self.assertEqual(cancelled,[])
                    self.assertEqual(pipeline.ai_package['script_blocks'][0]['voice_text'],
                                     'Nội dung đã sửa để tạo voice')
                    self.assertTrue((Path(folder)/'ai_package.json').exists())
                    editor = ScriptEditorWindow(parent,pipeline,
                        lambda:confirmed.append(True),lambda:cancelled.append(True))
                    editor._on_cancel()
                    self.assertEqual(confirmed,[True])
                    self.assertEqual(cancelled,[True])
                finally:
                    parent._tk_editor_bridge.close()
                    parent.close()


if __name__=='__main__': unittest.main()
