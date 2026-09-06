"""Keep the existing Tk editor responsive inside the Qt application's loop."""
import queue
import tkinter as tk
import _tkinter
from PySide6.QtCore import QObject, QTimer


class TkEditorBridge(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.root = tk.Tk()
        self.root.withdraw()
        self.pending = queue.SimpleQueue()
        self.closed = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.pump)
        self.timer.start(10)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(self.close)

    def schedule(self, delay, callback):
        if not self.closed:
            self.pending.put((delay, callback))

    def pump(self):
        if self.closed:
            return
        try:
            for _ in range(100):
                try:
                    delay, callback = self.pending.get_nowait()
                except queue.Empty:
                    break
                self.root.after(delay, callback)
            for _ in range(100):
                if not self.root.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT):
                    break
        except tk.TclError:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def editor_master(parent):
    if hasattr(parent, 'tk'):
        return parent, None
    bridge = getattr(parent, '_tk_editor_bridge', None)
    if bridge is None or bridge.closed:
        bridge = TkEditorBridge(parent)
        parent._tk_editor_bridge = bridge
    return bridge.root, bridge
