"""
CapCut Integration Module
- Launches CapCut to generate SRT from audio
- Monitors CapCut project folders for generated SRT
- Extracts SRT from CapCut project when app exits
"""

import os
import json
import subprocess
import threading
import time
import shutil
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Iterable


class CapCutIntegration:
    """Handle CapCut project management and SRT extraction"""
    
    # CapCut project directory paths on Windows
    CAPCUT_PROJECT_DIRS = [
        os.path.expanduser(r"~\Documents\CapCut"),
        os.path.expanduser(r"~\AppData\Local\ByteDance\CapCut"),
        os.path.expanduser(r"~\AppData\Roaming\CapCut"),
    ]
    
    # CapCut executable paths
    CAPCUT_EXECUTABLES = [
        r"C:\Program Files\CapCut\CapCut.exe",
        r"C:\Program Files (x86)\CapCut\CapCut.exe",
        os.path.expanduser(r"~\AppData\Local\ByteDance\CapCut\CapCut.exe"),
    ]
    
    def __init__(self, progress_callback: Optional[Callable] = None):
        """
        Initialize CapCut integration
        
        Args:
            progress_callback: Function(message) to report progress
        """
        self.progress_callback = progress_callback
        self.capcut_process = None
        self.last_project_path = None
        self._capcut_pids_before = set()
    
    def log(self, message: str):
        """Log message with callback"""
        try:
            print(f"[CapCut] {message}")
        except UnicodeEncodeError:
            safe_message = message.encode("ascii", errors="replace").decode("ascii")
            print(f"[CapCut] {safe_message}")
        if self.progress_callback:
            try:
                self.progress_callback(message)
            except:
                pass
    
    @staticmethod
    def find_capcut_executable() -> Optional[str]:
        """Find CapCut on a clean end-user Windows installation."""
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        apps_dir = os.path.join(local_app_data, "CapCut", "Apps")
        candidates = [
            os.path.join(apps_dir, "CapCut.exe"),
            os.path.join(local_app_data, "CapCut", "CapCut.exe"),
            os.path.join(local_app_data, "Programs", "CapCut", "CapCut.exe"),
            os.path.join(local_app_data, "Microsoft", "WindowsApps", "CapCut.exe"),
        ]
        try:
            app_items = os.listdir(apps_dir) if os.path.isdir(apps_dir) else []
        except (OSError, PermissionError):
            app_items = []
        if app_items:
            versioned_launchers = []
            for item in app_items:
                app_path = os.path.join(apps_dir, item)
                try:
                    if os.path.isdir(app_path):
                        versioned_launchers.append(os.path.join(app_path, "CapCut.exe"))
                except (OSError, PermissionError):
                    continue
            def _safe_mtime(path: str) -> float:
                try:
                    return os.path.getmtime(path) if os.path.exists(path) else 0
                except (OSError, PermissionError):
                    return 0

            versioned_launchers.sort(key=_safe_mtime, reverse=True)
            candidates.extend(versioned_launchers)
        candidates.extend(CapCutIntegration.CAPCUT_EXECUTABLES)

        # Installer locations differ between CapCut releases. Discover them
        # from Windows uninstall registry entries instead of hard-coding the
        # developer machine's version folder.
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for registry_path in (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                ):
                    try:
                        with winreg.OpenKey(hive, registry_path) as uninstall:
                            for index in range(winreg.QueryInfoKey(uninstall)[0]):
                                try:
                                    name = winreg.EnumKey(uninstall, index)
                                    with winreg.OpenKey(uninstall, name) as child:
                                        display_name = str(winreg.QueryValueEx(child, "DisplayName")[0] or "")
                                        if "capcut" not in display_name.lower():
                                            continue
                                        try:
                                            icon = str(winreg.QueryValueEx(child, "DisplayIcon")[0] or "")
                                            icon = icon.strip().strip('"').split('",')[0]
                                            if icon:
                                                candidates.append(icon)
                                        except OSError:
                                            pass
                                        try:
                                            install_dir = str(winreg.QueryValueEx(child, "InstallLocation")[0] or "")
                                            if install_dir:
                                                candidates.extend([
                                                    os.path.join(install_dir, "CapCut.exe"),
                                                    os.path.join(install_dir, "Apps", "CapCut.exe"),
                                                ])
                                        except OSError:
                                            pass
                                except (OSError, PermissionError):
                                    continue
                    except (OSError, PermissionError):
                        continue
        except (ImportError, OSError):
            pass

        seen = set()
        for exe_path in candidates:
            exe_path = os.path.expandvars(str(exe_path or "").strip().strip('"'))
            normalized = os.path.normcase(os.path.abspath(exe_path)) if exe_path else ""
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                if os.path.isfile(exe_path):
                    return exe_path
            except (OSError, PermissionError):
                continue
        
        # Try to find in PATH
        capcut = shutil.which("CapCut")
        if capcut:
            return capcut
        
        return None
    
    @staticmethod
    def get_capcut_project_roots() -> list:
        """Return existing CapCut project roots used by recent desktop builds."""
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        roaming_app_data = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
        current_drafts = os.path.join(
            local_app_data,
            "CapCut",
            "User Data",
            "Projects",
            "com.lveditor.draft",
        )
        candidates = [
            current_drafts,
            os.path.join(local_app_data, "CapCut", "User Data", "Projects"),
            os.path.join(local_app_data, "CapCut", "User Data"),
            os.path.join(local_app_data, "CapCut"),
            os.path.join(local_app_data, "ByteDance", "CapCut", "User Data", "Projects", "com.lveditor.draft"),
            os.path.join(local_app_data, "ByteDance", "CapCut", "User Data", "Projects"),
            os.path.join(roaming_app_data, "CapCut", "User Data", "Projects", "com.lveditor.draft"),
            os.path.join(roaming_app_data, "CapCut", "User Data", "Projects"),
            os.path.join(os.path.expanduser("~"), "Documents", "CapCut", "User Data", "Projects", "com.lveditor.draft"),
            os.path.join(os.path.expanduser("~"), "Documents", "CapCut", "User Data", "Projects"),
            *CapCutIntegration.CAPCUT_PROJECT_DIRS,
        ]

        roots = []
        seen = set()
        for project_dir in candidates:
            try:
                normalized = os.path.normcase(os.path.abspath(project_dir))
            except Exception:
                normalized = project_dir
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                if os.path.exists(project_dir):
                    roots.append(project_dir)
            except (OSError, PermissionError):
                continue
        return roots

    @staticmethod
    def find_capcut_projects_dir() -> Optional[str]:
        """Find the first CapCut projects directory."""
        roots = CapCutIntegration.get_capcut_project_roots()
        if roots:
            return roots[0]
        return None
    
    def open_capcut_for_srt(self, video_path: str) -> bool:
        """
        Open CapCut with video file for SRT generation
        
        Args:
            video_path: Path to video file
        
        Returns:
            True if CapCut started successfully
        """
        if not os.path.exists(video_path):
            self.log(f"❌ Video file not found: {video_path}")
            return False
        
        capcut_exe = self.find_capcut_executable()
        if not capcut_exe:
            self.log(
                "❌ Không tìm thấy CapCut trên máy này. Hãy cài CapCut Desktop "
                "rồi mở lại app."
            )
            return False
        
        try:
            project_roots = self.get_capcut_project_roots()
            self.log(f"🎬 CapCut: {capcut_exe}")
            if project_roots:
                self.log(f"📁 Theo dõi project: {project_roots[0]}")
            else:
                self.log(
                    "ℹ️ Chưa có thư mục project CapCut; app sẽ tự nhận sau khi "
                    "CapCut tạo project."
                )
            self.log(f"🎬 Mở CapCut với video gốc: {video_path}")
            self._capcut_pids_before = self._running_capcut_pids()
            # Launch CapCut with video file
            self.capcut_process = subprocess.Popen(
                [capcut_exe, video_path],
                cwd=os.path.dirname(capcut_exe) or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log(f"✅ CapCut started (PID: {self.capcut_process.pid})")
            self.log("📝 Please use CapCut's auto caption feature and export when done")
            return True
        except Exception as e:
            self.log(f"❌ Failed to launch CapCut: {str(e)}")
            return False
    
    def wait_for_capcut_exit(self, timeout: int = 3600) -> bool:
        """
        Wait for CapCut process to exit
        
        Args:
            timeout: Maximum wait time in seconds (default 1 hour)
        
        Returns:
            True if CapCut exited, False if timeout
        """
        if not self.capcut_process:
            return False
        
        try:
            self.capcut_process.wait(timeout=timeout)
            self.log("✅ CapCut closed")
            return True
        except subprocess.TimeoutExpired:
            self.log("⏱️ Timeout waiting for CapCut")
            return False
    
    @staticmethod
    def _running_capcut_pids() -> set:
        """Return CapCut.exe PIDs without requiring psutil."""
        if os.name != "nt":
            return set()
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            pids = set()
            for line in (result.stdout or "").splitlines():
                match = re.match(r'^"CapCut\.exe","(\d+)"', line.strip(), flags=re.I)
                if match:
                    pids.add(int(match.group(1)))
            return pids
        except Exception:
            return set()

    def wait_for_capcut_session(
        self,
        timeout: int = 0,
        video_path: str = "",
        since_time: float = 0.0,
        project_snapshot: Optional[Dict[str, float]] = None,
    ) -> bool:
        """Wait until the user closes CapCut, remembering the caption draft."""
        if not self.capcut_process:
            return False
        wait_started = time.time()
        deadline = (time.time() + max(30, timeout)) if timeout and timeout > 0 else None
        # CapCut may already be open. In that case the command-line launcher
        # delegates to the existing GUI process and exits almost immediately.
        observed_pids = set(self._capcut_pids_before or set())
        last_project = ""
        stable_since = 0.0
        last_project_check = 0.0
        caption_ready_logged = False
        while deadline is None or time.time() < deadline:
            current_pids = self._running_capcut_pids()
            observed_pids.update(current_pids - set(self._capcut_pids_before or set()))
            if self.capcut_process.poll() is None:
                observed_pids.add(int(self.capcut_process.pid))

            now = time.time()
            if video_path and now - last_project_check >= 3.0:
                last_project_check = now
                project = self.find_recent_project_for_video(
                    video_path,
                    since_time=since_time,
                    project_snapshot=project_snapshot,
                    quiet=True,
                )
                if project and project != self.last_project_path and self._project_has_subtitle_data(project):
                    if project == last_project:
                        if stable_since and now - stable_since >= 3.0:
                            self.last_project_path = project
                            if not caption_ready_logged:
                                self.log("CapCut đã lưu Auto Caption; đang chờ bạn đóng CapCut để lấy SRT")
                                caption_ready_logged = True
                            continue
                            self.log("✅ CapCut đã lưu Auto Caption; bắt đầu lấy SRT")
                            return True
                    else:
                        last_project = project
                        stable_since = now

            session_pids = observed_pids & current_pids
            if (
                observed_pids
                and self.capcut_process.poll() is not None
                and not session_pids
                and now - wait_started >= 8.0
            ):
                self.log("✅ CapCut đã đóng")
                return True
            time.sleep(1)

        self.log("⏱️ Hết thời gian chờ CapCut/Auto Caption")
        return False

    @staticmethod
    def _is_capcut_project_folder(project_path: str) -> bool:
        """Return True if the directory contains CapCut subtitle/project metadata."""
        if not os.path.isdir(project_path):
            return False
        try:
            for entry in os.listdir(project_path):
                if entry.lower() in {"project.json", "draft_content.json"} or entry.lower().endswith(".srt"):
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _project_latest_mtime(project_path: str) -> float:
        latest = os.path.getmtime(project_path)
        try:
            for root, _dirs, files in os.walk(project_path):
                for file in files:
                    lower_file = file.lower()
                    if lower_file in {"project.json", "draft_content.json"} or lower_file.endswith(".srt"):
                        file_path = os.path.join(root, file)
                        latest = max(latest, os.path.getmtime(file_path))
        except Exception:
            pass
        return latest

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        text = str(value or "").replace("\\", "/").lower()
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _iter_json_strings(value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from CapCutIntegration._iter_json_strings(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from CapCutIntegration._iter_json_strings(child)

    @classmethod
    def _project_mentions_video(cls, project_path: str, video_path: str) -> bool:
        """Return True when CapCut project metadata references the target video.

        CapCut Desktop mới lưu đường dẫn video dưới dạng Unicode-escaped JSON
        (VD: \\u1eec\\u0021...) nên cần decode trước khi so sánh.
        """
        if not video_path or not os.path.isdir(project_path):
            return False
        abs_video  = cls._normalize_match_text(os.path.abspath(video_path))
        base_video = cls._normalize_match_text(os.path.basename(video_path))
        stem_video = cls._normalize_match_text(
            os.path.splitext(os.path.basename(video_path))[0]
        )
        if not base_video and not stem_video:
            return False

        def _check_content(raw: str) -> bool:
            # So sánh trên raw text (có thể chứa unicode-escape)
            content_raw = cls._normalize_match_text(raw)
            if abs_video  and abs_video  in content_raw: return True
            if base_video and base_video in content_raw: return True
            if stem_video and len(stem_video) >= 6 and stem_video in content_raw: return True
            # So sánh sau khi decode unicode-escape (VD: \u0041 -> A)
            try:
                content_decoded = cls._normalize_match_text(
                    raw.encode("utf-8").decode("unicode_escape", errors="ignore")
                )
                if abs_video  and abs_video  in content_decoded: return True
                if base_video and base_video in content_decoded: return True
                if stem_video and len(stem_video) >= 6 and stem_video in content_decoded: return True
            except Exception:
                pass
            return False

        metadata_names = {
            "draft_content.json", "project.json", "draft_meta_info.json",
            "draft_info.json", "draft_settings.json", "meta.json",
        }
        try:
            for root, _dirs, files in os.walk(project_path):
                for file in files:
                    lower_file = file.lower()
                    if lower_file not in metadata_names and not lower_file.endswith(".json"):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        if os.path.getsize(file_path) > 20 * 1024 * 1024:
                            continue
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                            raw = handle.read()
                    except Exception:
                        continue
                    try:
                        parsed = json.loads(raw)
                        if any(_check_content(value) for value in cls._iter_json_strings(parsed)):
                            return True
                    except Exception:
                        pass
                    if _check_content(raw):
                        return True
        except Exception:
            return False
        return False

    @staticmethod
    def _project_has_subtitle_data(project_path: str) -> bool:
        """Return True when a changed CapCut project contains caption material."""
        if not project_path or not os.path.isdir(project_path):
            return False
        try:
            for root, _dirs, files in os.walk(project_path):
                for file in files:
                    lower_file = file.lower()
                    file_path = os.path.join(root, file)
                    if lower_file.endswith(".srt"):
                        try:
                            return os.path.getsize(file_path) > 20
                        except OSError:
                            continue
                    if lower_file not in {"draft_content.json", "project.json"}:
                        continue
                    try:
                        if os.path.getsize(file_path) > 50 * 1024 * 1024:
                            continue
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                            raw_original = handle.read()
                            raw = raw_original.lower()
                    except (OSError, PermissionError):
                        continue
                    if lower_file == "draft_content.json":
                        try:
                            if CapCutIntegration._parse_capcut_draft_content(json.loads(raw_original)):
                                return True
                        except Exception:
                            pass
                    markers = (
                        '\"type\":\"subtitle\"', '\"type\": \"subtitle\"',
                        '\"subtitles\"', '\"caption\"', '\"text_materials\"',
                    )
                    if any(marker in raw for marker in markers):
                        return True
        except (OSError, PermissionError):
            return False
        return False

    @classmethod
    def snapshot_projects(cls) -> Dict[str, float]:
        """Capture CapCut project mtimes before launching CapCut."""
        snapshot: Dict[str, float] = {}
        ignore_names = {".recycle_bin", "recycle_bin", ".trash", "trash"}
        for root_dir in cls.get_capcut_project_roots():
            if not root_dir or not os.path.exists(root_dir):
                continue
            try:
                for current_root, dirs, _files in os.walk(root_dir):
                    try:
                        relative_parts = Path(current_root).relative_to(root_dir).parts
                    except Exception:
                        relative_parts = ()
                    dirs[:] = [
                        dirname for dirname in dirs
                        if dirname.lower() not in ignore_names and not dirname.startswith(".")
                    ]
                    if len(relative_parts) >= 4:
                        dirs[:] = []
                    if not cls._is_capcut_project_folder(current_root):
                        continue
                    snapshot[os.path.normcase(os.path.abspath(current_root))] = cls._project_latest_mtime(current_root)
                    dirs[:] = []
            except Exception:
                continue
        return snapshot

    def find_recent_project_for_video(
        self,
        video_path: str,
        since_time: float = 0.0,
        project_snapshot: Optional[Dict[str, float]] = None,
        quiet: bool = False,
    ) -> Optional[str]:
        """Find a project modified by this CapCut session, preferring video matches."""
        roots = self.get_capcut_project_roots()
        if not roots:
            return None

        project_snapshot = project_snapshot or {}
        candidates = []
        ignore_names = {".recycle_bin", "recycle_bin", ".trash", "trash"}
        try:
            for root_dir in roots:
                if not root_dir or not os.path.exists(root_dir):
                    continue
                for current_root, dirs, _files in os.walk(root_dir):
                    try:
                        relative_parts = Path(current_root).relative_to(root_dir).parts
                    except Exception:
                        relative_parts = ()

                    dirs[:] = [
                        dirname for dirname in dirs
                        if dirname.lower() not in ignore_names and not dirname.startswith(".")
                    ]
                    if len(relative_parts) >= 4:
                        dirs[:] = []

                    if not CapCutIntegration._is_capcut_project_folder(current_root):
                        continue

                    normalized = os.path.normcase(os.path.abspath(current_root))
                    mod_time = CapCutIntegration._project_latest_mtime(current_root)
                    previous_time = float(project_snapshot.get(normalized, 0.0) or 0.0)

                    # Điều kiện "thay đổi trong phiên này":
                    # 1. Nếu project không có trong snapshot (project MỚI tạo sau khi launch) → luôn OK
                    # 2. Nếu có trong snapshot → mtime phải lớn hơn mtime snapshot (đã được sửa)
                    # 3. Fallback: mtime >= threshold (since_time - 5s) để bắt project sửa sát trước launch
                    if previous_time == 0.0:
                        # Project mới, không có trong snapshot
                        changed_this_run = True
                    elif mod_time > previous_time + 0.5:
                        # Project đã thay đổi sau khi snapshot
                        changed_this_run = True
                    else:
                        # Không thay đổi trong phiên
                        changed_this_run = False

                    if not changed_this_run:
                        dirs[:] = []
                        continue

                    video_match = CapCutIntegration._project_mentions_video(current_root, video_path)
                    has_subtitles = CapCutIntegration._project_has_subtitle_data(current_root)
                    is_new = previous_time == 0.0
                    score = (
                        100 if video_match else 0,
                        30 if has_subtitles else 0,
                        20 if is_new else 0,
                        mod_time,
                    )
                    candidates.append(
                        (score, current_root, video_match, has_subtitles, mod_time)
                    )
                    dirs[:] = []

            if not candidates:
                return None

            matches = [item for item in candidates if item[2]]
            if matches:
                matches.sort(key=lambda item: item[0], reverse=True)
                selected = matches[0][1]
                reason = "metadata khớp video gốc"
            else:
                # CapCut mới có thể lưu đường dẫn media trong database/binary.
                # Khi đó chỉ nhận project vừa thay đổi trong chính phiên này và
                # đã có caption, không lấy project cũ mới nhất một cách mù quáng.
                caption_projects = [item for item in candidates if item[3]]
                caption_projects.sort(key=lambda item: item[0], reverse=True)
                selected = caption_projects[0][1] if caption_projects else None
                reason = "project vừa thay đổi và có Auto Caption"
            if not selected:
                self.log(
                    "❌ Project CapCut vừa sửa chưa có Auto Caption và cũng không "
                    "tham chiếu video hiện tại; dừng để tránh lấy nhầm SRT."
                )
                return None
            self.last_project_path = selected
            if quiet:
                return selected
            self.log(
                f"✅ Đã nhận project CapCut: {os.path.basename(selected)} ({reason})"
            )
            return selected
        except Exception as e:
            self.log(f"❌ Error finding recent project: {str(e)}")
            return None

    def find_latest_project(self, project_dir: Optional[str] = None) -> Optional[str]:
        """
        Find the most recently modified CapCut project
        
        Args:
            project_dir: CapCut projects directory (auto-detect if None)
        
        Returns:
            Path to project directory or None
        """
        project_dirs = [project_dir] if project_dir else self.get_capcut_project_roots()

        if not project_dirs:
            return None
        
        ignore_names = {".recycle_bin", "recycle_bin", ".trash", "trash"}
        try:
            projects = []
            for root_dir in project_dirs:
                if not root_dir or not os.path.exists(root_dir):
                    continue
                for current_root, dirs, _files in os.walk(root_dir):
                    try:
                        relative_parts = Path(current_root).relative_to(root_dir).parts
                    except Exception:
                        relative_parts = ()

                    dirs[:] = [
                        dirname for dirname in dirs
                        if dirname.lower() not in ignore_names and not dirname.startswith(".")
                    ]
                    if len(relative_parts) >= 4:
                        dirs[:] = []

                    if not CapCutIntegration._is_capcut_project_folder(current_root):
                        continue
                    mod_time = CapCutIntegration._project_latest_mtime(current_root)
                    projects.append((mod_time, current_root))
                    dirs[:] = []
            
            if projects:
                projects.sort(reverse=True)
                self.last_project_path = projects[0][1]
                self.log(f"📂 Found latest project: {os.path.basename(projects[0][1])}")
                return projects[0][1]
        except Exception as e:
            self.log(f"❌ Error finding projects: {str(e)}")
        
        return None
    
    def extract_srt_from_project(self, project_path: str, output_srt: str) -> bool:
        """
        Extract SRT from CapCut project
        
        Args:
            project_path: CapCut project directory
            output_srt: Path to save extracted SRT
        
        Returns:
            True if extraction successful
        """
        if not os.path.exists(project_path):
            self.log(f"❌ Project path not found: {project_path}")
            return False
        
        try:
            # CapCut stores subtitles in project.json or subtitle files
            srt_found = False
            srt_content = ""
            
            # Look for subtitle files
            for root, dirs, files in os.walk(project_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Check for .srt files
                    if file.endswith('.srt'):
                        self.log(f"📄 Found SRT file: {file}")
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            srt_content = f.read()
                            srt_found = True
                            break
                    
                    # Check for project.json with subtitle data
                    elif file == 'project.json':
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                project_data = json.load(f)
                                # Extract subtitles if available
                                if 'subtitles' in project_data:
                                    srt_content = self._parse_capcut_subtitles(project_data['subtitles'])
                                    if srt_content:
                                        srt_found = True
                                        self.log("📋 Extracted subtitles from project.json")
                                        break
                        except:
                            pass

                    # Current desktop CapCut drafts store auto captions here.
                    elif file == 'draft_content.json':
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                draft_data = json.load(f)
                            srt_content = self._parse_capcut_draft_content(draft_data)
                            if srt_content:
                                srt_found = True
                                self.log("Extracted subtitles from draft_content.json")
                                break
                        except Exception:
                            pass
                
                if srt_found:
                    break
            
            if srt_found and srt_content:
                # Save extracted SRT
                os.makedirs(os.path.dirname(output_srt) or ".", exist_ok=True)
                with open(output_srt, 'w', encoding='utf-8') as f:
                    f.write(srt_content)
                self.log(f"✅ SRT extracted and saved: {output_srt}")
                return True
            else:
                self.log("⚠️ No subtitle data found in CapCut project")
                return False
        
        except Exception as e:
            self.log(f"❌ Error extracting SRT: {str(e)}")
            return False
    
    def extract_srt_from_latest_project(self, output_srt: str) -> bool:
        """
        Extract SRT from the latest CapCut project
        
        Args:
            output_srt: Path to save extracted SRT
        
        Returns:
            True if extraction successful
        """
        project_path = self.find_latest_project()
        if not project_path:
            self.log("❌ No CapCut projects found")
            return False
        
        return self.extract_srt_from_project(project_path, output_srt)
    
    def extract_srt_from_recent_project(
        self,
        video_path: str,
        output_srt: str,
        since_time: float = 0.0,
        project_snapshot: Optional[Dict[str, float]] = None,
    ) -> bool:
        project_path = self.find_recent_project_for_video(
            video_path,
            since_time=since_time,
            project_snapshot=project_snapshot,
        )
        if not project_path:
            self.log(
                "❌ Không tìm thấy project CapCut khớp video hiện tại. "
                "Hãy nhập đúng video vào project và chạy Auto Caption rồi đóng CapCut."
            )
            return False
        return self.extract_srt_from_project(project_path, output_srt)

    @staticmethod
    def _parse_capcut_subtitles(subtitles_data) -> str:
        """
        Convert CapCut subtitle data to SRT format
        
        Args:
            subtitles_data: Subtitle data from project.json
        
        Returns:
            SRT content as string
        """
        srt_lines = []
        
        if isinstance(subtitles_data, list):
            for idx, subtitle in enumerate(subtitles_data, 1):
                if isinstance(subtitle, dict):
                    start_time = subtitle.get('startTime', 0) / 1000  # Convert from ms to s
                    end_time = subtitle.get('endTime', 0) / 1000
                    text = subtitle.get('text', '')
                    
                    if text:
                        start_str = CapCutIntegration._seconds_to_srt_time(start_time)
                        end_str = CapCutIntegration._seconds_to_srt_time(end_time)
                        srt_lines.append(f"{idx}")
                        srt_lines.append(f"{start_str} --> {end_str}")
                        srt_lines.append(text)
                        srt_lines.append("")
        
        return "\n".join(srt_lines)

    @staticmethod
    def _parse_capcut_draft_content(draft_data) -> str:
        """Convert current CapCut desktop subtitle tracks to SRT."""
        texts = {}
        for material in draft_data.get("materials", {}).get("texts", []) or []:
            material_type = str(material.get("type") or "").lower()
            try:
                text = json.loads(material.get("content", "{}")).get("text", "")
            except Exception:
                text = material.get("recognize_text", "")
            text = text or material.get("recognize_text", "") or material.get("text", "")
            if text:
                texts[material.get("id")] = {
                    "text": str(text).strip(),
                    "type": material_type,
                }

        subtitles = []
        for track in draft_data.get("tracks", []) or []:
            if str(track.get("type") or "").lower() not in {"text", "subtitle"}:
                continue
            for segment in track.get("segments", []) or []:
                material = texts.get(segment.get("material_id")) or {}
                text = material.get("text", "")
                timerange = segment.get("target_timerange") or segment.get("source_timerange") or {}
                duration = float(timerange.get("duration", 0) or 0) / 1000000
                if not text or duration <= 0:
                    continue
                start = float(timerange.get("start", 0) or 0) / 1000000
                subtitles.append((start, start + duration, text))

        subtitles.sort(key=lambda item: item[0])
        srt_lines = []
        for index, (start, end, text) in enumerate(subtitles, 1):
            srt_lines.extend([
                str(index),
                f"{CapCutIntegration._seconds_to_srt_time(start)} --> "
                f"{CapCutIntegration._seconds_to_srt_time(end)}",
                text,
                "",
            ])
        return "\n".join(srt_lines)
    
    @staticmethod
    def _seconds_to_srt_time(seconds: float) -> str:
        """Convert seconds to SRT time format HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def monitor_and_extract_srt(
        self,
        video_path: str,
        output_srt: str,
        on_complete: Optional[Callable[[bool], None]] = None
    ) -> bool:
        """
        Complete workflow: Open CapCut → Wait for exit → Extract SRT
        
        Args:
            video_path: Path to video file
            output_srt: Path to save extracted SRT
            on_complete: Callback when done (success: bool)
        
        Returns:
            True if workflow completed successfully
        """
        project_snapshot = self.snapshot_projects()
        launch_time = time.time()

        # Open CapCut
        if not self.open_capcut_for_srt(video_path):
            if on_complete:
                on_complete(False)
            return False
        
        # Wait for CapCut to close
        if not self.wait_for_capcut_session(
            video_path=video_path,
            since_time=launch_time,
            project_snapshot=project_snapshot,
        ):
            if on_complete:
                on_complete(False)
            return False
        
        # CapCut có thể ghi draft_content.json trễ vài giây sau khi cửa sổ
        # đóng. Đợi đúng project của phiên này thay vì rơi ngay sang Whisper.
        project_path = self.last_project_path
        for attempt in range(5):
            if project_path:
                break
            time.sleep(2)
            project_path = self.find_recent_project_for_video(
                video_path,
                since_time=launch_time,
                project_snapshot=project_snapshot,
            )
            if project_path:
                break
            if attempt < 4:
                self.log(f"⏳ Đợi CapCut lưu Auto Caption ({attempt + 2}/5)...")

        success = False
        if project_path:
            # Đọc caption trực tiếp từ project và tạo SRT cho pipeline. Bước
            # này không xuất file bằng CapCut và không gọi AI để dịch.
            success = self.extract_srt_from_project(project_path, output_srt)
        else:
            self.log(
                "❌ Không tìm thấy project vừa chỉnh có Auto Caption cho video hiện tại."
            )
        
        if on_complete:
            on_complete(success)
        
        return success
    
    def monitor_and_extract_srt_async(
        self,
        video_path: str,
        output_srt: str,
        on_complete: Optional[Callable[[bool], None]] = None
    ) -> threading.Thread:
        """
        Run monitor_and_extract_srt in background thread
        
        Returns:
            Thread object
        """
        thread = threading.Thread(
            target=self.monitor_and_extract_srt,
            args=(video_path, output_srt, on_complete),
            daemon=True
        )
        thread.start()
        return thread
