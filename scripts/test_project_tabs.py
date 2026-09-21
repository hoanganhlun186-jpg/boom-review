import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from main import App, ProjectTabs
app=QApplication.instance() or QApplication([])
with patch.object(App,'_load_saved_config'), patch.object(App,'_refresh_gemini_web_login_status'), patch.object(App,'save_config'):
    window=ProjectTabs()
    first=window.tabs.widget(0)
    first.video_path.setText('first.mp4')
    window.add_project()
    second=window.tabs.widget(1)
    assert window.tabs.count()==2
    assert second.video_path.text()==''
    assert first.video_path.text()=='first.mp4'
    assert second._shared_only
    assert first.log is not second.log
    window.running=True
    window.add_project()
    assert window.tabs.count()==2
    window.running=False
    window.close()
print('PASS: independent project widgets and queue tab lock')
