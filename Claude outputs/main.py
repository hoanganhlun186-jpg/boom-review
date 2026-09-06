import customtkinter as ctk
from tkinter import filedialog, messagebox, Canvas, colorchooser, BooleanVar
from PIL import Image, ImageDraw, ImageFont, ImageTk
import threading
import asyncio
import os
import json
import uuid
import re
import shutil
import cv2
import numpy as np
import unicodedata
import tempfile
from core.video_engine import VideoEngine
from core.ai_engine import AIEngine
from core.script_processor import ScriptProcessor
from core.video_cutter import VideoCutter
from core.srt_processor import SRTParser
from core.calculator import VideoCalculator
from core.script_generator import ScriptGenerator
from core.workflow import VideoProcessingWorkflow
from core.srt_reviewer import SRTReviewer
from core.capcut_bridge import CapCutIntegration
from core.auto_workflow import AutoWorkflowHandler
from core.premium_pipeline import PremiumReviewPipeline
from core.srt_translator import SRTTranslator
from core.voice_pipeline_enhanced import EnhancedVoiceProcessingPipeline
from core.full_pipeline import FullPipeline
from core.review_styles import normalize_review_style, available_review_styles
from config import ConfigManager
import subprocess
import io
import webbrowser
import time
import sys
import math
try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False
    print("⚠️ Warning: pyperclip not installed. Clipboard features disabled.")

# ── Anti-tamper check (đã tắt) ───────────────────────────────────────────────
# verify_runtime_integrity() bị bỏ qua để app mở thẳng.
    print("   Install with: pip install pyperclip")
from utils.helpers import FFmpegUtils

class App(ctk.CTk):
    CAPCUT_SRT_AUTO = "Tự động (chạy nền)"
    CAPCUT_SRT_MANUAL = "Thủ công (mở CapCut)"

    # ── Device fingerprint helpers ─────────────────────────────────────────────
    _DEVICE_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capcut_device.json")

    def _load_capcut_device(self) -> dict:
        """Đọc device fingerprint từ file; tạo mới nếu chưa có."""
        if os.path.exists(self._DEVICE_CONFIG):
            try:
                with open(self._DEVICE_CONFIG, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("device_id") and data.get("iid"):
                    return data
            except Exception:
                pass
        return self._generate_capcut_device()

    def _generate_capcut_device(self) -> dict:
        """Tạo device fingerprint mới ngẫu nhiên và lưu."""
        did = str(uuid.uuid4().int)[:19]
        iid = str(uuid.uuid4().int)[:19]
        device = {"device_id": did, "iid": iid, "tdid": did, "appvr": "4.1.0", "region": "us", "lan": "en"}
        try:
            with open(self._DEVICE_CONFIG, "w", encoding="utf-8") as f:
                json.dump(device, f, indent=2)
        except Exception as e:
            print(f"[Device] Không lưu được: {e}")
        return device

    def _refresh_device_label(self):
        if hasattr(self, "_lbl_device_info"):
            d = self._load_capcut_device()
            self._lbl_device_info.configure(
                text=f"device_id: {d['device_id'][:12]}...  iid: {d['iid'][:12]}..."
            )

    def _apply_app_icon(self):
        """Apply the branded icon in source and Nuitka ONEDIR builds."""
        bases = [
            os.path.dirname(os.path.abspath(__file__)),
            os.path.dirname(os.path.abspath(sys.executable)),
            os.getcwd(),
        ]
        seen = set()
        for base_dir in bases:
            normalized = os.path.normcase(os.path.abspath(base_dir))
            if normalized in seen:
                continue
            seen.add(normalized)
            ico_path = os.path.join(base_dir, "assets", "app_icon.ico")
            png_path = os.path.join(base_dir, "assets", "app_icon.png")
            try:
                if os.path.isfile(ico_path):
                    self.iconbitmap(ico_path)
                if os.path.isfile(png_path):
                    self._app_icon_photo = ImageTk.PhotoImage(Image.open(png_path))
                    self.iconphoto(True, self._app_icon_photo)
                if os.path.isfile(ico_path) or os.path.isfile(png_path):
                    return
            except Exception:
                continue

    def __init__(self):
        # ── SL6 Theme ─────────────────────────────────────────────────────
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()
        try:
            from engine.updater import get_current_version
            app_version = get_current_version()
        except Exception:
            app_version = "2.0.0"
        self.title(f"Auto Recap Pro V2 v{app_version} - AI Smart Title & Layout")
        self._apply_app_icon()
        self.after(250, self._apply_app_icon)
        self.geometry("1400x950")
        # Override màu nền chính sang SL6 style (xám than đậm)
        self.configure(fg_color="#0a0a14")

        # ── ROOT LAYOUT: sidebar | body ──────────────────────────────────────
        root_frame = ctk.CTkFrame(self, fg_color="#0a0a14", corner_radius=0)
        root_frame.pack(fill="both", expand=True)

        # ── SIDEBAR (trái, icon) ──────────────────────────────────────────────
        sidebar = ctk.CTkFrame(root_frame, width=60, fg_color="#0d0d1f", corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # App logo/avatar
        ctk.CTkLabel(sidebar, text="R", font=("Segoe UI", 18, "bold"),
                     width=44, height=44, fg_color="#6366f1", corner_radius=8,
                     text_color="white").pack(pady=(14, 20))

        def _sb_btn(icon, label, cmd=None):
            f = ctk.CTkFrame(sidebar, fg_color="transparent", cursor="hand2")
            f.pack(fill="x", pady=2, padx=6)
            ctk.CTkLabel(f, text=icon, font=("Segoe UI", 18), width=44, height=38,
                         fg_color="transparent", text_color="#94a3b8").pack()
            ctk.CTkLabel(f, text=label, font=("Segoe UI", 8),
                         text_color="#64748b").pack()
            if cmd:
                f.bind("<Button-1>", lambda e: cmd())
            return f

        self._sb_project  = _sb_btn("📁", "Dự án")
        self._sb_library  = _sb_btn("📚", "Thư viện")
        self._sb_settings = _sb_btn("⚙️", "Cài đặt")

        # Trợ giúp xuống cuối sidebar
        ctk.CTkFrame(sidebar, fg_color="#1e293b", height=1).pack(fill="x", pady=6, padx=6)
        _sb_btn("❓", "Trợ giúp")

        # ── BODY (phải sidebar): header + content + bottom bar ────────────────
        body = ctk.CTkFrame(root_frame, fg_color="#0a0a14", corner_radius=0)
        body.pack(side="left", fill="both", expand=True)

        # ── HEADER BAR (app title + project status) ───────────────────────────
        header_bar = ctk.CTkFrame(body, fg_color="#0d0d1f", height=52, corner_radius=0)
        header_bar.pack(fill="x")
        header_bar.pack_propagate(False)
        ctk.CTkLabel(header_bar, text="Auto Recap Pro V2",
                     font=("Segoe UI", 14, "bold"), text_color="#e2e8f0").pack(side="left", padx=16, pady=6)
        ctk.CTkLabel(header_bar, text="AI Smart Title & Layout",
                     font=("Segoe UI", 10), text_color="#475569").pack(side="left")
        self.license_status_label = ctk.CTkLabel(
            header_bar, text="Dự án chưa lưu",
            font=("Segoe UI", 10), text_color="#64748b", anchor="e")
        self.license_status_label.pack(side="right", padx=16)

        # ── TAB BAR ───────────────────────────────────────────────────────────
        tab_bar = ctk.CTkFrame(body, fg_color="#111827", height=42, corner_radius=0)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        self._tab_frames = {}
        self._tab_btns   = {}
        self._active_tab = None

        def _switch_tab(name):
            self._tab_frames[name].lift()
            for n, b in self._tab_btns.items():
                b.configure(fg_color="#1e293b" if n == name else "transparent",
                            text_color="#a5b4fc" if n == name else "#64748b")
            self._active_tab = name

        tab_defs = [
            ("du_an",    "📂  1. Dự án"),
            ("noi_dung", "✨  2. Nội dung AI"),
            ("style",    "🎨  3. Style & Giọng đọc"),
        ]
        for key, label in tab_defs:
            b = ctk.CTkButton(tab_bar, text=label,
                              font=("Segoe UI", 11), height=42,
                              fg_color="transparent", hover_color="#1e293b",
                              text_color="#64748b", corner_radius=0,
                              command=lambda k=key: _switch_tab(k))
            b.pack(side="left", padx=2)
            self._tab_btns[key] = b

        # ── CONTENT AREA (tabs + preview side by side) ────────────────────────
        content_area = ctk.CTkFrame(body, fg_color="#0a0a14", corner_radius=0)
        content_area.pack(fill="both", expand=True)

        # Tab container (trái)
        tab_container = ctk.CTkFrame(content_area, fg_color="#0a0a14", corner_radius=0)
        tab_container.pack(side="left", fill="both", expand=True)

        # Tạo 3 scrollable frames chồng lên nhau — dùng place để lift() hoạt động
        tab_container.update_idletasks()
        for key, _ in tab_defs:
            f = ctk.CTkScrollableFrame(tab_container, fg_color="#0a0a14",
                                        scrollbar_button_color="#334155")
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._tab_frames[key] = f

        # RIGHT: Preview
        right_frame = ctk.CTkFrame(content_area, width=470, fg_color="#0d0d1f", corner_radius=0)
        right_frame.pack(side="right", fill="y")
        right_frame.pack_propagate(False)

        # ── BOTTOM BAR ────────────────────────────────────────────────────────
        bottom_bar = ctk.CTkFrame(body, fg_color="#0d0d1f", height=48, corner_radius=0)
        bottom_bar.pack(fill="x", side="bottom")
        bottom_bar.pack_propagate(False)

        # Status dot + text
        status_dot = ctk.CTkLabel(bottom_bar, text="●", font=("Segoe UI", 10),
                                   text_color="#22c55e")
        status_dot.pack(side="left", padx=(12, 2), pady=14)
        self._status_label = ctk.CTkLabel(bottom_bar, text="Sẵn sàng",
                                          font=("Segoe UI", 10), text_color="#94a3b8")
        self._status_label.pack(side="left")
        ctk.CTkLabel(bottom_bar, text="|", text_color="#334155").pack(side="left", padx=8)

        # Log + Script Editor buttons (bottom)
        ctk.CTkButton(bottom_bar, text="📋 Xem log", width=90, height=32,
                      fg_color="transparent", hover_color="#1e293b",
                      text_color="#94a3b8", font=("Segoe UI", 10),
                      command=lambda: _switch_tab("du_an")).pack(side="left", padx=4)
        ctk.CTkButton(bottom_bar, text="✏️ Script Editor", width=110, height=32,
                      fg_color="transparent", hover_color="#1e293b",
                      text_color="#94a3b8", font=("Segoe UI", 10),
                      command=self._open_script_editor_manual).pack(side="left", padx=4)

        # CHẠY FULL PIPELINE (right of bottom bar)
        self.btn_run = ctk.CTkButton(
            bottom_bar,
            text="▶  CHẠY FULL PIPELINE",
            height=36, width=220,
            font=("Segoe UI", 12, "bold"),
            corner_radius=8,
            command=self.start_thread,
            fg_color="#4f46e5",
            hover_color="#4338ca",
        )
        self.btn_run.pack(side="right", padx=12, pady=6)
        
        # Store preview state
        self.preview_image = None
        self.preview_source_size = (0, 0)
        self.preview_frame_box = (0, 0, 0, 0)
        self.preview_scale = 1.0
        self.current_video_path = None
        
        # Store text positions (relative to preview size 380x600)
        self.header_x, self.header_y = 190, 80  # Center horizontally
        self.footer_x, self.footer_y = 190, 520  # Center horizontally
        self.dragging = None  # Track which text is being dragged
        
        # Store text colors (RGB tuples)
        self.header_color = (255, 255, 0)  # Yellow
        self.footer_color = (255, 255, 255)  # White
        self.header_bar_color = (255, 0, 0)
        self.footer_bar_color = (0, 174, 255)
        
        # Store font sizes
        self.header_font_size = 80
        self.footer_font_size = 60
        
        # ── Kích hoạt tab mặc định ─────────────────────────────────────────────
        _switch_tab("du_an")

        # Voice intro sample
        self.voice_intro_path = None
        self.source_srt_path = ""
        self.capcut_srt_output_path = ""

        # ── TAB 1: Dự án (file input + cắt video) ─────────────────────────────
        self.container = self._tab_frames["du_an"]
        self.create_label("📂  Thiết lập file & Công thức băm")
        self.video_path = self.create_file_input("Video gốc:")
        self.bgm_path = self.create_file_input("Nhạc nền (tuỳ chọn):")
        self.srt_path = self.create_file_input("SRT thoại nguồn (tự tạo/đã dịch):")
        self.output_dir = self.create_file_input("Nơi lưu:", is_dir=True)
        default_exports = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
        os.makedirs(default_exports, exist_ok=True)
        self.output_dir.insert(0, default_exports)



        self.cut_mode_presets = {
            "Thông minh (phân cảnh + nhân vật)": (4, 8),
            "Review nhanh": (2, 6),
            "Cân bằng": (5, 8),
            "Chi tiết": (8, 8),
            "Cinematic": (10, 15),
            "Liên tục theo cảnh": (1, 0),
            "Tùy chỉnh": None,
            # Alias cấu hình cũ, không hiển thị trong combobox mới.
            "Review nhanh (2s/6s)": (2, 6),
            "Cân bằng (5s/8s)": (5, 8),
            "Chi tiết (8s/8s)": (8, 8),
            "Cinematic (10s/15s)": (10, 15),
            "Không băm (liên tục)": (1, 0),
        }
        visible_cut_modes = [
            "Thông minh (phân cảnh + nhân vật)",
            "Review nhanh",
            "Cân bằng",
            "Chi tiết",
            "Cinematic",
            "Liên tục theo cảnh",
        ]
        cut_mode_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        cut_mode_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(cut_mode_frame, text="Kiểu preview:", width=120, anchor="w").pack(side="left")
        self.cut_mode = ctk.CTkComboBox(
            cut_mode_frame,
            values=visible_cut_modes,
            state="readonly",
            width=190,
            command=self.on_cut_mode_change,
        )
        self.cut_mode.set("Thông minh (phân cảnh + nhân vật)")
        self.cut_mode.pack(side="left", padx=5)

        # RECAP2 flow không còn dùng UI cắt cố định giữ/bỏ. Hai entry này chỉ
        # giữ giá trị fallback cho các đường legacy, nên không pack lên giao diện.
        self.keep_val = ctk.CTkEntry(self.container)
        self.skip_val = ctk.CTkEntry(self.container)
        self.keep_val.insert(0, "4")
        self.skip_val.insert(0, "8")
        self.keep_val.bind("<KeyRelease>", self._mark_cut_mode_custom)
        self.skip_val.bind("<KeyRelease>", self._mark_cut_mode_custom)
        
        # Duration selector
        frame_dur = ctk.CTkFrame(self.container, fg_color="transparent")
        frame_dur.pack(fill="x", pady=2)
        ctk.CTkLabel(frame_dur, text="Ngân sách review (phút):", width=170, anchor="w").pack(side="left")
        self.video_duration = ctk.CTkComboBox(
            frame_dur, 
            values=[
                "5 phút", "10 phút", "15 phút", "20 phút",
                "30 phút", "40 phút", "45 phút", "60 phút",
            ],
            state="readonly",
            command=self._on_review_budget_preset_selected,
        )
        self.video_duration.set("20 phút")
        self.video_duration.pack(side="left", padx=5)
        self.custom_review_minutes = ctk.CTkEntry(
            frame_dur,
            width=78,
            placeholder_text="Tự nhập",
        )
        self.custom_review_minutes.pack(side="left", padx=(4, 2))
        ctk.CTkLabel(frame_dur, text="phút", width=34).pack(side="left")
        ctk.CTkButton(
            frame_dur,
            text="BẰNG VIDEO GỐC",
            width=130,
            fg_color="#0f766e",
            hover_color="#115e59",
            command=self._set_review_budget_from_source,
        ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            frame_dur,
            text="? GIẢI THÍCH",
            width=105,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._show_review_budget_help,
        ).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(
            self.container,
            text="Số phút lời review AI dự kiến viết; vẫn phủ mở đầu - diễn biến - kết thúc phim, không phải cắt video nguồn.",
            text_color="#94a3b8",
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(fill="x", padx=(120, 0), pady=(0, 4))

        # Cut output folder
        self.cut_output_dir = self.create_file_input("Thư mục lưu preview:", is_dir=True)

        # Cut video button
        self.btn_cut_video = ctk.CTkButton(
            self.container,
            text="⚡ TẠO PREVIEW VIDEO BĂM",
            fg_color="#d97706",
            hover_color="#b45309",
            font=("Segoe UI", 12, "bold"),
            corner_radius=8,
            height=36,
            command=self.start_cut_video_thread
        )
        self.btn_cut_video.pack(fill="x", padx=(125, 5), pady=(4, 6))

        # ── TAB 2: Nội dung AI (CapCut SRT + AI Gemini) ───────────────────────
        self.container = self._tab_frames["noi_dung"]
        self.create_label("🎬  Tạo SRT bằng CapCut")

        srt_gen_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        srt_gen_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(srt_gen_frame, text="Video tạo SRT:", width=120, anchor="w").pack(side="left")
        self.srt_gen_video = ctk.CTkEntry(srt_gen_frame)
        self.srt_gen_video.pack(side="left", fill="x", expand=True, padx=5)

        def browse_srt_gen_video():
            p = filedialog.askopenfilename(
                title="Chọn video để tạo SRT trong CapCut",
                filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv"), ("All files", "*.*")]
            )
            if p:
                self.srt_gen_video.delete(0, "end")
                self.srt_gen_video.insert(0, p)

        # Bind click vào ô entry để mở file picker luôn
        self.srt_gen_video.bind("<Button-1>", lambda e: browse_srt_gen_video())

        ctk.CTkButton(srt_gen_frame, text="📁", width=50, command=browse_srt_gen_video).pack(side="left", padx=2)

        capcut_mode_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        capcut_mode_frame.pack(fill="x", pady=(1, 3))
        ctk.CTkLabel(
            capcut_mode_frame, text="Chế độ tạo SRT:", width=120, anchor="w"
        ).pack(side="left")
        self.capcut_srt_mode = ctk.StringVar(value=self.CAPCUT_SRT_MANUAL)
        self.capcut_srt_mode_menu = ctk.CTkOptionMenu(
            capcut_mode_frame,
            variable=self.capcut_srt_mode,
            values=[self.CAPCUT_SRT_AUTO, self.CAPCUT_SRT_MANUAL],
            width=220,
            command=self._on_capcut_srt_mode_change,
        )
        self.capcut_srt_mode_menu.pack(side="left", padx=5)
        self.capcut_srt_mode_hint = ctk.CTkLabel(
            capcut_mode_frame,
            text="Ổn định nhất: mở CapCut, tạo Auto Caption rồi đóng CapCut.",
            text_color="#94a3b8",
            anchor="w",
        )
        self.capcut_srt_mode_hint.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.btn_capcut_srt = ctk.CTkButton(
            self.container,
            text="🎬 AUTO CAPCUT TẠO SRT",
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=("Segoe UI", 12, "bold"),
            corner_radius=8,
            height=36,
            command=self.start_capcut_workflow_thread
        )
        self.btn_capcut_srt.pack(fill="x", padx=(125, 5), pady=(4, 6))

        # ── Device fingerprint ─────────────────────────────────────────────────
        dev_card = ctk.CTkFrame(self.container, fg_color="#16213e", corner_radius=6)
        dev_card.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            dev_card, text="🔑 Device ID:", width=120, anchor="w",
            font=("Segoe UI", 11), text_color="#94a3b8"
        ).pack(side="left", padx=(10, 0), pady=4)
        _init_dev = self._load_capcut_device()
        self._lbl_device_info = ctk.CTkLabel(
            dev_card,
            text=f"device_id: {_init_dev['device_id'][:12]}...  iid: {_init_dev['iid'][:12]}...",
            font=("Segoe UI", 10), text_color="#64748b", anchor="w"
        )
        self._lbl_device_info.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(
            dev_card, text="🎲 Đổi mới", width=80, height=26,
            font=("Segoe UI", 10), corner_radius=6,
            fg_color="#334155", hover_color="#475569", text_color="#e2e8f0",
            command=lambda: (self._generate_capcut_device(), self._refresh_device_label())
        ).pack(side="right", padx=(0, 8), pady=4)
        # ──────────────────────────────────────────────────────────────────────

        # Auto workflow da bi tat - khong tu dong chay Gemini sau CapCut
        self.auto_workflow_enabled = BooleanVar(value=False)

        self.create_label("🤖  AI Gemini & Nội dung")
        self.api_key = self.create_entry("Gemini API Key:", "Dán key vào đây...", show="*")
        self.api_keys_file = self.create_file_input("File keys .txt:")
        self.openrouter_api_key = self.create_entry("OpenRouter Key:", "sk-or-... (free text/vision fallback)", show="*")

        gemini_login_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        gemini_login_frame.pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(
            gemini_login_frame,
            text="Gemini Web:",
            width=120,
            anchor="w",
        ).pack(side="left")
        self.gemini_web_login_btn = ctk.CTkButton(
            gemini_login_frame,
            text="ĐĂNG NHẬP GEMINI WEB",
            width=210,
            fg_color="#0f766e",
            hover_color="#115e59",
            command=self.login_gemini_web,
        )
        self.gemini_web_login_btn.pack(side="left", padx=5)
        self.gemini_web_status_label = ctk.CTkLabel(
            gemini_login_frame,
            text="Đang kiểm tra phiên...",
            text_color="#94a3b8",
            anchor="w",
        )
        self.gemini_web_status_label.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._gemini_login_running = False
        self.after(300, self._refresh_gemini_web_login_status)

        self.movie_name = self.create_entry("Tên phim:", "Ví dụ: Người Nhện")
        self.movie_description = self.create_text_area(
            "Tóm tắt nội dung phim:",
            "Dán mô tả ngắn về cốt truyện phim..."
        )
        self._setup_review_style_selector()
        
        # Pipeline hiện là workflow chính. Kịch bản được tạo trong pipeline và chỉnh ở Script Editor,
        # nên các nút/ô review cũ được bỏ khỏi màn hình chính để tránh chạy nhầm workflow cũ.
        
        try:
            cfg = self.load_config()
            if cfg.get('gemini_api_key'):
                self.api_key.insert(0, cfg.get('gemini_api_key'))
            if cfg.get('gemini_keys_file'):
                self.api_keys_file.insert(0, cfg.get('gemini_keys_file'))
            if cfg.get('openrouter_api_key'):
                self.openrouter_api_key.insert(0, cfg.get('openrouter_api_key'))
            if cfg.get('review_style') and hasattr(self, "review_style"):
                self._set_review_style(cfg.get('review_style'), save=False)
            if hasattr(self, "capcut_srt_mode"):
                mode_key = str(cfg.get("capcut_srt_mode", "manual") or "manual").strip().lower()
                self.capcut_srt_mode.set(
                    self.CAPCUT_SRT_MANUAL if mode_key == "manual" else self.CAPCUT_SRT_AUTO
                )
                self._on_capcut_srt_mode_change(self.capcut_srt_mode.get(), save=False)
            # Tự động load preview nếu đã có video từ lần trước
            if cfg.get('video_source'):
                vpath = cfg.get('video_source', '').strip()
                if vpath and os.path.exists(vpath):
                    self.current_video_path = vpath
                    self.after(1500, lambda: threading.Thread(
                        target=self.load_video_preview, daemon=True
                    ).start())
            # Sync thanh tiêu đề preview (chỉ khi đã có video)
            # KHÔNG vẽ placeholder — canvas giữ màu đen cho đến khi user chọn video
        except Exception:
            pass

        
        # ── TAB 3: Style & Giọng đọc ──────────────────────────────────────────
        self.container = self._tab_frames["style"]
        self.create_label("🖼️  Header & Footer")
        self.header_text = self.create_entry("Tiêu đề trên:", "CHƯA CÓ TIÊU ĐỀ")
        self.footer_text = self.create_entry("Tiêu đề dưới:", "XEM NGAY KẾT CỤC")
        
        # Style options
        self.create_label("📐  Style & Căn chỉnh")
        
        # Header alignment
        h_align_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        h_align_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(h_align_frame, text="Vị trí tiêu đề trên:", width=120, anchor="w").pack(side="left")
        self.header_align = ctk.CTkComboBox(
            h_align_frame, 
            values=["Trái", "Giữa", "Phải"],
            state="readonly",
            width=100
        )
        self.header_align.set("Giữa")
        self.header_align.pack(side="left", padx=5)
        self.header_align.configure(command=self.update_preview_delayed)
        
        # Footer alignment
        f_align_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        f_align_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(f_align_frame, text="Vị trí tiêu đề dưới:", width=120, anchor="w").pack(side="left")
        self.footer_align = ctk.CTkComboBox(
            f_align_frame, 
            values=["Trái", "Giữa", "Phải"],
            state="readonly",
            width=100
        )
        self.footer_align.set("Giữa")
        self.footer_align.pack(side="left", padx=5)
        self.footer_align.configure(command=self.update_preview_delayed)
        
        # Font size sliders and color controls
        self.create_label("🎨  Font & Màu sắc")
        
        # Header font size slider
        h_font_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        h_font_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(h_font_frame, text="Font tiêu đề trên:", width=120, anchor="w").pack(side="left", pady=2)
        self.header_font_slider = ctk.CTkSlider(
            h_font_frame, from_=20, to=120, number_of_steps=100,
            command=self.on_header_font_change
        )
        self.header_font_slider.set(80)
        self.header_font_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.header_font_label = ctk.CTkLabel(h_font_frame, text="80", width=40, anchor="w")
        self.header_font_label.pack(side="left", padx=2)
        
        # Footer font size slider
        f_font_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        f_font_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(f_font_frame, text="Font tiêu đề dưới:", width=120, anchor="w").pack(side="left", pady=2)
        self.footer_font_slider = ctk.CTkSlider(
            f_font_frame, from_=20, to=120, number_of_steps=100,
            command=self.on_footer_font_change
        )
        self.footer_font_slider.set(60)
        self.footer_font_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.footer_font_label = ctk.CTkLabel(f_font_frame, text="60", width=40, anchor="w")
        self.footer_font_label.pack(side="left", padx=2)
        
        # Header color picker
        h_color_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        h_color_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(h_color_frame, text="Màu tiêu đề trên:", width=120, anchor="w").pack(side="left")
        self.header_color_btn = ctk.CTkButton(
            h_color_frame, text="🎨 Chọn màu", width=100,
            command=self.pick_header_color, fg_color="#FFFF00"
        )
        self.header_color_btn.pack(side="left", padx=5)
        
        # Footer color picker
        f_color_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        f_color_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(f_color_frame, text="Màu tiêu đề dưới:", width=120, anchor="w").pack(side="left")
        self.footer_color_btn = ctk.CTkButton(
            f_color_frame, text="🎨 Chọn màu", width=100,
            command=self.pick_footer_color, fg_color="#FFFFFF"
        )
        self.footer_color_btn.pack(side="left", padx=5)
        
        h_bar_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        h_bar_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(h_bar_frame, text="Màu thanh trên:", width=120, anchor="w").pack(side="left")
        self.header_bar_color_btn = ctk.CTkButton(
            h_bar_frame, text="▮ Chọn màu", width=100,
            command=self.pick_header_bar_color, fg_color="#FF0000"
        )
        self.header_bar_color_btn.pack(side="left", padx=5)

        f_bar_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        f_bar_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(f_bar_frame, text="Màu thanh dưới:", width=120, anchor="w").pack(side="left")
        self.footer_bar_color_btn = ctk.CTkButton(
            f_bar_frame, text="▮ Chọn màu", width=100,
            command=self.pick_footer_bar_color, fg_color="#00AEFF"
        )
        self.footer_bar_color_btn.pack(side="left", padx=5)

        # AI Gen button
        btn_gen_title = ctk.CTkButton(self.container, text="✨ AI TẠO TIÊU ĐỀ ẢNH (KHÔNG SỬA SCRIPT)", 
                                       command=self.auto_gen_title, fg_color="purple")
        btn_gen_title.pack(pady=10)

        # Hidden compat vars (không hiển thị UI)
        button_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.workflow_enabled = BooleanVar(value=True)
        self._full_pipeline_mode = BooleanVar(value=True)
        self.workflow_check = ctk.CTkCheckBox(button_frame, text="WORKFLOW", variable=self.workflow_enabled)
        self._fp_mode_check = ctk.CTkCheckBox(button_frame, text="FULL PIPELINE", variable=self._full_pipeline_mode)

        # Log textbox — trong tab 1
        self.container = self._tab_frames["du_an"]
        self.log = ctk.CTkTextbox(self.container, height=140, fg_color="#0d0d1f",
                                   border_color="#1e293b", border_width=1,
                                   font=("Consolas", 10))
        self.log.pack(fill="x", pady=(8, 4), padx=2)

        # Resume row — trong tab 1
        resume_row = ctk.CTkFrame(self.container, fg_color="transparent")
        resume_row.pack(fill="x", pady=(0, 8))
        self.btn_resume_pipeline = ctk.CTkButton(
            resume_row, text="▶️ Tiếp tục từ bước lỗi",
            height=32, font=("Segoe UI", 11, "bold"),
            fg_color="#7c3aed", hover_color="#6d28d9",
            command=self._resume_pipeline,
        )
        self.btn_resume_pipeline.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.btn_open_script_editor = ctk.CTkButton(
            resume_row, text="✏️ Script Editor",
            height=32, font=("Segoe UI", 11),
            fg_color="#0f766e", hover_color="#115e59",
            command=self._open_script_editor_manual,
        )
        self.btn_open_script_editor.pack(side="left", fill="both", expand=True, padx=(4, 0))

        # ── FULL PIPELINE step labels (ẩn UI, chỉ dùng nội bộ để cập nhật status) ──
        # fp_frame không pack → toàn bộ section bị ẩn
        self._fp_step_labels = {}
        fp_frame = ctk.CTkFrame(self.container, fg_color="#1a1a2e", corner_radius=8)
        # KHÔNG pack fp_frame → ẩn toàn bộ section bên dưới

        STEP_DISPLAY = [
            ("METADATA",       "📐 METADATA"),
            ("TRANSCRIPT",     "🎙️ TRANSCRIPT"),
            ("SCENE_DETECT",   "🎬 SCENE_DETECT"),
            ("SUBTITLE_MAP",   "📋 SUBTITLE_MAP"),
            ("KEYFRAMES",      "🖼️ KEYFRAMES"),
            ("AI_FULL",        "🤖 AI_FULL"),
            ("CLIP_FIND",      "✂️ CLIP_FIND"),
            ("VOICE_SEGMENTS", "🔊 VOICE_SEGMENTS"),
            ("VOICE_CONCAT",   "🎵 VOICE_CONCAT"),
            ("VOICE_SRT",      "📝 VOICE_SRT"),
            ("RENDER_FINAL",   "🎬 RENDER_FINAL"),
        ]

        steps_grid = ctk.CTkFrame(fp_frame, fg_color="transparent")
        for col, (key, label) in enumerate(STEP_DISPLAY):
            cell = ctk.CTkFrame(steps_grid, fg_color="#2a2a3e", corner_radius=4)
            status_lbl = ctk.CTkLabel(cell, text="⏳", font=("Arial", 11))
            self._fp_step_labels[key] = status_lbl

        # Skip options (giữ biến để pipeline đọc được)
        self._fp_skip_transcript = BooleanVar(value=False)
        self._fp_skip_scene      = BooleanVar(value=False)
        self._fp_skip_kf         = BooleanVar(value=False)

        # btn_full_pipeline giữ lại để tránh lỗi tham chiếu
        self.btn_full_pipeline = ctk.CTkButton(
            fp_frame,
            text="🚀 CHẠY FULL PIPELINE",
            command=self._start_full_pipeline_thread,
        )
        # KHÔNG pack → ẩn
        # ── END FULL PIPELINE (hidden) ─────────────────────────────

        # RIGHT SECTION: Preview
        preview_label = ctk.CTkLabel(right_frame, text="● PREVIEW",
                                      font=("Segoe UI", 11, "bold"), text_color="#6366f1")
        preview_label.pack(pady=(10, 4), padx=10, anchor="w")

        # Canvas video preview — đủ cao để chứa video + 2 thanh tiêu đề trên/dưới
        # Người dùng kéo 2 thanh tiêu đề lên/xuống để chỉnh vị trí
        self.preview_canvas = Canvas(right_frame, bg="black", width=450, height=340, highlightthickness=0)
        self.preview_canvas.pack(padx=8, pady=4)

        # Bind mouse events — kéo thả 2 thanh tiêu đề
        self.preview_canvas.bind("<Button-1>",      self.on_canvas_press)
        self.preview_canvas.bind("<B1-Motion>",     self.on_canvas_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self.on_canvas_release)

        self.preview_info = ctk.CTkLabel(
            right_frame,
            text="Chọn video → kéo thanh tiêu đề lên/xuống để căn vị trí",
            text_color="gray", font=("Arial", 10), wraplength=430,
        )
        self.preview_info.pack(pady=(2, 2))

        # ── Voice selection (dưới preview, chia 2 dòng) ──────────────────────
        voice_row1 = ctk.CTkFrame(right_frame, fg_color="transparent")
        voice_row1.pack(fill="x", padx=10, pady=(4, 2))
        ctk.CTkLabel(voice_row1, text="Ngôn ngữ:", width=72, anchor="w").pack(side="left")
        self.tts_language = ctk.CTkComboBox(
            voice_row1, values=["Tiếng Việt", "English"],
            state="readonly", width=120, command=self.on_tts_language_change,
        )
        self.tts_language.set("Tiếng Việt")
        self.tts_language.pack(side="left", padx=(0, 4))

        voice_row2 = ctk.CTkFrame(right_frame, fg_color="transparent")
        voice_row2.pack(fill="x", padx=10, pady=(0, 4))
        self.voice_choice = ctk.CTkComboBox(
            voice_row2, values=self.get_voice_options("Tiếng Việt"),
            state="readonly", width=270,
        )
        self.voice_choice.set("Review nữ - vi-VN-HoaiMyNeural")
        self.voice_choice.pack(side="left", padx=(0, 4))
        # voice_row alias dùng cho btn_preview_voice pack bên dưới
        voice_row = voice_row2

        self.btn_preview_voice = ctk.CTkButton(
            voice_row,
            text="🎧 Nghe thử",
            width=90,
            fg_color="#0f766e",
            hover_color="#115e59",
            command=self._preview_voice_sample,
        )
        self.btn_preview_voice.pack(side="left")

    def create_label(self, text):
        """Section header — gọn, chỉ là label có màu accent."""
        ctk.CTkLabel(
            self.container, text=text,
            font=("Segoe UI", 11, "bold"),
            text_color="#818cf8",
            anchor="w",
        ).pack(fill="x", padx=4, pady=(10, 2))

    def create_file_input(self, label_text, is_dir=False):
        frame = ctk.CTkFrame(self.container, fg_color="#16213e", corner_radius=6)
        frame.pack(fill="x", pady=2)
        ctk.CTkLabel(frame, text=label_text, width=120, anchor="w",
                     font=("Segoe UI", 11), text_color="#94a3b8").pack(side="left", padx=(10, 0), pady=3)
        entry = ctk.CTkEntry(frame)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        lower_label = label_text.lower()
        def on_change(event=None):
            self.update_preview_delayed()
            if ("video" in lower_label and not is_dir) or (is_dir and any(key in lower_label for key in ("lưu", "output", "thư mục"))):
                self._sync_cut_related_paths()
        entry.bind("<KeyRelease>", on_change)
        
        def browse():
            if is_dir:
                p = filedialog.askdirectory()
            elif "video" in lower_label:
                p = filedialog.askopenfilename(
                    title="Chọn video gốc",
                    filetypes=[
                        ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                        ("All files", "*.*"),
                    ],
                )
            elif "srt" in lower_label:
                p = filedialog.askopenfilename(
                    title="Chọn SRT thoại gốc đã dịch",
                    filetypes=[("SRT subtitles", "*.srt"), ("All files", "*.*")],
                )
            elif "key" in lower_label:
                p = filedialog.askopenfilename(
                    title="Chọn file Gemini API keys",
                    filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                )
            else:
                p = filedialog.askopenfilename()
            if p:
                try:
                    if (not is_dir) and ("video" in lower_label):
                        ext = os.path.splitext(p)[1].lower()
                        if ext == ".srt":
                            if hasattr(self, 'srt_path'):
                                self._set_source_srt_path(p, auto_detected=False)
                            if hasattr(self, 'preview_info'):
                                self.preview_info.configure(
                                    text="ℹ️ Bạn vừa chọn SRT. File đã được chuyển sang ô SRT thoại nguồn.",
                                    text_color="orange"
                                )
                            return
                        if ext not in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"]:
                            if hasattr(self, 'preview_info'):
                                self.preview_info.configure(
                                    text="❌ Video gốc phải là file video (.mp4/.mkv/.mov/.avi)",
                                    text_color="red"
                                )
                            return
                    entry.delete(0, "end")
                    entry.insert(0, p)
                    if hasattr(self, 'srt_path') and entry == self.srt_path:
                        self.source_srt_path = p
                    if hasattr(self, "video_path") and entry == self.video_path:
                        self._ensure_video_output_dir(p, force_new=False, update_entries=True)
                        self._sync_cut_related_paths(p)
                    elif is_dir and hasattr(self, "video_path") and (
                        entry == self.output_dir or entry == self.cut_output_dir
                    ):
                        self._sync_cut_related_paths()
                    if (not is_dir) and ("video" in label_text.lower() or "video gốc" in label_text.lower()):
                        basename = os.path.splitext(os.path.basename(p))[0]
                        if hasattr(self, 'movie_name') and self.movie_name.get().strip() == "":
                            self.movie_name.delete(0, "end")
                            self.movie_name.insert(0, basename)
                        # Load video preview
                        self.current_video_path = p
                        self.save_config({"video_source": p})  # lưu để auto-load lần sau
                        if hasattr(self, 'movie_description'):
                            self._set_textbox_text(self.movie_description, "")
                        if hasattr(self, 'review_script_box'):
                            self._set_textbox_text(self.review_script_box, "")
                        if hasattr(self, 'review_srt_box'):
                            self._set_textbox_text(self.review_srt_box, "")
                        if hasattr(self, 'srt_path'):
                            self.srt_path.delete(0, "end")
                            self.source_srt_path = ""
                            self._ensure_source_srt(prompt_if_missing=False)
                        threading.Thread(target=self.load_video_preview, daemon=True).start()
                except Exception as e:
                    print(f"Error: {e}")
        
        ctk.CTkButton(frame, text="📂", width=40,
                      fg_color="#6366f1", hover_color="#4f46e5",
                      command=browse).pack(side="right", padx=6, pady=2)
        return entry

    def _safe_output_slug(self, value: str, max_len: int = 70) -> str:
        base = os.path.splitext(os.path.basename(str(value or "").strip()))[0] or "video"
        base = re.sub(r"[^\w\-. À-ỹà-ỹĐđ]+", "_", base, flags=re.UNICODE)
        base = re.sub(r"_+", "_", base).strip(" _.")
        return (base or "video")[:max_len]

    def _is_autorecap_job_dir(self, path: str) -> bool:
        if not path:
            return False
        return os.path.exists(os.path.join(path, ".autorecap_job.json"))

    def _looks_like_autorecap_job_dir(self, path: str) -> bool:
        name = os.path.basename(os.path.abspath(str(path or "")))
        return bool(re.search(r"_\d{8}_\d{6}(?:_\d+)?$", name))

    def _output_base_from_entry(self, fallback_video_path: str = "") -> str:
        current = ""
        try:
            current = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        except Exception:
            current = ""
        if current:
            current_abs = os.path.abspath(current)
            # If the entry points to a previous job folder, or to a subfolder
            # inside it, climb out to the real exports root. This prevents
            # nested outputs like D:\exports\video_...\video_...\keyframes.
            probe = current_abs
            last_job = ""
            while probe and probe != os.path.dirname(probe):
                if self._is_autorecap_job_dir(probe) or self._looks_like_autorecap_job_dir(probe):
                    last_job = probe
                probe = os.path.dirname(probe)
            if last_job:
                parent = os.path.dirname(last_job)
                while parent and parent != os.path.dirname(parent):
                    if self._is_autorecap_job_dir(parent) or self._looks_like_autorecap_job_dir(parent):
                        last_job = parent
                        parent = os.path.dirname(parent)
                        continue
                    break
                return os.path.dirname(last_job)
            return current_abs
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")

    def _read_output_job_marker(self, output_dir: str) -> dict:
        try:
            marker = os.path.join(output_dir, ".autorecap_job.json")
            if os.path.exists(marker):
                with open(marker, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _same_video_marker(self, output_dir: str, video_path: str) -> bool:
        marker = self._read_output_job_marker(output_dir)
        if not marker:
            return False
        try:
            sig = self._video_file_signature(video_path)
            return marker.get("video_signature", {}).get("path") == sig.get("path")
        except Exception:
            return os.path.abspath(marker.get("source_video", "")) == os.path.abspath(video_path)

    def _find_existing_output_job_dir(self, base_dir: str, video_path: str) -> str:
        if not base_dir or not os.path.isdir(base_dir):
            return ""
        matches = []
        try:
            candidates = []
            for root, dirs, files in os.walk(base_dir):
                depth = os.path.relpath(root, base_dir).count(os.sep)
                if depth > 2:
                    dirs[:] = []
                    continue
                if ".autorecap_job.json" in files:
                    candidates.append(root)
            for candidate in candidates:
                if self._same_video_marker(candidate, video_path):
                    try:
                        mtime = os.path.getmtime(os.path.join(candidate, ".autorecap_job.json"))
                    except Exception:
                        mtime = os.path.getmtime(candidate)
                    matches.append((mtime, candidate))
        except Exception:
            return ""
        if not matches:
            return ""
        matches.sort(reverse=True)
        return matches[0][1]

    def _ensure_video_output_dir(self, video_path: str, force_new: bool = False, update_entries: bool = False) -> str:
        """Create or reuse one isolated output folder per source video."""
        video_path = os.path.abspath(str(video_path or "").strip())
        if not video_path:
            return ""
        try:
            current = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        except Exception:
            current = ""
        if current and self._is_autorecap_job_dir(current) and self._same_video_marker(current, video_path) and not force_new:
            return current

        base_dir = self._output_base_from_entry(video_path)
        os.makedirs(base_dir, exist_ok=True)
        if not force_new:
            existing = self._find_existing_output_job_dir(base_dir, video_path)
            if existing:
                if update_entries:
                    try:
                        self.output_dir.delete(0, "end")
                        self.output_dir.insert(0, existing)
                    except Exception:
                        pass
                    try:
                        if hasattr(self, "cut_output_dir"):
                            self.cut_output_dir.delete(0, "end")
                            self.cut_output_dir.insert(0, existing)
                    except Exception:
                        pass
                    try:
                        self._thread_safe_log(f"📁 Dùng lại output đã có cho video: {existing}\n")
                    except Exception:
                        pass
                return existing
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = self._safe_output_slug(video_path)
        out_dir = os.path.join(base_dir, f"{slug}_{stamp}")
        suffix = 2
        while os.path.exists(out_dir):
            out_dir = os.path.join(base_dir, f"{slug}_{stamp}_{suffix}")
            suffix += 1
        os.makedirs(out_dir, exist_ok=True)
        try:
            marker = {
                "app": "AutoRecapPro_V2",
                "created_at": stamp,
                "source_video": video_path,
                "video_signature": self._video_file_signature(video_path),
            }
            with open(os.path.join(out_dir, ".autorecap_job.json"), "w", encoding="utf-8") as f:
                json.dump(marker, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        if update_entries:
            try:
                self.output_dir.delete(0, "end")
                self.output_dir.insert(0, out_dir)
            except Exception:
                pass
            try:
                if hasattr(self, "cut_output_dir"):
                    self.cut_output_dir.delete(0, "end")
                    self.cut_output_dir.insert(0, out_dir)
            except Exception:
                pass
            try:
                self._thread_safe_log(f"📁 Tạo output riêng cho video: {out_dir}\n")
            except Exception:
                pass
        return out_dir

    def create_text_area(self, label_text, placeholder, height=80):
        frame = ctk.CTkFrame(self.container, fg_color="#16213e", corner_radius=6)
        frame.pack(fill="x", pady=2)
        ctk.CTkLabel(frame, text=label_text, width=120, anchor="nw",
                     font=("Segoe UI", 11), text_color="#94a3b8").pack(side="left", padx=(10, 0), pady=4)
        textbox = ctk.CTkTextbox(frame, height=height, fg_color="#0f172a", border_color="#334155", border_width=1)
        textbox.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        return textbox

    def _get_textbox_text(self, textbox):
        if not textbox:
            return ""
        try:
            return textbox.get("1.0", "end").strip()
        except Exception:
            return ""

    def _set_textbox_text(self, textbox, text):
        if not textbox:
            return
        try:
            textbox.delete("1.0", "end")
            if text:
                textbox.insert("1.0", text.strip())
        except Exception:
            pass

    def _append_log(self, message):
        if not hasattr(self, "log"):
            return
        try:
            self.log.insert("end", message)
            self.log.see("end")
        except Exception:
            pass

    @staticmethod
    def _clipboard_text_matches(actual, expected):
        actual_norm = str(actual or "").replace("\r\n", "\n").strip()
        expected_norm = str(expected or "").replace("\r\n", "\n").strip()
        if not expected_norm:
            return False
        if actual_norm == expected_norm:
            return True
        head = min(400, len(expected_norm))
        tail = min(400, len(expected_norm))
        return (
            len(actual_norm) >= min(len(expected_norm), 800)
            and actual_norm[:head] == expected_norm[:head]
            and actual_norm[-tail:] == expected_norm[-tail:]
        )

    def _read_system_clipboard_text(self):
        if PYPERCLIP_AVAILABLE:
            try:
                return pyperclip.paste() or ""
            except Exception:
                pass
        try:
            return self.clipboard_get()
        except Exception:
            pass
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-Clipboard -Raw",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                creationflags=flags,
            )
            if proc.returncode == 0:
                return proc.stdout or ""
        except Exception:
            pass
        return ""

    def _copy_with_tk_clipboard(self, text, timeout_seconds=3.0):
        result = {"ok": False, "error": ""}

        def _do_copy():
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.update_idletasks()
                result["ok"] = True
            except Exception as exc:
                result["error"] = str(exc)

        if threading.current_thread() is threading.main_thread():
            _do_copy()
            return result

        done = threading.Event()

        def _wrapped():
            try:
                _do_copy()
            finally:
                done.set()

        try:
            self.after(0, _wrapped)
            done.wait(timeout_seconds)
        except Exception as exc:
            result["error"] = str(exc)
        if not done.is_set() and not result["ok"]:
            result["error"] = result["error"] or "tk_clipboard_timeout"
        return result

    def _copy_with_powershell_clipboard(self, text):
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); "
                    "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
                ],
                input=text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=12,
                creationflags=flags,
            )
            return {"ok": proc.returncode == 0, "error": (proc.stderr or "").strip()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _copy_text_to_system_clipboard(self, text):
        errors = []
        methods = []

        def _verify(method_name):
            clip = self._read_system_clipboard_text()
            if self._clipboard_text_matches(clip, text):
                return True
            errors.append(f"{method_name}: verify_failed")
            return False

        if PYPERCLIP_AVAILABLE:
            try:
                pyperclip.copy(text)
                methods.append("pyperclip")
                if _verify("pyperclip"):
                    return True, "pyperclip"
            except Exception as exc:
                errors.append(f"pyperclip: {exc}")

        tk_result = self._copy_with_tk_clipboard(text)
        if tk_result.get("ok"):
            methods.append("tk")
            if _verify("tk"):
                return True, "tk"
        elif tk_result.get("error"):
            errors.append(f"tk: {tk_result.get('error')}")

        ps_result = self._copy_with_powershell_clipboard(text)
        if ps_result.get("ok"):
            methods.append("powershell")
            if _verify("powershell"):
                return True, "powershell"
        elif ps_result.get("error"):
            errors.append(f"powershell: {ps_result.get('error')}")

        if methods:
            return True, f"{methods[-1]} (chưa verify được)"
        return False, "; ".join(errors[-3:]) or "unknown_clipboard_error"

    def _save_manual_ai_studio_prompt_file(self, prompt, filename="manual_ai_studio_prompt.txt"):
        output_dir = ""
        try:
            output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        except Exception:
            output_dir = ""
        if not output_dir:
            output_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(output_dir, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(filename or "")).strip("._")
        if not safe_name:
            safe_name = "manual_ai_studio_prompt.txt"
        prompt_path = os.path.join(output_dir, safe_name)
        with open(prompt_path, "w", encoding="utf-8") as handle:
            handle.write(prompt)
        return prompt_path

    def _looks_like_ai_studio_result(self, text, prompt_text=""):
        text = str(text or "").strip()
        if len(text) < 80:
            return False
        if prompt_text and text == str(prompt_text).strip():
            return False
        lower = text.lower()
        if "thông tin phim:" in lower and "phụ đề srt" in lower and "tạo kịch bản ngay" in lower:
            return False
        if any(marker in lower for marker in ('"script"', '"script_blocks"', '"subtitle_chunks"', "script_blocks")):
            return True
        vietnamese_words = re.findall(r"[A-Za-zÀ-ỹ]{2,}", text)
        sentence_count = len(re.findall(r"[.!?]\s+", text + " "))
        return len(vietnamese_words) >= 60 and sentence_count >= 3

    def _parse_ai_studio_review_result(self, text, target_seconds=None):
        raw = str(text or "").strip()
        data = AIEngine._extract_json_payload(raw) or {}
        summary = ""
        script = ""
        chunks = []
        if isinstance(data, dict) and data:
            summary = AIEngine._clean_review_script_text(str(data.get("summary") or ""))
            script = AIEngine._clean_review_script_text(str(data.get("script") or ""))
            blocks = data.get("script_blocks") or []
            if not script and isinstance(blocks, list):
                script = "\n\n".join(
                    AIEngine._clean_review_script_text(str(block.get("text") or ""))
                    for block in blocks
                    if isinstance(block, dict) and str(block.get("text") or "").strip()
                ).strip()
            chunks = data.get("subtitle_chunks") or []
            if not chunks and isinstance(blocks, list):
                chunks = [
                    str(block.get("text") or "").strip()
                    for block in blocks
                    if isinstance(block, dict) and str(block.get("text") or "").strip()
                ]
        if not script:
            script = AIEngine._clean_review_script_text(raw)
        if not isinstance(chunks, list) or not chunks:
            chunks = AIEngine._split_subtitle_chunks(script)
        srt_text = self._build_review_srt(chunks, target_seconds) if chunks else ""
        return summary, script, srt_text

    def _apply_ai_studio_clipboard_result(self, text, target_seconds=None):
        try:
            summary, script_text, srt_text = self._parse_ai_studio_review_result(text, target_seconds)
            if not script_text:
                self._append_log("⚠️ Clipboard có nội dung nhưng không đọc được kịch bản hợp lệ.\n")
                return False
            if hasattr(self, "review_script_box"):
                self._set_textbox_text(self.review_script_box, script_text)
            if srt_text and hasattr(self, "review_srt_box"):
                self._set_textbox_text(self.review_srt_box, srt_text)
            try:
                script_path, srt_path = self._save_review_package_assets(script_text, srt_text)
                self._append_log(f"✅ Đã nhận kết quả từ AI Studio và lưu:\n  Script: {script_path}\n  SRT: {srt_path}\n")
            except Exception as save_error:
                self._append_log(f"✅ Đã nhận kết quả từ AI Studio vào app. Không lưu được file: {save_error}\n")
            if summary:
                self._append_log("ℹ️ Summary từ AI Studio đã đọc được; giữ nguyên mô tả phim hiện tại để tránh ghi đè.\n")
            return True
        except Exception as e:
            self._append_log(f"❌ Lỗi nhập kết quả AI Studio từ clipboard: {e}\n")
            return False

    def _start_ai_studio_clipboard_watch(self, prompt_text, target_seconds=None, timeout_seconds=900):
        token = str(time.time())
        self._ai_studio_watch_token = token
        deadline = time.time() + timeout_seconds
        self._append_log("👀 App đang chờ bạn copy kết quả từ AI Studio. Khi copy xong, app sẽ tự đưa vào ô kịch bản/sub.\n")

        def _poll():
            if getattr(self, "_ai_studio_watch_token", None) != token:
                return
            if time.time() > deadline:
                self._ai_studio_watch_token = None
                self._append_log("⏳ Hết thời gian chờ clipboard AI Studio. Bạn vẫn có thể paste thủ công.\n")
                return
            clip = self._read_system_clipboard_text()
            if self._looks_like_ai_studio_result(clip, prompt_text):
                self._ai_studio_watch_token = None
                self._apply_ai_studio_clipboard_result(clip, target_seconds)
                return
            self.after(2000, _poll)

        self.after(2000, _poll)

    @staticmethod
    def _extract_srt_payload_from_ai_studio_text(text):
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        if "```" in cleaned:
            cleaned = re.sub(r"```(?:srt|text|plaintext)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.replace("```", "").strip()
        marker = re.search(
            r"(?m)^\s*\d+\s*\n\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}",
            cleaned,
        )
        if marker:
            cleaned = cleaned[marker.start():]
        return cleaned.strip() + ("\n" if cleaned.strip() else "")

    @staticmethod
    def _parse_srt_payload_text(srt_text):
        normalized = str(srt_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as handle:
                temp_path = handle.name
                handle.write(normalized + "\n")
            return SRTParser.parse_srt(temp_path)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def _text_has_vietnamese_diacritics(text):
        return bool(re.search(r"[À-ỹ]", str(text or "")))

    @classmethod
    def _srt_text_has_enough_vietnamese_marks(cls, subtitles):
        combined = " ".join(str(item.get("text") or "") for item in subtitles or [])
        if re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]", combined):
            return False
        letters = re.findall(r"[A-Za-zÀ-ỹ]", combined)
        if len(letters) < 120:
            return True
        marks = re.findall(r"[À-ỹ]", combined)
        return len(marks) >= max(3, int(len(letters) * 0.005))

    def _looks_like_ai_studio_srt_result(self, text, prompt_text=""):
        text = str(text or "").strip()
        if len(text) < 40:
            return False
        if prompt_text and self._clipboard_text_matches(text, prompt_text):
            return False
        lower = text.lower()
        if "srt nguồn cần dịch" in lower and "giữ nguyên số thứ tự" in lower:
            return False
        candidate = self._extract_srt_payload_from_ai_studio_text(text)
        timestamp_count = len(
            re.findall(
                r"\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}",
                candidate,
            )
        )
        return timestamp_count >= 1

    def _apply_ai_studio_srt_translation_result(
        self,
        text,
        source_srt_path,
        output_path,
        auto_continue_review=True,
    ):
        try:
            candidate = self._extract_srt_payload_from_ai_studio_text(text)
            translated_subtitles = self._parse_srt_payload_text(candidate)
            if not translated_subtitles:
                self._append_log("⚠️ Clipboard có nội dung nhưng không đọc được SRT hợp lệ.\n")
                return False

            source_count = 0
            if source_srt_path and os.path.exists(source_srt_path):
                try:
                    source_count = len(SRTParser.parse_srt(source_srt_path))
                except Exception:
                    source_count = 0
            if source_count and len(translated_subtitles) < max(1, int(source_count * 0.88)):
                self._append_log(
                    f"❌ SRT dịch bị thiếu dòng: {len(translated_subtitles)}/{source_count}. "
                    "Hãy copy lại toàn bộ kết quả từ Gemini.\n"
                )
                return False

            if not self._srt_text_has_enough_vietnamese_marks(translated_subtitles):
                self._append_log(
                    "❌ SRT Gemini trả về vẫn thiếu dấu tiếng Việt. App không lưu để tránh TTS đọc sai.\n"
                )
                return False

            output_path = output_path or self._translated_srt_output_path(source_srt_path)
            if not output_path:
                raise ValueError("Không xác định được đường dẫn lưu SRT dịch")
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(candidate.strip() + "\n")
            output_path = self._mirror_translated_srt_to_output_dir(output_path)

            self.source_srt_path = output_path
            self._set_source_srt_path_threadsafe(output_path, auto_detected=True)
            self._append_log(
                f"✅ Đã nhận SRT dịch từ AI Studio: {output_path} "
                f"({len(translated_subtitles)} dòng)\n"
            )

            if auto_continue_review:
                self._append_log("➡️ SRT tiếng Việt đã sẵn sàng. App tự mở tiếp prompt tạo kịch bản review.\n")
                threading.Thread(target=self._manual_review_prompt_worker, daemon=True).start()
            return True
        except Exception as e:
            self._append_log(f"❌ Lỗi nhập SRT dịch từ AI Studio: {e}\n")
            return False

    def _start_ai_studio_srt_translation_watch(
        self,
        prompt_text,
        source_srt_path,
        output_path,
        timeout_seconds=900,
        auto_continue_review=True,
    ):
        token = str(time.time())
        self._ai_studio_watch_token = token
        deadline = time.time() + timeout_seconds
        rejected_clipboards = set()
        self._append_log(
            "👀 App đang chờ bạn copy SRT đã dịch từ AI Studio. Copy xong app tự lưu và mở prompt review.\n"
        )

        def _poll():
            if getattr(self, "_ai_studio_watch_token", None) != token:
                return
            if time.time() > deadline:
                self._ai_studio_watch_token = None
                self._append_log("⏳ Hết thời gian chờ SRT dịch từ AI Studio. Bạn vẫn có thể chạy lại nút web.\n")
                return
            clip = self._read_system_clipboard_text()
            if self._looks_like_ai_studio_srt_result(clip, prompt_text):
                signature = str(hash(clip))
                if signature not in rejected_clipboards:
                    ok = self._apply_ai_studio_srt_translation_result(
                        clip,
                        source_srt_path,
                        output_path,
                        auto_continue_review=auto_continue_review,
                    )
                    if ok:
                        self._ai_studio_watch_token = None
                        return
                    rejected_clipboards.add(signature)
            self.after(2000, _poll)

        self.after(2000, _poll)

    @staticmethod
    def _normalize_srt_reference_tokens(text):
        value = unicodedata.normalize("NFKD", str(text or ""))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.lower()
        stopwords = {
            "full",
            "review",
            "reviews",
            "subtitle",
            "subtitles",
            "srt",
            "whisper",
            "vietsub",
            "viet",
            "vi",
            "video",
            "movie",
            "film",
            "clip",
            "official",
            "tiktok",
            "mp4",
            "mkv",
            "mov",
            "avi",
            "webm",
            "m4v",
            "hd",
            "4k",
            "1080p",
            "720p",
            "1080",
            "720",
            "fullhd",
            "motchill",
            "mot",
            "chill",
            "com",
            "www",
            "http",
            "https",
        }
        tokens = []
        for token in re.split(r"[^a-z0-9]+", value):
            token = token.strip()
            if not token:
                continue
            if token in stopwords:
                continue
            if len(token) == 1 and not token.isdigit():
                continue
            tokens.append(token)
        return tokens

    @staticmethod
    def _score_source_srt_candidate(reference_text, candidate_path):
        reference_tokens = App._normalize_srt_reference_tokens(reference_text)
        candidate_tokens = App._normalize_srt_reference_tokens(os.path.basename(candidate_path))
        if not reference_tokens or not candidate_tokens:
            return 0

        reference_set = set(reference_tokens)
        candidate_set = set(candidate_tokens)
        shared = reference_set & candidate_set
        if not shared:
            return 0

        score = 0
        for token in shared:
            score += 1 if token.isdigit() else 3

        if reference_set.issubset(candidate_set):
            score += 8
        elif candidate_set.issubset(reference_set):
            score += 4

        return score

    @staticmethod
    def _is_generated_review_srt_path(path):
        name = os.path.basename(str(path or "")).lower()
        return (
            name.endswith("_review_vi.srt")
            or name.endswith("_review.srt")
            or name.endswith("_review_script.txt")
            or name.endswith("_review.txt")
        )

    def _invalidate_review_package_fields(self):
        if hasattr(self, "review_script_box"):
            self._set_textbox_text(self.review_script_box, "")
        if hasattr(self, "review_srt_box"):
            self._set_textbox_text(self.review_srt_box, "")

    def _thread_safe_log(self, message):
        if not hasattr(self, "after"):
            self._append_log(message)
            return
        try:
            self.after(0, lambda msg=message: self._append_log(msg))
        except Exception:
            self._append_log(message)

    def _set_gemini_login_ui(self, text, color="#94a3b8", running=None):
        """Update Gemini login widgets without touching destroyed Tk widgets."""
        if running is not None:
            self._gemini_login_running = bool(running)
        try:
            if hasattr(self, "gemini_web_status_label") and self.gemini_web_status_label.winfo_exists():
                self.gemini_web_status_label.configure(text=text, text_color=color)
            if hasattr(self, "gemini_web_login_btn") and self.gemini_web_login_btn.winfo_exists():
                self.gemini_web_login_btn.configure(
                    state="disabled" if self._gemini_login_running else "normal",
                    text="ĐANG CHỜ ĐĂNG NHẬP..." if self._gemini_login_running else "ĐĂNG NHẬP GEMINI WEB",
                )
        except Exception:
            pass

    def _refresh_gemini_web_login_status(self):
        try:
            from core.gemini_web import is_profile_ready
            if is_profile_ready():
                self._set_gemini_login_ui("Đã lưu phiên đăng nhập", "#22c55e")
            else:
                self._set_gemini_login_ui("Chưa đăng nhập", "#f59e0b")
        except Exception:
            self._set_gemini_login_ui("Chưa kiểm tra được", "#f59e0b")

    def login_gemini_web(self):
        """Open a visible Gemini session and persist login for later hidden calls."""
        if getattr(self, "_gemini_login_running", False):
            return
        self._set_gemini_login_ui("Đang mở Chrome...", "#38bdf8", running=True)
        self._thread_safe_log("🌐 Mở Gemini Web để đăng nhập (không gửi prompt)...\n")
        threading.Thread(target=self._gemini_web_login_worker, daemon=True).start()

    def _gemini_web_login_worker(self):
        driver = None
        try:
            from core.gemini_web import create_auto_driver, wait_for_gemini_login

            # A stale/headless shared session can lock the persistent profile.
            AIEngine.close_web_driver()
            driver = create_auto_driver(log=self._thread_safe_log, force_visible=True)
            timeout = int(os.environ.get("AUTORECAP_GEMINI_LOGIN_TIMEOUT", "300") or 300)
            ready = wait_for_gemini_login(
                driver,
                timeout=timeout,
                log=self._thread_safe_log,
            )
            if ready:
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                self.after(
                    0,
                    lambda: self._set_gemini_login_ui(
                        "Đăng nhập thành công", "#22c55e", running=False
                    ),
                )
                self._thread_safe_log("✅ Gemini Web đã sẵn sàng; các lần gọi sau sẽ dùng phiên đã lưu.\n")
            else:
                self.after(
                    0,
                    lambda: self._set_gemini_login_ui(
                        "Chưa xác nhận, bấm lại để kiểm tra", "#f59e0b", running=False
                    ),
                )
        except Exception as exc:
            self._thread_safe_log(f"❌ Đăng nhập Gemini Web lỗi: {exc}\n")
            self.after(
                0,
                lambda err=str(exc): self._set_gemini_login_ui(
                    f"Lỗi: {err[:55]}", "#ef4444", running=False
                ),
            )
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _get_gemini_keys(self):
        keys = []
        keys_file = self.api_keys_file.get().strip() if hasattr(self, "api_keys_file") else ""
        if keys_file and os.path.exists(keys_file):
            try:
                with open(keys_file, "r", encoding="utf-8") as f:
                    for line in f:
                        key = line.strip()
                        if key and not key.startswith("#") and key not in keys:
                            keys.append(key)
            except Exception as e:
                self._thread_safe_log(f"⚠️ Không đọc được file keys Gemini: {e}\n")
        single_key = self.api_key.get().strip() if hasattr(self, "api_key") else ""
        if single_key and single_key not in keys:
            keys.append(single_key)
        openrouter_key = self.openrouter_api_key.get().strip() if hasattr(self, "openrouter_api_key") else ""
        if openrouter_key:
            os.environ["OPENROUTER_API_KEY"] = openrouter_key
            os.environ.setdefault("AUTORECAP_TEXT_API_ENABLED", "0")
            os.environ.setdefault(
                "OPENROUTER_VISION_MODELS",
                ",".join([
                    "qwen/qwen2.5-vl-72b-instruct:free",
                    "qwen/qwen2.5-vl-32b-instruct:free",
                    "meta-llama/llama-3.2-11b-vision-instruct:free",
                    "mistralai/mistral-small-3.1-24b-instruct:free",
                    "google/gemma-3-27b-it:free",
                ]),
            )
            if openrouter_key not in keys:
                keys.append(openrouter_key)
        return keys

    def _has_gemini_key(self):
        return bool(self._get_gemini_keys())

    def create_entry(self, label_text, placeholder, show=None):
        frame = ctk.CTkFrame(self.container, fg_color="#16213e", corner_radius=6)
        frame.pack(fill="x", pady=2)
        ctk.CTkLabel(frame, text=label_text, width=120, anchor="w",
                     font=("Segoe UI", 11), text_color="#94a3b8").pack(side="left", padx=(10, 0))
        entry_kwargs = {"placeholder_text": placeholder, "fg_color": "#0f172a",
                        "border_color": "#334155", "border_width": 1}
        if show:
            entry_kwargs["show"] = show
        entry = ctk.CTkEntry(frame, **entry_kwargs)
        entry.pack(side="left", fill="x", expand=True, padx=8, pady=3)
        entry.bind("<KeyRelease>", lambda e: self.update_preview_delayed())
        return entry

    def load_video_preview(self):
        """Load first frame from video và resize canvas theo tỷ lệ video."""
        print(f"[PREVIEW] load_video_preview called, path={self.current_video_path!r}")
        try:
            if not self.current_video_path:
                self.after(0, lambda: self.preview_info.configure(text="❌ Video không tồn tại", text_color="red"))
                return

            video_path = os.path.normpath(os.path.abspath(self.current_video_path))
            if not os.path.exists(video_path):
                self.after(0, lambda: self.preview_info.configure(text=f"❌ Video không tồn tại: {video_path}", text_color="red"))
                return

            pil_frame = None
            cap = cv2.VideoCapture(video_path)
            try:
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25
                    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    # Thử lấy frame ở các mốc thời gian — tránh màn hình đen đầu video
                    seek_seconds = [3, 8, 15, 30, 1, 0]
                    for sec in seek_seconds:
                        target = int(sec * fps)
                        if total_frames > 0 and target >= total_frames:
                            continue
                        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            continue
                        # Kiểm tra frame có tối quá không (mean < 15 = đen)
                        mean_brightness = frame.mean()
                        if mean_brightness < 15 and sec != seek_seconds[-1]:
                            continue  # bỏ qua frame đen, thử tiếp
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        pil_frame = Image.fromarray(frame_rgb)
                        print(f"[PREVIEW] Dùng frame @{sec}s, brightness={mean_brightness:.1f}")
                        break
            finally:
                cap.release()

            if pil_frame is None:
                ffmpeg_exe = FFmpegUtils.ffmpeg_executable()
                if not ffmpeg_exe:
                    raise FileNotFoundError("OpenCV không đọc được video và ffmpeg.exe không có trong PATH")
                # Thử các mốc thời gian để tránh frame đen
                for seek_sec in [5, 10, 3, 1, 0]:
                    cmd = [
                        ffmpeg_exe, '-hide_banner', '-loglevel', 'error',
                        '-ss', str(seek_sec),
                        '-i', video_path,
                        '-vframes', '1',
                        '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'
                    ]
                    proc = subprocess.Popen(
                        cmd,
                        **FFmpegUtils.subprocess_kwargs(
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE
                        ),
                    )
                    frame_bytes, _ = proc.communicate()
                    if frame_bytes:
                        candidate = Image.open(io.BytesIO(frame_bytes)).convert('RGB')
                        import numpy as np
                        if np.array(candidate).mean() > 15 or seek_sec == 0:
                            pil_frame = candidate
                            print(f"[PREVIEW] ffmpeg frame @{seek_sec}s")
                            break
                if pil_frame is None:
                    raise RuntimeError("Không thể đọc frame từ video")

            self.preview_source_size = pil_frame.size
            orig_w, orig_h = pil_frame.size

            # Fit vào canvas 440px wide (right_frame 460 - padx 8*2 = 444)
            PREVIEW_MAX_W = 440
            PREVIEW_MAX_H = 500
            scale = min(PREVIEW_MAX_W / orig_w, PREVIEW_MAX_H / orig_h)
            self.preview_scale = scale
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)

            frame_resized = pil_frame.resize((new_w, new_h), Image.LANCZOS)
            _canvas_img = frame_resized.copy()
            _total_w    = new_w
            _total_h    = new_h

            self.preview_frame_box = (0, 0, new_w, new_h)
            self.header_x = new_w // 2
            self.header_y = max(20, int(new_h * 0.10))
            self.footer_x = new_w // 2
            self.footer_y = min(new_h - 20, int(new_h * 0.88))

            print(f"[PREVIEW] frame ready: {new_w}x{new_h} (orig {orig_w}x{orig_h}, scale={scale:.3f})")

            def _apply_preview():
                try:
                    self.preview_image = _canvas_img
                    self.preview_canvas.configure(width=_total_w, height=_total_h)
                    self.update_idletasks()
                    # Cập nhật text thanh tiêu đề ngoài
                    self._sync_title_bars()
                    self.update_preview()
                    self.preview_info.configure(
                        text=f"✓ {orig_w}×{orig_h} | 📍 Chỉnh text tiêu đề ở ô bên trái",
                        text_color="green"
                    )
                    print(f"[PREVIEW] OK — canvas {_total_w}x{_total_h}")
                except Exception as _e:
                    import traceback
                    print(f"[PREVIEW apply error] {traceback.format_exc()}")
                    try:
                        self.preview_info.configure(text=f"❌ {_e}", text_color="red")
                    except Exception:
                        pass

            # Gọi after() an toàn từ thread — bắt RuntimeError nếu mainloop chưa start
            try:
                self.after(0, _apply_preview)
            except RuntimeError:
                # mainloop chưa ready — thử lại sau 1s
                import threading as _th
                import time as _tm
                def _retry():
                    _tm.sleep(1.0)
                    try:
                        self.after(0, _apply_preview)
                    except Exception:
                        pass
                _th.Thread(target=_retry, daemon=True).start()

        except Exception as e:
            import traceback
            print(f"[PREVIEW load error] {traceback.format_exc()}")
            err = str(e)
            try:
                self.after(0, lambda err=err: self.preview_info.configure(
                    text=f"❌ Lỗi load video: {err}", text_color="red"
                ))
            except RuntimeError:
                pass  # mainloop chưa ready — bỏ qua, không crash

    def on_header_font_change(self, value):
        """Callback when header font size slider changes"""
        self.header_font_size = int(value)
        self.header_font_label.configure(text=str(self.header_font_size))
        self.update_preview_delayed()
    
    def on_footer_font_change(self, value):
        """Callback when footer font size slider changes"""
        self.footer_font_size = int(value)
        self.footer_font_label.configure(text=str(self.footer_font_size))
        self.update_preview_delayed()
    
    def pick_header_color(self):
        """Open color picker for header text"""
        color = colorchooser.askcolor(
            color=self.header_color,
            title="Chọn màu cho tiêu đề trên"
        )
        if color[1]:  # If user selected a color
            # Convert hex to RGB
            hex_color = color[1]
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            self.header_color = (r, g, b)
            self.header_color_btn.configure(fg_color=hex_color)
            self.update_preview_delayed()
    
    def pick_footer_color(self):
        """Open color picker for footer text"""
        color = colorchooser.askcolor(
            color=self.footer_color,
            title="Chọn màu cho tiêu đề dưới"
        )
        if color[1]:  # If user selected a color
            # Convert hex to RGB
            hex_color = color[1]
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            self.footer_color = (r, g, b)
            self.footer_color_btn.configure(fg_color=hex_color)
            self.update_preview_delayed()

    def pick_header_bar_color(self):
        color = colorchooser.askcolor(
            color=self.header_bar_color,
            title="Chọn màu thanh trên"
        )
        if color[1]:
            hex_color = color[1]
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            self.header_bar_color = (r, g, b)
            self.header_bar_color_btn.configure(fg_color=hex_color)
            self.update_preview_delayed()

    def pick_footer_bar_color(self):
        color = colorchooser.askcolor(
            color=self.footer_bar_color,
            title="Chọn màu thanh dưới"
        )
        if color[1]:
            hex_color = color[1]
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            self.footer_bar_color = (r, g, b)
            self.footer_bar_color_btn.configure(fg_color=hex_color)
            self.update_preview_delayed()

    def get_voice_options(self, language_label):
        if language_label == "English":
            return ["en-US-AriaNeural", "en-US-GuyNeural", "en-GB-LibbyNeural"]
        # Danh sách giọng Việt: edge-tts + Piper offline
        base = [
            "Review nữ - vi-VN-HoaiMyNeural",
            "Lồng tiếng nam - vi-VN-NamMinhNeural",
        ]
        # Thêm Piper offline nếu model có sẵn
        try:
            from engine.piper_tts import list_piper_voices, VOICE_NAMES
            piper_voices = list_piper_voices()
            if piper_voices:
                for pv in piper_voices:
                    # pv = "piper:ngoc_huyen" → hiển thị "🎙 Ngọc Huyền (Offline)"
                    stem = pv.replace("piper:", "")
                    pretty = VOICE_NAMES.get(stem, stem.replace("_", " ").title())
                    label = f"🎙 {pretty} (Offline) - {pv}"
                    base.append(label)
        except Exception:
            pass
        return base

    def _is_voice_id(self, text):
        return bool(re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+Neural", (text or "").strip()))

    def _is_vietnamese_language(self, language_label):
        if not language_label:
            return False
        label = language_label.strip().lower()
        return "tiếng việt" in label or label.startswith("vi") or label == "vietnamese"

    def _resolve_voice_id(self, voice_label, language_label=None):
        voice_label = (voice_label or "").strip()
        if self._is_voice_id(voice_label):
            return voice_label
        # Nhận dạng Piper offline: "🎙 Ngọc Huyền (Offline) - piper:ngoc_huyen"
        try:
            from engine.piper_tts import is_piper_voice
            # Tìm "piper:xxx" trong label
            import re as _re
            piper_match = _re.search(r"(piper:[a-z_]+)", voice_label)
            if piper_match:
                return piper_match.group(1)
            if is_piper_voice(voice_label):
                return voice_label
        except Exception:
            pass
        match = re.search(r"([a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+Neural)", voice_label)
        if match:
            return match.group(1)
        if self._is_vietnamese_language(language_label):
            return "vi-VN-HoaiMyNeural"
        return "en-US-JennyNeural"

    def _voice_id(self, voice_label):
        return self._resolve_voice_id(voice_label, getattr(self, 'tts_language', None) and self.tts_language.get())

    # ── Nghe thử giọng đọc ────────────────────────────────────────────────────
    def _preview_voice_sample(self):
        """Tổng hợp câu mẫu bằng giọng đang chọn và phát ngay."""
        import threading
        voice_label = self.voice_choice.get() if hasattr(self, "voice_choice") else ""
        lang_label  = self.tts_language.get() if hasattr(self, "tts_language") else "Tiếng Việt"
        voice_id    = self._resolve_voice_id(voice_label, lang_label)

        # Luôn dùng câu mẫu tiếng Việt để người dùng nghe và chọn giọng
        sample_text = "Xin chào! Đây là giọng đọc mẫu của ứng dụng AutoRecapPro. Giọng này sẽ được dùng để lồng tiếng cho video recap."

        # Disable nút trong lúc phát để tránh spam
        if hasattr(self, "btn_preview_voice"):
            self.btn_preview_voice.configure(state="disabled", text="⏳ Đang tạo...")

        def _do_preview():
            import tempfile, os
            tmp_path = os.path.join(tempfile.gettempdir(), "_autorecap_preview.wav")
            tmp_mp3  = os.path.join(tempfile.gettempdir(), "_autorecap_preview.mp3")
            ok = False
            err_msg = ""
            try:
                from engine.piper_tts import is_piper_voice, synthesize_piper
                if is_piper_voice(voice_id):
                    # Piper offline → WAV
                    synthesize_piper(sample_text, tmp_path, voice=voice_id, speed=1.0)
                    play_path = tmp_path
                else:
                    # edge-tts → MP3, dùng new_event_loop để tránh conflict
                    import asyncio
                    loop = asyncio.new_event_loop()
                    try:
                        async def _tts():
                            import edge_tts as _edge
                            comm = _edge.Communicate(text=sample_text, voice=voice_id)
                            await comm.save(tmp_mp3)
                        loop.run_until_complete(_tts())
                    finally:
                        loop.close()
                    play_path = tmp_mp3

                # Phát audio bằng ffplay (hỗ trợ cả WAV lẫn MP3)
                import sys, subprocess
                if os.path.exists(play_path) and os.path.getsize(play_path) > 0:
                    if sys.platform == "win32":
                        played = False
                        # WAV → thử winsound trước (nhanh, không cần ffplay)
                        if play_path.endswith(".wav"):
                            try:
                                import winsound
                                winsound.PlaySound(play_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                                played = True
                                ok = True
                            except Exception:
                                pass
                        # Fallback → ffplay (xử lý cả MP3)
                        if not played:
                            ffplay = self._get_ffplay()
                            if ffplay:
                                try:
                                    subprocess.run(
                                        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", play_path],
                                        timeout=60,
                                        startupinfo=self._subprocess_startupinfo(),
                                    )
                                    ok = True
                                except Exception as fe:
                                    err_msg = f"ffplay lỗi: {fe}"
                            else:
                                err_msg = "Không tìm thấy ffplay để phát MP3"
                    else:
                        subprocess.run(["aplay", play_path], timeout=60)
                        ok = True
                else:
                    err_msg = "File audio rỗng hoặc không tạo được"
            except Exception as e:
                err_msg = str(e)
            finally:
                def _restore():
                    if hasattr(self, "btn_preview_voice"):
                        self.btn_preview_voice.configure(state="normal", text="🎧 Nghe thử")
                    if not ok and err_msg:
                        self._show_error(f"Nghe thử thất bại: {err_msg}")
                self.after(0, _restore)

        threading.Thread(target=_do_preview, daemon=True).start()

    def _get_ffplay(self):
        """Lấy đường dẫn ffplay (bundled hoặc system)."""
        from utils.helpers import FFmpegUtils
        base = FFmpegUtils.ffmpeg_executable()
        if base:
            import os
            ffplay = os.path.join(os.path.dirname(base), "ffplay.exe")
            if os.path.exists(ffplay):
                return ffplay
        import shutil
        return shutil.which("ffplay") or shutil.which("ffplay.exe")

    @staticmethod
    def _subprocess_startupinfo():
        import subprocess, sys
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return si
        return None

    def _show_error(self, msg):
        """Hiển thị popup lỗi nhỏ."""
        try:
            import customtkinter as ctk
            top = ctk.CTkToplevel(self)
            top.title("Lỗi")
            top.geometry("420x120")
            top.grab_set()
            ctk.CTkLabel(top, text=msg, wraplength=390).pack(pady=20, padx=10)
            ctk.CTkButton(top, text="Đóng", command=top.destroy, width=80).pack()
        except Exception:
            print(f"[ERROR] {msg}")

    def on_tts_language_change(self, value):
        if not hasattr(self, "voice_choice"):
            return
        voices = self.get_voice_options(value)
        current = self.voice_choice.get() if hasattr(self, "voice_choice") else ""
        self.voice_choice.configure(values=voices)
        if current not in voices:
            self.voice_choice.set(voices[0])

    def on_cut_mode_change(self, value):
        preset = getattr(self, "cut_mode_presets", {}).get(value)
        if preset is None:
            return
        keep_seconds, skip_seconds = preset
        if hasattr(self, "keep_val") and hasattr(self, "skip_val"):
            self.keep_val.delete(0, "end")
            self.keep_val.insert(0, str(keep_seconds))
            self.skip_val.delete(0, "end")
            self.skip_val.insert(0, str(skip_seconds))

    def _mark_cut_mode_custom(self, event=None):
        if hasattr(self, "cut_mode") and self.cut_mode.get() != "Tùy chỉnh":
            self.cut_mode.set("Tùy chỉnh")

    def _get_cut_settings(self):
        mode = self.cut_mode.get() if hasattr(self, "cut_mode") else "Tùy chỉnh"
        preset = getattr(self, "cut_mode_presets", {}).get(mode)
        if preset is not None:
            return preset[0], preset[1], mode

        def _int_from_entry(entry, default):
            try:
                v = entry.get().strip()
                parsed = int(v) if v != "" else default
                return max(0, parsed)
            except Exception:
                return default

        keep_seconds = _int_from_entry(self.keep_val, 5)
        skip_seconds = _int_from_entry(self.skip_val, 8)
        if keep_seconds <= 0 and skip_seconds <= 0:
            keep_seconds, skip_seconds = 1, 0
        return keep_seconds, skip_seconds, mode

    def _get_preview_duration_settings(self):
        custom_value = ""
        if hasattr(self, "custom_review_minutes"):
            try:
                custom_value = self.custom_review_minutes.get().strip().replace(",", ".")
            except Exception:
                custom_value = ""

        if custom_value:
            try:
                minutes = float(custom_value)
                if not math.isfinite(minutes) or minutes <= 0:
                    raise ValueError("invalid review budget")
                source_seconds = self._get_source_video_duration()
                if source_seconds and source_seconds > 0:
                    minutes = min(minutes, source_seconds / 60.0)
                minutes = max(1.0, minutes)
                label = f"{minutes:g} phút (tùy chỉnh)"
                return label, minutes, minutes * 60.0
            except Exception:
                try:
                    self.custom_review_minutes.delete(0, "end")
                except Exception:
                    pass

        label = self.video_duration.get() if hasattr(self, "video_duration") else "20 phút"
        label = (label or "20 phút").strip()
        try:
            minutes = float(label.split()[0])
        except Exception:
            minutes = 20.0
            label = "20 phút"
        allowed_minutes = {5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 45.0, 60.0}
        if minutes not in allowed_minutes:
            minutes = min(60.0, max(5.0, minutes))
            if minutes not in allowed_minutes:
                minutes = 20.0
            label = f"{int(minutes)} phút"
            try:
                self.video_duration.set(label)
            except Exception:
                pass
        seconds = minutes * 60.0
        return label, minutes, seconds

    def _on_review_budget_preset_selected(self, _selected=None):
        if hasattr(self, "custom_review_minutes"):
            try:
                self.custom_review_minutes.delete(0, "end")
            except Exception:
                pass

    def _set_review_budget_from_source(self):
        source_seconds = self._get_source_video_duration()
        if not source_seconds or source_seconds <= 0:
            messagebox.showwarning(
                "Chưa đọc được thời lượng",
                "Hãy chọn video gốc hợp lệ trước khi dùng tùy chọn này.",
                parent=self,
            )
            return
        minutes = math.ceil((source_seconds / 60.0) * 10.0) / 10.0
        value = f"{minutes:.1f}".rstrip("0").rstrip(".")
        self.custom_review_minutes.delete(0, "end")
        self.custom_review_minutes.insert(0, value)
        messagebox.showinfo(
            "Đã lấy thời lượng video gốc",
            f"Ngân sách review được đặt thành {value} phút. AI sẽ kể rất chi tiết và phủ toàn bộ câu chuyện.",
            parent=self,
        )

    def _show_review_budget_help(self):
        messagebox.showinfo(
            "Ngân sách review là gì?",
            "Ngân sách review là thời lượng LỜI THUYẾT MINH mà AI dự kiến viết, "
            "không phải thời lượng video gốc và không phải lệnh cắt mất phần cuối.\n\n"
            "AI sẽ chia ngân sách cho các chapter và vẫn phủ đủ mở đầu, diễn biến chính, "
            "cao trào và kết thúc. Video render đi theo voice thực tế.\n\n"
            "Ví dụ video gốc 40 phút:\n"
            "- Chọn 10 phút: recap cô đọng, nhanh.\n"
            "- Chọn 20 phút: review chi tiết hơn.\n"
            "- Tự nhập 25 phút: dùng đúng ngân sách riêng bạn muốn.\n"
            "- Bằng video gốc: tự đọc thời lượng phim và tạo bản kể rất chi tiết.\n\n"
            "Khuyến nghị video YouTube review: thường chọn 10-20 phút cho phim dài 40-60 phút.",
            parent=self,
        )

    def _is_smart_cut_mode(self, cut_mode=None):
        value = cut_mode if cut_mode is not None else (self.cut_mode.get() if hasattr(self, "cut_mode") else "")
        normalized = unicodedata.normalize("NFD", str(value).lower())
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return "thong minh" in normalized or "smart" in normalized

    def _get_overlay_positions(self):
        """Trả về header_pos và footer_pos cho video_engine render.
        
        Trong video_engine, tiêu đề vẽ vào vùng padding (350px đen chia đôi trên/dưới).
        Default: header ở 12% (vùng padding trên), footer ở 88% (vùng padding dưới).
        """
        # Nếu user đã kéo trên preview → header_y/footer_y là tỷ lệ 0.0-1.0
        # Nếu chưa kéo → dùng default
        hy = getattr(self, "header_y", 0.12)
        fy = getattr(self, "footer_y", 0.88)

        # Nếu header_y > 1 (pixel cũ) → convert sang tỷ lệ
        if isinstance(hy, (int, float)) and hy > 1.5:
            # Giá trị pixel → chuyển về 0.12 mặc định
            hy = 0.12
        if isinstance(fy, (int, float)) and fy > 1.5:
            fy = 0.88

        # Clamp vào vùng padding an toàn — tiêu đề không đè lên video
        # video_engine pad 350px → video bắt đầu từ ~15%, kết thúc ~85%
        # Header nên <= 13%, Footer nên >= 87%
        hy = max(0.02, min(0.14, float(hy)))
        fy = max(0.86, min(0.98, float(fy)))

        return (
            {"x": 0.5, "y": hy},
            {"x": 0.5, "y": fy},
        )

    def get_text_anchor(self, align_str):
        """Convert alignment string to PIL anchor."""
        align_map = {
            "Trái": "lm",
            "Giữa": "mm",
            "Phải": "rm",
        }
        return align_map.get(align_str, "mm")


    def on_canvas_press(self, event):
        """Mouse press — detect kéo thanh header hay footer."""
        bar_h = getattr(self, "_preview_bar_h", 38)
        hy = getattr(self, "_bar_header_y", bar_h // 2)
        fy = getattr(self, "_bar_footer_y", 300)
        # Click trong vùng bar (±bar_h//2)
        if abs(event.y - hy) <= bar_h // 2 + 5:
            self.dragging = "header"
        elif abs(event.y - fy) <= bar_h // 2 + 5:
            self.dragging = "footer"
        else:
            self.dragging = None

    def on_canvas_drag(self, event):
        """Mouse drag — cập nhật vị trí thanh tiêu đề."""
        total_h = getattr(self, "_preview_total_h", 300)
        bar_h   = getattr(self, "_preview_bar_h", 38)
        if self.dragging == "header":
            self._bar_header_y = max(bar_h // 2, min(total_h - bar_h // 2, event.y))
            self.update_preview()
        elif self.dragging == "footer":
            self._bar_footer_y = max(bar_h // 2, min(total_h - bar_h // 2, event.y))
            self.update_preview()

    def on_canvas_release(self, event):
        """Mouse release — lưu vị trí kéo thành tỷ lệ cho video_engine."""
        self.dragging = None
        vid_h    = getattr(self, "_preview_vid_h",    220)
        bar_h    = getattr(self, "_preview_bar_h",     38)
        total_h  = getattr(self, "_preview_total_h",  296)

        hy = getattr(self, "_bar_header_y", bar_h // 2)
        fy = getattr(self, "_bar_footer_y", bar_h + vid_h + bar_h // 2)

        # Map vị trí kéo sang tỷ lệ 0.0-1.0 trong tổng chiều cao canvas
        # rồi clamp vào vùng padding an toàn
        if total_h > 0:
            hy_ratio = hy / total_h
            fy_ratio = fy / total_h
        else:
            hy_ratio = 0.12
            fy_ratio = 0.88

        # Clamp: header <= 0.14 (vùng padding trên), footer >= 0.86 (vùng padding dưới)
        self.header_y = max(0.02, min(0.14, hy_ratio))
        self.footer_y = max(0.86, min(0.98, fy_ratio))

    def update_preview_delayed(self, *args):
        """Debounced preview update"""
        if hasattr(self, '_preview_timer'):
            self.after_cancel(self._preview_timer)
        self._preview_timer = self.after(300, self._update_all_preview)

    def _update_all_preview(self):
        """Cập nhật cả thanh tiêu đề ngoài lẫn canvas."""
        self._sync_title_bars()
        self.update_preview()

    def _sync_title_bars(self):
        """Đồng bộ text và màu thanh tiêu đề nằm ngoài video."""
        try:
            header_txt = self.header_text.get().strip() if hasattr(self, 'header_text') else ""
            footer_txt = self.footer_text.get().strip() if hasattr(self, 'footer_text') else ""
            if not header_txt:
                header_txt = "TIÊU ĐỀ TRÊN"
            if not footer_txt:
                footer_txt = "TIÊU ĐỀ DƯỚI"

            # Màu bar
            hbar_r, hbar_g, hbar_b = getattr(self, 'header_bar_color', (220, 20, 20))
            fbar_r, fbar_g, fbar_b = getattr(self, 'footer_bar_color', (0, 180, 216))
            hbar_hex = f"#{hbar_r:02x}{hbar_g:02x}{hbar_b:02x}"
            fbar_hex = f"#{fbar_r:02x}{fbar_g:02x}{fbar_b:02x}"

            # Màu text
            htxt_r, htxt_g, htxt_b = getattr(self, 'header_color', (255, 255, 255))
            ftxt_r, ftxt_g, ftxt_b = getattr(self, 'footer_color', (255, 255, 255))
            htxt_hex = f"#{htxt_r:02x}{htxt_g:02x}{htxt_b:02x}"
            ftxt_hex = f"#{ftxt_r:02x}{ftxt_g:02x}{ftxt_b:02x}"

            # Căn chỉnh
            h_align = getattr(self, 'header_align', None)
            f_align = getattr(self, 'footer_align', None)
            anchor_map = {"Trái": "w", "Giữa": "center", "Phải": "e"}
            h_anchor = anchor_map.get(h_align.get() if h_align else "Giữa", "center")
            f_anchor = anchor_map.get(f_align.get() if f_align else "Giữa", "center")

            if hasattr(self, '_header_bar_preview'):
                self._header_bar_preview.configure(fg_color=hbar_hex)
            if hasattr(self, '_header_lbl_preview'):
                h_lines, h_size = VideoEngine._prepare_title_layout(
                    header_txt, getattr(self, 'header_font_size', 80),
                    min_font_size=20, frame_width=1920, max_lines=2,
                    auto_fit=True,
                )
                base_fs = max(5, int(round(h_size * 0.23)))
                self._header_lbl_preview.configure(
                    text="\n".join(h_lines), text_color=htxt_hex,
                    font=("Arial", base_fs, "bold"), anchor=h_anchor,
                    wraplength=0,
                )
            if hasattr(self, '_footer_bar_preview'):
                self._footer_bar_preview.configure(fg_color=fbar_hex)
            if hasattr(self, '_footer_lbl_preview'):
                f_lines, f_size = VideoEngine._prepare_title_layout(
                    footer_txt, getattr(self, 'footer_font_size', 60),
                    min_font_size=20, frame_width=1920, max_lines=2,
                    auto_fit=True,
                )
                base_fs = max(5, int(round(f_size * 0.23)))
                self._footer_lbl_preview.configure(
                    text="\n".join(f_lines), text_color=ftxt_hex,
                    font=("Arial", base_fs, "bold"), anchor=f_anchor,
                    wraplength=0,
                )
        except Exception:
            pass

    def update_preview(self):
        """Hiển thị frame video + 2 thanh tiêu đề lên canvas — có thể kéo thả."""
        if self.preview_image is None:
            # Vẽ placeholder với 2 thanh tiêu đề mẫu trên nền đen
            self._draw_preview_placeholder()
            return
        try:
            orig_w, orig_h = self.preview_image.size
            # Canvas width cố định 440, tính video height
            canvas_w = 440
            scale    = canvas_w / orig_w
            vid_h    = int(orig_h * scale)

            # Chiều cao mỗi thanh tiêu đề
            # FFmpeg adds a 200px title band at 1920px reference width.
            # Scale that real source band into the preview canvas.
            BAR_H = max(30, int(round(200 * (orig_w / 1920.0) * scale)))

            # Tổng canvas height = bar_trên + video + bar_dưới
            total_h = BAR_H + vid_h + BAR_H

            # Resize video cho khớp canvas_w
            vid_img = self.preview_image.resize((canvas_w, vid_h), Image.LANCZOS)

            # Tạo composite image
            composite = Image.new("RGB", (canvas_w, total_h), (0, 0, 0))
            composite.paste(vid_img, (0, BAR_H))

            draw = ImageDraw.Draw(composite)

            # Màu thanh
            hbar_color = getattr(self, "header_bar_color", (255, 0, 0))
            fbar_color = getattr(self, "footer_bar_color",  (0, 180, 216))
            htxt_color = getattr(self, "header_color",      (255, 255, 0))
            ftxt_color = getattr(self, "footer_color",      (255, 255, 255))

            header_txt = (self.header_text.get() if hasattr(self, "header_text") else "") or "TIÊU ĐỀ TRÊN"
            footer_txt = (self.footer_text.get() if hasattr(self, "footer_text") else "") or "TIÊU ĐỀ DƯỚI"

            # Dung cung layout voi FFmpeg: font co san de doc va title dai
            # duoc xuong toi da 2 dong thay vi ep thanh mot dong chu li ti.
            h_lines, h_render_fs = VideoEngine._prepare_title_layout(
                header_txt, getattr(self, "header_font_size", 80),
                min_font_size=20, frame_width=orig_w, max_lines=2,
                auto_fit=True,
            )
            f_lines, f_render_fs = VideoEngine._prepare_title_layout(
                footer_txt, getattr(self, "footer_font_size", 60),
                min_font_size=20, frame_width=orig_w, max_lines=2,
                auto_fit=True,
            )
            header_preview_text = "\n".join(h_lines)
            footer_preview_text = "\n".join(f_lines)
            h_fs = max(8, int(round(h_render_fs * scale)))
            f_fs = max(8, int(round(f_render_fs * scale)))

            try:
                h_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", h_fs)
            except Exception:
                h_font = ImageFont.load_default()
            try:
                f_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", f_fs)
            except Exception:
                f_font = ImageFont.load_default()

            # Vẽ thanh tiêu đề TRÊN (y = 0 → BAR_H)
            # header_y trong range 0..BAR_H+vid_h — mặc định giữa bar trên
            # Giữ header_y để user kéo được (init = BAR_H//2)
            if not hasattr(self, "_bar_header_y") or self._bar_header_y is None:
                self._bar_header_y = BAR_H // 2
            if not hasattr(self, "_bar_footer_y") or self._bar_footer_y is None:
                self._bar_footer_y = BAR_H + vid_h + BAR_H // 2

            # Clamp vị trí
            hbar_y = max(0, min(BAR_H + vid_h, int(self._bar_header_y)))
            fbar_y = max(0, min(total_h,        int(self._bar_footer_y)))

            # Vẽ bar header
            draw.rectangle([0, hbar_y - BAR_H//2, canvas_w, hbar_y + BAR_H//2], fill=hbar_color)
            # Text header căn giữa bar
            tbbox = draw.multiline_textbbox((0, 0), header_preview_text, font=h_font, spacing=2, align="center")
            tw = tbbox[2] - tbbox[0]
            tx = (canvas_w - tw) // 2
            ty = hbar_y - (tbbox[3] - tbbox[1]) // 2
            draw.multiline_text(
                (tx, ty), header_preview_text, fill=htxt_color,
                font=h_font, spacing=2, align="center",
            )

            # Vẽ bar footer
            draw.rectangle([0, fbar_y - BAR_H//2, canvas_w, fbar_y + BAR_H//2], fill=fbar_color)
            tbbox2 = draw.multiline_textbbox((0, 0), footer_preview_text, font=f_font, spacing=2, align="center")
            tw2 = tbbox2[2] - tbbox2[0]
            tx2 = (canvas_w - tw2) // 2
            ty2 = fbar_y - (tbbox2[3] - tbbox2[1]) // 2
            draw.multiline_text(
                (tx2, ty2), footer_preview_text, fill=ftxt_color,
                font=f_font, spacing=2, align="center",
            )

            # Highlight khi kéo (viền vàng)
            if getattr(self, "dragging", None) == "header":
                draw.rectangle([0, hbar_y - BAR_H//2, canvas_w, hbar_y + BAR_H//2],
                                outline=(255, 220, 0), width=2)
            elif getattr(self, "dragging", None) == "footer":
                draw.rectangle([0, fbar_y - BAR_H//2, canvas_w, fbar_y + BAR_H//2],
                                outline=(255, 220, 0), width=2)

            # Cập nhật canvas
            photo = ImageTk.PhotoImage(composite)
            self.preview_canvas.configure(width=canvas_w, height=total_h)
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(0, 0, image=photo, anchor="nw")
            self.preview_canvas.image = photo

            # Lưu để drag dùng
            self._preview_bar_h  = BAR_H
            self._preview_vid_h  = vid_h
            self._preview_total_h = total_h
            self._preview_canvas_w = canvas_w

            # Sync lại header_y/footer_y để drag đúng
            self.header_x = canvas_w // 2
            self.header_y = hbar_y
            self.footer_x = canvas_w // 2
            self.footer_y = fbar_y

            overflow = []
            if len(h_lines) > 2:
                overflow.append("tiêu đề trên quá dài, hãy rút ngắn nội dung")
            if len(f_lines) > 2:
                overflow.append("tiêu đề dưới quá dài, hãy rút ngắn nội dung")

            self.preview_info.configure(
                text="📍 Kéo thanh tiêu đề lên/xuống để chỉnh vị trí",
                text_color="green"
            )
            if overflow:
                self.preview_info.configure(
                    text="CẢNH BÁO: " + " | ".join(overflow),
                    text_color="#ef4444",
                )
        except Exception as e:
            import traceback
            print(f"[update_preview error] {traceback.format_exc()}")
            self.preview_info.configure(text=f"Lỗi preview: {e}", text_color="red")

    def _draw_preview_placeholder(self):
        """Vẽ placeholder khi chưa có video."""
        try:
            BAR_H   = 38
            VID_H   = 220
            W       = 440
            total_h = BAR_H + VID_H + BAR_H

            img  = Image.new("RGB", (W, total_h), (15, 15, 20))
            draw = ImageDraw.Draw(img)

            hbar = getattr(self, "header_bar_color", (220, 20,  20))
            fbar = getattr(self, "footer_bar_color",  (0,  180, 216))
            htxt = getattr(self, "header_color",      (255, 255, 0))
            ftxt = getattr(self, "footer_color",      (255, 255, 255))

            header_txt = (self.header_text.get() if hasattr(self, "header_text") else "") or "TIÊU ĐỀ TRÊN"
            footer_txt = (self.footer_text.get() if hasattr(self, "footer_text") else "") or "TIÊU ĐỀ DƯỚI"

            # Gia lap khung render rong 1920px khi chua co video. Khong auto-fit:
            # tieu de qua dai se bi cat giong ket qua render that.
            scale = W / 1920.0
            h_lines, h_render_fs = VideoEngine._prepare_title_layout(
                header_txt, getattr(self, "header_font_size", 80),
                min_font_size=20, frame_width=1920, max_lines=2,
                auto_fit=True,
            )
            f_lines, f_render_fs = VideoEngine._prepare_title_layout(
                footer_txt, getattr(self, "footer_font_size", 60),
                min_font_size=20, frame_width=1920, max_lines=2,
                auto_fit=True,
            )
            header_preview_text = "\n".join(h_lines)
            footer_preview_text = "\n".join(f_lines)
            try:
                h_font = ImageFont.truetype(
                    "C:/Windows/Fonts/arial.ttf",
                    max(8, int(round(h_render_fs * scale))),
                )
            except Exception:
                h_font = ImageFont.load_default()
            try:
                f_font = ImageFont.truetype(
                    "C:/Windows/Fonts/arial.ttf",
                    max(8, int(round(f_render_fs * scale))),
                )
            except Exception:
                f_font = ImageFont.load_default()

            draw.rectangle([0, 0, W, BAR_H], fill=hbar)
            bb = draw.multiline_textbbox((0,0), header_preview_text, font=h_font, spacing=2, align="center")
            header_w = bb[2] - bb[0]
            draw.multiline_text(
                ((W-header_w)//2, (BAR_H-(bb[3]-bb[1]))//2),
                header_preview_text, fill=htxt, font=h_font, spacing=2, align="center",
            )

            draw.rectangle([0, BAR_H, W, BAR_H+VID_H], fill=(20, 20, 25))
            draw.text((W//2-80, BAR_H+VID_H//2-10), "Chọn video để xem preview", fill=(80,80,90))

            draw.rectangle([0, BAR_H+VID_H, W, total_h], fill=fbar)
            bb2 = draw.multiline_textbbox((0,0), footer_preview_text, font=f_font, spacing=2, align="center")
            footer_w = bb2[2] - bb2[0]
            draw.multiline_text(
                ((W-footer_w)//2, BAR_H+VID_H+(BAR_H-(bb2[3]-bb2[1]))//2),
                footer_preview_text, fill=ftxt, font=f_font, spacing=2, align="center",
            )

            photo = ImageTk.PhotoImage(img)
            self.preview_canvas.configure(width=W, height=total_h)
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(0, 0, image=photo, anchor="nw")
            self.preview_canvas.image = photo

            self._bar_header_y = BAR_H // 2
            self._bar_footer_y = BAR_H + VID_H + BAR_H // 2

            overflow = []
            if len(h_lines) > 2:
                overflow.append("tiêu đề trên quá dài, hãy rút ngắn nội dung")
            if len(f_lines) > 2:
                overflow.append("tiêu đề dưới quá dài, hãy rút ngắn nội dung")
            if hasattr(self, "preview_info"):
                if overflow:
                    self.preview_info.configure(
                        text="CẢNH BÁO: " + " | ".join(overflow),
                        text_color="#ef4444",
                    )
                else:
                    self.preview_info.configure(
                        text="Chọn video để xem preview chính xác",
                        text_color="green",
                    )
        except Exception:
            pass

    def auto_gen_title(self):
        def _gen_title_thread():
            try:
                if not self.movie_name.get().strip():
                    self.log.insert("end", "❌ Lỗi: Chưa nhập tên phim\n")
                    return
                    
                self.log.insert("end", "⏳ Đang gọi AI để tạo tiêu đề...\n")
                self.log.see("end")
                
                self.save_config({
                    'gemini_api_key': self.api_key.get(),
                    'gemini_keys_file': self.api_keys_file.get(),
                    'openrouter_api_key': self.openrouter_api_key.get() if hasattr(self, "openrouter_api_key") else '',
                    'review_style': self._selected_review_style_key(),
                })
                ai = AIEngine(self._get_gemini_keys())
                h, f = ai.generate_hooks(self.movie_name.get())

                def _apply_generated_titles():
                    self.header_text.delete(0, "end")
                    self.header_text.insert(0, h)
                    self.footer_text.delete(0, "end")
                    self.footer_text.insert(0, f)
                    self.log.insert("end", "✅ Đã có tiêu đề hay!\n")
                    self.log.see("end")
                    self._update_all_preview()

                # Gemini chay o worker; moi thay doi Tk phai quay ve UI thread.
                self.after(0, _apply_generated_titles)
            except Exception as e:
                error_message = str(e)
                self.after(0, lambda: (
                    self.log.insert("end", f"❌ Lỗi AI: {error_message}\n"),
                    self.log.see("end"),
                ))
        
        threading.Thread(target=_gen_title_thread, daemon=True).start()

    def _make_script_review_callback(self):
        """Tạo callback để hiện Script Editor sau AI_FULL.

        Pipeline gọi callback này, truyền chính nó làm tham số.
        Callback chạy trên pipeline thread → cần dùng threading.Event để chờ
        UI thread mở cửa sổ và user xác nhận.
        """
        import threading as _threading

        app_ref = self  # reference tới App instance

        def callback(pipeline) -> bool:
            """Chạy trên worker thread — phải chờ main thread."""
            event = _threading.Event()
            result_holder = [False]  # chỉ tiếp tục khi user bấm xác nhận rõ ràng

            def _open_editor():
                """Chạy trên main (Tk) thread."""
                try:
                    from ui.script_editor import ScriptEditorWindow

                    def on_confirm():
                        result_holder[0] = True
                        event.set()

                    def on_cancel():
                        result_holder[0] = False
                        event.set()

                    n = len((pipeline.ai_package or {}).get("script_blocks") or [])
                    pipeline._log(f"\n✏️  Script Editor: {n} blocks sẵn sàng để chỉnh sửa\n")

                    editor = ScriptEditorWindow(
                        parent=app_ref,
                        pipeline=pipeline,
                        on_confirm=on_confirm,
                        on_cancel=on_cancel,
                    )
                    # Editor không modal: có thể thu nhỏ để dùng app khác rồi mở lại.
                except Exception as e:
                    result_holder[0] = False
                    pipeline._log(f"   ⚠️ Không mở được Script Editor: {e} → dừng, không tạo voice\n")
                    event.set()

            # Lên lịch mở editor trên main thread
            app_ref.after(0, _open_editor)

            # Chờ user xác nhận. Mặc định không timeout để pipeline không tự hủy
            # khi người dùng còn đang chỉnh trong Script Editor.
            timeout_raw = str(os.environ.get("AUTORECAP_SCRIPT_EDITOR_TIMEOUT", "0") or "0").strip()
            try:
                timeout_seconds = float(timeout_raw)
            except Exception:
                timeout_seconds = 0.0
            if timeout_seconds > 0:
                completed = event.wait(timeout=timeout_seconds)
            else:
                completed = event.wait()
            if not completed:
                result_holder[0] = False
                try:
                    pipeline._log("   ⚠️ Script Editor timeout: chưa xác nhận nên dừng pipeline, không tạo voice/render\n")
                except Exception:
                    pass
            return result_holder[0]

        return callback

    def start_thread(self):
        """Luôn chạy Full Pipeline (workflow đã được tích hợp vào pipeline)."""
        if not hasattr(self, "video_path"):
            threading.Thread(target=self.run_advanced_workflow, daemon=True).start()
            return
        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path or not os.path.exists(video_path):
            self.log.insert("end", "❌ Vui lòng chọn video gốc trước.\n")
            self.log.see("end")
            return
        output_dir = self._ensure_video_output_dir(video_path, force_new=False, update_entries=True)
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(video_path), "full_pipeline_output")

        self.log.insert("end", "🚀 Full Pipeline: METADATA → TRANSCRIPT → SCENE_DETECT → SUBTITLE_MAP → KEYFRAMES → AI_FULL → CLIP_FIND → VOICE_SEGMENTS → VOICE_CONCAT → VOICE_SRT → RENDER_FINAL\n")
        self.log.see("end")

        def reset_labels():
            for lbl in self._fp_step_labels.values():
                lbl.configure(text="⏳", text_color="gray")
        self.after(0, reset_labels)

        threading.Thread(
            target=self._full_pipeline_worker,
            args=(video_path, output_dir),
            daemon=True,
        ).start()

    def _resume_pipeline(self):
        """Tiếp tục pipeline từ bước bị lỗi — bỏ qua các bước đã có output."""
        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path or not os.path.exists(video_path):
            self.log.insert("end", "❌ Vui lòng chọn video gốc trước khi resume.\n")
            self.log.see("end")
            return
        # Dùng output_dir hiện tại (không tạo mới) → pipeline tự skip bước đã xong
        output_dir = self._ensure_video_output_dir(video_path, force_new=False, update_entries=False)
        if not output_dir:
            self.log.insert("end", "❌ Chưa có output_dir — hãy chạy pipeline lần đầu trước.\n")
            self.log.see("end")
            return
        self.log.insert("end", "♻️  Resume pipeline — bỏ qua bước đã xong, chạy lại bước lỗi...\n")
        self.log.see("end")
        threading.Thread(
            target=self._full_pipeline_worker,
            args=(video_path, output_dir),
            daemon=True,
        ).start()

    def _open_script_editor_manual(self):
        """Mở Script Editor thủ công — load kịch bản từ output_dir hiện tại."""
        active_editor = getattr(self, "_active_script_editor", None)
        if active_editor is not None:
            try:
                if active_editor.winfo_exists() and active_editor.restore_editor():
                    self._thread_safe_log("✏️ Đã mở lại Script Editor đang chỉnh sửa.\n")
                    return
            except Exception:
                self._active_script_editor = None

        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path:
            self.log.insert("end", "❌ Vui lòng chọn video gốc trước.\n")
            self.log.see("end")
            return
        output_dir = self._ensure_video_output_dir(video_path, force_new=False, update_entries=False)
        ai_pkg_path = os.path.join(output_dir, "ai_package.json") if output_dir else ""

        if not ai_pkg_path or not os.path.exists(ai_pkg_path):
            self.log.insert("end", "❌ Chưa có kịch bản — chạy pipeline đến bước AI_FULL trước.\n")
            self.log.see("end")
            return

        try:
            import json
            # Tạo pipeline object từ output_dir đã có (không chạy lại từ đầu)
            gemini_keys = self._get_gemini_keys()
            api_key = "\n".join(gemini_keys) if gemini_keys else ""
            voice_id = self._voice_id(self.voice_choice.get()) if hasattr(self, "voice_choice") else "vi-VN-HoaiMyNeural"

            pipeline = FullPipeline(
                video_path=video_path,
                output_dir=output_dir,
                movie_title=self.movie_name.get().strip() if hasattr(self, "movie_name") else "",
                gemini_api_key=api_key,
                voice=voice_id,
                progress_callback=lambda msg: self._thread_safe_log(f"{msg}\n"),
            )

            # Load lại state từ file đã có
            with open(ai_pkg_path, encoding="utf-8") as f:
                ai_pkg = json.load(f)
            pipeline.ai_package = ai_pkg
            pipeline.render_blocks = ai_pkg.get("render_blocks") or []

            # Load render_blocks.json nếu có
            rb_path = os.path.join(output_dir, "render_blocks.json")
            if os.path.exists(rb_path):
                with open(rb_path, encoding="utf-8") as f:
                    pipeline.render_blocks = json.load(f)

            # Load metadata để editor có video info
            meta_path = os.path.join(output_dir, "metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    pipeline.metadata = json.load(f)

            n = len(pipeline.render_blocks)
            self.log.insert("end", f"✏️  Mở Script Editor: {n} blocks...\n")
            self.log.see("end")

            import threading as _th
            import time as _t

            confirmed_event = _th.Event()
            result_holder = [False]

            def on_confirm():
                result_holder[0] = True
                confirmed_event.set()

            def on_cancel():
                result_holder[0] = False
                confirmed_event.set()

            def _open_editor():
                from ui.script_editor import ScriptEditorWindow
                editor = ScriptEditorWindow(
                    parent=self,
                    pipeline=pipeline,
                    on_confirm=on_confirm,
                    on_cancel=on_cancel,
                )
                editor.focus_set()

            self.after(0, _open_editor)

            def _wait_and_notify():
                # Không timeout khi editor đang ẩn/thu nhỏ; chỉ kết thúc khi
                # người dùng bấm Xác nhận hoặc Hủy.
                confirmed_event.wait()
                if result_holder[0]:
                    # Lưu lại ai_package đã chỉnh
                    try:
                        updated_pkg = pipeline.ai_package or ai_pkg
                        with open(ai_pkg_path, "w", encoding="utf-8") as f:
                            json.dump(updated_pkg, f, ensure_ascii=False, indent=2)
                        n2 = len((updated_pkg.get("script_blocks") or []))
                        self._thread_safe_log(f"✅ Đã lưu kịch bản ({n2} blocks).\n")
                        self._thread_safe_log("💡 Bấm '▶️ TIẾP TỤC TỪ BƯỚC LỖI' để tạo voice và render.\n")
                        # Xóa voice cache để pipeline chạy lại từ VOICE_SEGMENTS
                        _del = [
                            os.path.join(output_dir, "voice_segments"),
                            os.path.join(output_dir, "voice_concat.mp3"),
                            os.path.join(output_dir, "voice.srt"),
                        ]
                        for p in _del:
                            try:
                                if os.path.isdir(p):
                                    import shutil; shutil.rmtree(p, ignore_errors=True)
                                elif os.path.exists(p):
                                    os.unlink(p)
                            except Exception:
                                pass
                        self._thread_safe_log("🗑️  Đã xóa voice cache → resume sẽ chạy lại từ VOICE_SEGMENTS.\n")
                    except Exception as e:
                        self._thread_safe_log(f"❌ Lưu kịch bản lỗi: {e}\n")

            _th.Thread(target=_wait_and_notify, daemon=True).start()

        except Exception as e:
            self.log.insert("end", f"❌ Mở Script Editor lỗi: {e}\n")
            self.log.see("end")

    def start_cut_video_thread(self):
        thread = threading.Thread(target=self._cut_video_worker, daemon=True)
        thread.start()

    def _cut_video_worker(self):
        try:
            video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
            if not video_path or not os.path.exists(video_path):
                self._thread_safe_log("❌ Vui lòng chọn video gốc hợp lệ để cắt.")
                return

            output_dir = self.cut_output_dir.get().strip() if hasattr(self, "cut_output_dir") else ""
            output_dir = output_dir or self._ensure_video_output_dir(video_path, force_new=False, update_entries=True)
            output_dir = output_dir or (self.output_dir.get().strip() if hasattr(self, "output_dir") else "")
            if not output_dir:
                self._thread_safe_log("❌ Chưa chọn thư mục lưu video băm.")
                return

            os.makedirs(output_dir, exist_ok=True)
            keep_seconds, skip_seconds, cut_mode = self._get_cut_settings()
            preview_label, preview_minutes, max_duration = self._get_preview_duration_settings()

            base_name = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(output_dir, f"{base_name}_cut.mp4")

            def disable_button():
                self.btn_cut_video.configure(state="disabled", text="ĐANG TẠO PREVIEW...")
            self.after(0, disable_button)

            self._thread_safe_log(f"✂️ Bắt đầu tạo preview video băm: {cut_mode}\n")
            if self._is_smart_cut_mode(cut_mode):
                self._thread_safe_log("🧠 Smart Cut: phân tích chuyển cảnh, motion và mặt nhân vật nổi bật...\n")
                success, actual_duration, error_msg, smart_segments = VideoCutter.cut_video_smart(
                    video_path,
                    output_path,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    max_duration_seconds=max_duration,
                    progress_callback=lambda msg: self._thread_safe_log(f"   {msg}\n"),
                    return_segments=True,
                )
                if success:
                    self._thread_safe_log(f"✓ Smart Cut đã chọn {len(smart_segments)} phân đoạn hay\n")
            else:
                success, actual_duration, error_msg = VideoCutter.cut_video_with_pattern(
                    video_path,
                    output_path,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    max_duration_seconds=max_duration,
                )

            def update_ui():
                self.btn_cut_video.configure(state="normal", text="TẠO PREVIEW VIDEO BĂM")
                if success:
                    self._thread_safe_log(f"✅ Video băm đã được lưu: {output_path} ({round(actual_duration, 1)}s)\n")
                else:
                    self._thread_safe_log(f"❌ Lỗi khi cắt video: {error_msg}\n")

            self.after(0, update_ui)
        except Exception as e:
            def fail_ui():
                if hasattr(self, "btn_cut_video"):
                    self.btn_cut_video.configure(state="normal", text="TẠO PREVIEW VIDEO BĂM")
                self._thread_safe_log(f"❌ Lỗi video băm: {str(e)}\n")
            self.after(0, fail_ui)

    def _get_cut_output_video_path(self, video_path):
        if not video_path:
            return ""
        output_dir = self.cut_output_dir.get().strip() if hasattr(self, "cut_output_dir") else ""
        if not output_dir:
            output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        if not output_dir:
            output_dir = os.path.dirname(video_path)
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        return os.path.join(output_dir, f"{base_name}_cut.mp4")

    def _get_capcut_srt_output_path(self, cut_video_path):
        if not cut_video_path:
            return ""
        output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        if not output_dir:
            output_dir = os.path.dirname(cut_video_path)
        if not output_dir:
            return ""
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(cut_video_path))[0]
        return os.path.join(output_dir, f"{base_name}_capcut.srt")

    def _video_file_signature(self, video_path):
        if not video_path or not os.path.exists(video_path):
            return {}
        try:
            return {
                "path": os.path.normcase(os.path.abspath(video_path)),
                "size": int(os.path.getsize(video_path)),
                "mtime": round(float(os.path.getmtime(video_path)), 3),
            }
        except Exception:
            return {"path": os.path.normcase(os.path.abspath(video_path))}

    @staticmethod
    def _capcut_srt_meta_path(srt_path):
        return f"{srt_path}.meta.json" if srt_path else ""

    def _capcut_srt_matches_video(self, srt_path, video_path):
        if not srt_path or not video_path or not os.path.exists(srt_path):
            return False
        meta_path = self._capcut_srt_meta_path(srt_path)
        if not meta_path or not os.path.exists(meta_path):
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            expected = self._video_file_signature(video_path)
            actual = meta.get("video") or {}
            if not expected or not actual:
                return False
            if os.path.normcase(str(actual.get("path") or "")) != os.path.normcase(str(expected.get("path") or "")):
                return False
            if actual.get("size") is not None and expected.get("size") is not None:
                if int(actual.get("size") or 0) != int(expected.get("size") or 0):
                    return False
            if actual.get("mtime") is not None and expected.get("mtime") is not None:
                if abs(float(actual.get("mtime") or 0.0) - float(expected.get("mtime") or 0.0)) > 2.0:
                    return False
            return True
        except Exception:
            return False

    def _write_capcut_srt_meta(self, srt_path, video_path):
        if not srt_path or not video_path:
            return
        try:
            meta_path = self._capcut_srt_meta_path(srt_path)
            data = {
                "video": self._video_file_signature(video_path),
                "srt": {
                    "path": os.path.normcase(os.path.abspath(srt_path)),
                    "size": int(os.path.getsize(srt_path)) if os.path.exists(srt_path) else 0,
                    "mtime": round(float(os.path.getmtime(srt_path)), 3) if os.path.exists(srt_path) else 0,
                },
                "created_by": "AutoRecapPro_V2",
            }
            with open(meta_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _delete_capcut_srt_cache(self, srt_path):
        if not srt_path:
            return
        candidates = [srt_path, self._capcut_srt_meta_path(srt_path)]
        try:
            translated = self._translated_srt_output_path(srt_path)
            candidates.extend([translated, self._capcut_srt_meta_path(translated)])
        except Exception:
            pass
        for candidate in candidates:
            if not candidate:
                continue
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except Exception:
                pass

    @staticmethod
    def _is_video_file_path(path):
        return os.path.splitext(path or "")[1].lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

    @staticmethod
    def _same_path(path_a, path_b):
        if not path_a or not path_b:
            return False
        try:
            return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))
        except Exception:
            return False

    def _is_cut_video_output_path(self, path):
        if not path or not self._is_video_file_path(path):
            return False

        candidates = [
            getattr(self, "cut_video_output_path", ""),
        ]
        source_video = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if source_video:
            candidates.append(self._get_cut_output_video_path(source_video))
        output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        if output_dir:
            candidates.append(os.path.join(output_dir, "video_cut_temp.mp4"))

        for candidate in candidates:
            if self._same_path(path, candidate):
                return True

        stem = os.path.splitext(os.path.basename(path))[0].lower()
        return stem.endswith("_cut") or stem == "video_cut_temp" or stem.endswith("_cut_temp")

    def _find_existing_cut_video_path(self):
        candidates = []
        if hasattr(self, "srt_gen_video"):
            candidates.append(self.srt_gen_video.get().strip())
        candidates.append(getattr(self, "cut_video_output_path", ""))

        source_video = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if source_video:
            candidates.append(self._get_cut_output_video_path(source_video))

        output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        if output_dir:
            candidates.append(os.path.join(output_dir, "video_cut_temp.mp4"))

        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            try:
                normalized = os.path.normcase(os.path.abspath(candidate))
            except Exception:
                normalized = candidate
            if normalized in seen:
                continue
            seen.add(normalized)
            if os.path.exists(candidate) and self._is_cut_video_output_path(candidate):
                self.cut_video_output_path = candidate
                self.capcut_srt_output_path = self._get_capcut_srt_output_path(candidate)
                return candidate
        return ""

    @staticmethod
    def _is_valid_srt_path(path):
        if not path or not os.path.exists(path):
            return False
        try:
            return bool(SRTParser.parse_srt(path))
        except Exception:
            return False

    def _find_existing_srt_for_cut_video(self, cut_video_path):
        candidates = [
            getattr(self, "capcut_srt_output_path", ""),
            self._get_capcut_srt_output_path(cut_video_path),
            self._get_source_srt_path(),
            self.srt_path.get().strip() if hasattr(self, "srt_path") else "",
        ]
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            try:
                normalized = os.path.normcase(os.path.abspath(candidate))
            except Exception:
                normalized = candidate
            if normalized in seen:
                continue
            seen.add(normalized)
            if self._is_valid_srt_path(candidate):
                lower = os.path.basename(candidate).lower()
                if lower.endswith("_capcut.srt") and cut_video_path:
                    if not self._capcut_srt_matches_video(candidate, cut_video_path):
                        continue
                return candidate
        return ""

    def _translated_srt_output_path(self, source_srt_path):
        return SRTTranslator.default_output_path(source_srt_path)

    def _mirror_translated_srt_to_output_dir(self, translated_srt_path):
        """Copy translated SRT into the selected app output folder."""
        if not translated_srt_path or not os.path.exists(translated_srt_path):
            return translated_srt_path
        try:
            if not SRTTranslator.is_translated_srt_path(translated_srt_path):
                return translated_srt_path
        except Exception:
            pass
        try:
            output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        except Exception:
            output_dir = ""
        if not output_dir:
            return translated_srt_path
        try:
            os.makedirs(output_dir, exist_ok=True)
            src_abs = os.path.abspath(translated_srt_path)
            dst_abs = os.path.abspath(os.path.join(output_dir, os.path.basename(translated_srt_path)))
            if os.path.normcase(src_abs) == os.path.normcase(dst_abs):
                return translated_srt_path
            shutil.copy2(src_abs, dst_abs)
            self._thread_safe_log(f"   💾 Copy SRT dịch vào thư mục lưu: {dst_abs}\n")
            return dst_abs
        except Exception as exc:
            self._thread_safe_log(f"   ⚠️ Không copy được SRT dịch vào thư mục lưu: {exc}\n")
            return translated_srt_path

    def _save_translated_srt_from_text(self, response_text: str, source_srt_path: str, output_path: str) -> str:
        """Parse text response từ Gemini Web thành file SRT hợp lệ và lưu.

        Gemini có thể trả về:
        - SRT thuần (1\\n00:00:01,000 --> ...\\ntext\\n...)
        - JSON có field "srt" hoặc text trong markdown code block
        - Plain text cần wrap thành SRT

        Trả về đường dẫn file đã lưu, hoặc "" nếu thất bại.
        """
        import re as _re, json as _json
        text = (response_text or "").strip()

        # 1. Thử extract từ markdown code block ```srt ... ``` hoặc ``` ... ```
        code_match = _re.search(r'```(?:srt)?\s*\n([\s\S]+?)\n```', text, _re.IGNORECASE)
        if code_match:
            text = code_match.group(1).strip()

        # 2. Thử parse JSON nếu response là JSON object
        if text.startswith('{') or text.startswith('['):
            try:
                data = _json.loads(text)
                if isinstance(data, dict):
                    for key in ('srt', 'content', 'result', 'translation', 'subtitle'):
                        if key in data and isinstance(data[key], str):
                            text = data[key].strip()
                            break
            except Exception:
                pass

        # 3. Kiểm tra text có dạng SRT hợp lệ không
        has_timestamp = bool(_re.search(r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->', text))
        has_index = bool(_re.search(r'^\d+\s*$', text, _re.MULTILINE))

        if not has_timestamp:
            # Response không phải SRT → không thể lưu
            return ""

        # 4. Chuẩn hóa: đảm bảo dòng trắng giữa các entry
        text = _re.sub(r'\r\n', '\n', text)
        text = _re.sub(r'\r', '\n', text)
        # Thêm dòng trắng trước số thứ tự nếu chưa có
        text = _re.sub(r'\n(\d+)\n(\d{2}:\d{2})', r'\n\n\1\n\2', text)
        text = text.strip()

        # 5. Lưu file
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text + '\n')
            # Verify lại file vừa lưu
            try:
                saved_subs = SRTParser.parse_srt(output_path)
                if saved_subs and len(saved_subs) >= 3:
                    self._thread_safe_log(f"   💾 Lưu SRT {len(saved_subs)} dòng → {os.path.basename(output_path)}\n")
                    return self._mirror_translated_srt_to_output_dir(output_path)
                else:
                    self._thread_safe_log(f"   ⚠️ SRT lưu được nhưng chỉ {len(saved_subs)} dòng, cần kiểm tra.\n")
                    return self._mirror_translated_srt_to_output_dir(output_path) if saved_subs else ""
            except Exception:
                return self._mirror_translated_srt_to_output_dir(output_path)  # file đã lưu, bất kể parse được không
        except Exception as e:
            self._thread_safe_log(f"   ❌ Lưu SRT thất bại: {e}\n")
            return ""

    def _set_source_srt_path_threadsafe(self, srt_path, auto_detected=True):
        if not srt_path:
            return
        try:
            self.after(0, lambda path=srt_path: self._set_source_srt_path(path, auto_detected=auto_detected))
        except Exception:
            self.source_srt_path = srt_path

    def _ensure_vietnamese_source_srt(self, source_srt_path, force=False):
        """Translate CapCut/source SRT to Vietnamese and make it the active source SRT."""
        if not source_srt_path or not os.path.exists(source_srt_path):
            return ""
        if not self._is_valid_srt_path(source_srt_path):
            return source_srt_path

        if SRTTranslator.is_translated_srt_path(source_srt_path) and not force:
            try:
                translated_subtitles = SRTParser.parse_srt(source_srt_path)
                if self._srt_text_has_enough_vietnamese_marks(translated_subtitles):
                    source_srt_path = self._mirror_translated_srt_to_output_dir(source_srt_path)
                    self._set_source_srt_path_threadsafe(source_srt_path, auto_detected=True)
                    return source_srt_path
                self._thread_safe_log("⚠️ File _translated.srt hiện chưa phải tiếng Việt đủ dấu; sẽ dịch lại bằng Gemini Web.\n")
            except Exception:
                self._thread_safe_log("⚠️ Không đọc được file _translated.srt; sẽ dịch lại bằng Gemini Web.\n")

        output_path = self._translated_srt_output_path(source_srt_path)
        if output_path and self._is_valid_srt_path(output_path):
            try:
                cached_subtitles = SRTParser.parse_srt(output_path)
                if (
                    os.path.getmtime(output_path) >= os.path.getmtime(source_srt_path)
                    and self._srt_text_has_enough_vietnamese_marks(cached_subtitles)
                ):
                    self._thread_safe_log(f"🇻🇳 Dùng lại SRT đã dịch tiếng Việt: {output_path}\n")
                    output_path = self._mirror_translated_srt_to_output_dir(output_path)
                    self._set_source_srt_path_threadsafe(output_path, auto_detected=True)
                    return output_path
                if cached_subtitles and not self._srt_text_has_enough_vietnamese_marks(cached_subtitles):
                    self._thread_safe_log("⚠️ SRT dịch cache thiếu dấu tiếng Việt; bỏ cache và dịch lại.\n")
            except Exception:
                pass

        try:
            self._thread_safe_log("Gemini Web: dich SRT, khong dung API key text...\n")
            prompt, output_path = self._build_ai_studio_srt_translation_prompt(source_srt_path)
            if not prompt or not output_path:
                self._thread_safe_log("Khong build duoc prompt dich SRT Web; tam dung SRT goc.\n")
                self._set_source_srt_path_threadsafe(source_srt_path, auto_detected=True)
                return source_srt_path

            translated_now = self._copy_srt_translation_prompt_and_open_ai_studio(
                prompt,
                source_srt_path,
                output_path,
                auto_continue_review=False,
                run_async=False,
            )
            if translated_now and os.path.exists(str(translated_now)) and self._is_valid_srt_path(str(translated_now)):
                self._set_source_srt_path_threadsafe(str(translated_now), auto_detected=True)
                return str(translated_now)
            if not translated_now:
                self._set_source_srt_path_threadsafe(source_srt_path, auto_detected=True)
                return source_srt_path

            self._thread_safe_log("? Ch? b?n copy k?t qu? SRT d?ch t? Gemini Web v? app...\n")
            deadline = time.time() + int(os.environ.get("AUTORECAP_SRT_WEB_WAIT_SECONDS", "900") or "900")
            while time.time() < deadline:
                time.sleep(2)
                candidates = []
                if output_path:
                    candidates.append(output_path)
                    try:
                        out_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
                        if out_dir:
                            candidates.append(os.path.join(out_dir, os.path.basename(output_path)))
                    except Exception:
                        pass
                try:
                    current = self._get_source_srt_path()
                    if current:
                        candidates.append(current)
                except Exception:
                    pass

                for candidate in candidates:
                    if not candidate or not os.path.exists(candidate) or not self._is_valid_srt_path(candidate):
                        continue
                    try:
                        subtitles = SRTParser.parse_srt(candidate)
                        if self._srt_text_has_enough_vietnamese_marks(subtitles):
                            translated_path = self._mirror_translated_srt_to_output_dir(candidate)
                            self._set_source_srt_path_threadsafe(translated_path, auto_detected=True)
                            self._thread_safe_log(f"? SRT ti?ng Vi?t ?? s?n s?ng: {translated_path}\n")
                            return translated_path
                    except Exception:
                        pass

            self._thread_safe_log("? H?t th?i gian ch? SRT d?ch Gemini Web; t?m d?ng SRT g?c.\n")
            self._set_source_srt_path_threadsafe(source_srt_path, auto_detected=True)
            return source_srt_path
        except Exception as exc:
            self._thread_safe_log(f"?? D?ch SRT b?ng Gemini Web th?t b?i: {exc}. T?m d?ng SRT g?c.\n")
            self._set_source_srt_path_threadsafe(source_srt_path, auto_detected=True)
            return source_srt_path

    def _sync_cut_related_paths(self, video_path=None):
        source_video = (video_path or (self.video_path.get().strip() if hasattr(self, "video_path") else "")).strip()
        if not source_video or not os.path.exists(source_video):
            return

        if self._is_cut_video_output_path(source_video):
            cut_video_path = source_video
        else:
            cut_video_path = self._get_cut_output_video_path(source_video)
        self.cut_video_output_path = cut_video_path
        self.capcut_srt_output_path = self._get_capcut_srt_output_path(cut_video_path)

        # SRT phải lấy từ video GỐC (chưa băm) để giữ timestamp gốc.
        if hasattr(self, "srt_gen_video"):
            self.srt_gen_video.delete(0, "end")
            self.srt_gen_video.insert(0, source_video)

    def _prepare_cut_video_for_capcut(self, video_path):
        if not video_path or not os.path.exists(video_path):
            return ""

        if self._is_cut_video_output_path(video_path):
            self.cut_video_output_path = video_path
            self.capcut_srt_output_path = self._get_capcut_srt_output_path(video_path)
            self._thread_safe_log(f"🧩 Dùng video băm đã có cho CapCut: {video_path}\n")
            return video_path

        cut_video_path = self._get_cut_output_video_path(video_path)
        cut_mode = self.cut_mode.get() if hasattr(self, "cut_mode") else ""
        try:
            if (
                not self._is_smart_cut_mode(cut_mode)
                and os.path.exists(cut_video_path)
                and os.path.getmtime(cut_video_path) >= os.path.getmtime(video_path)
            ):
                self.cut_video_output_path = cut_video_path
                self.capcut_srt_output_path = self._get_capcut_srt_output_path(cut_video_path)
                return cut_video_path
        except Exception:
            pass

        keep_seconds, skip_seconds, _ = self._get_cut_settings()
        preview_label, preview_minutes, max_duration = self._get_preview_duration_settings()

        self._thread_safe_log("✂️ Tạo preview video băm để CapCut theo phân cảnh/nhịp review...\n")
        if self._is_smart_cut_mode(cut_mode):
            self._thread_safe_log("🧠 Smart Cut cho CapCut: ưu tiên phân cảnh hay và nhân vật chính...\n")
            success, actual_duration, error_msg, _segments = VideoCutter.cut_video_smart(
                video_path,
                cut_video_path,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                max_duration_seconds=max_duration,
                progress_callback=lambda msg: self._thread_safe_log(f"   {msg}\n"),
                return_segments=True,
            )
        else:
            success, actual_duration, error_msg = VideoCutter.cut_video_with_pattern(
                video_path,
                cut_video_path,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                max_duration_seconds=max_duration,
            )
        if not success:
            self._thread_safe_log(f"❌ Không tạo được video băm: {error_msg}\n")
            return ""

        self._thread_safe_log(f"✅ Video băm đã chuẩn bị: {cut_video_path} ({round(actual_duration,1)}s)\n")
        self.cut_video_output_path = cut_video_path
        self.capcut_srt_output_path = self._get_capcut_srt_output_path(cut_video_path)
        return cut_video_path

    def _generate_source_srt_for_ai(self, preferred_video_path=None, force_for_video=False):
        source_path = "" if force_for_video else self._ensure_source_srt(prompt_if_missing=False)
        if source_path:
            return self._ensure_vietnamese_source_srt(source_path) or source_path

        source_video_path = (preferred_video_path or "").strip()
        if not source_video_path:
            source_video_path = self.srt_gen_video.get().strip() if hasattr(self, "srt_gen_video") else ""
        if not source_video_path:
            source_video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""

        original_video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if source_video_path and self._is_cut_video_output_path(source_video_path) and original_video_path:
            self._thread_safe_log("[CAPCUT] Phat hien video bam -> doi ve video goc de lay SRT dung timestamp.\n")
            source_video_path = original_video_path

        if not source_video_path or not os.path.exists(source_video_path):
            self._thread_safe_log("❌ Chưa có video hợp lệ để tự tạo SRT nguồn.\n")
            return ""

        # Source SRT for AI must keep the ORIGINAL video's timestamps. The
        # recap pipeline maps those original timestamps back into the cut
        # video later, so auto-cutting here makes the script drift by scene.
        if hasattr(self, "_get_capcut_srt_output_path"):
            srt_output = self._get_capcut_srt_output_path(source_video_path)
        else:
            output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
            output_dir = output_dir or os.path.dirname(source_video_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(source_video_path))[0]
            srt_output = os.path.join(output_dir, f"{base_name}_capcut.srt")
        self.capcut_srt_output_path = srt_output

        valid_existing = False
        if os.path.exists(srt_output):
            try:
                valid_existing = bool(SRTParser.parse_srt(srt_output)) and self._capcut_srt_matches_video(
                    srt_output,
                    source_video_path,
                )
            except Exception:
                valid_existing = False

        if valid_existing:
            self._thread_safe_log(f"[CAPCUT] Dung lai SRT nguon tu CapCut: {srt_output}\n")
        else:
            if os.path.exists(srt_output):
                self._thread_safe_log("[CAPCUT] SRT cache khong khop video hien tai; xoa cache va tao lai.\n")
            delete_cache = getattr(self, "_delete_capcut_srt_cache", None)
            if callable(delete_cache):
                delete_cache(srt_output)
            capcut_exe = CapCutIntegration.find_capcut_executable()
            if not capcut_exe:
                self._thread_safe_log("❌ Không tìm thấy CapCut để lấy SRT nguồn. Hãy mở CapCut hoặc chọn SRT có sẵn.\n")
                return ""

            self._thread_safe_log("🎬 Mở CapCut để lấy SRT nguồn nhanh hơn...\n")

            def progress_callback(message, percentage):
                self._thread_safe_log(f"   {message}\n")

            capcut = CapCutIntegration(progress_callback=progress_callback)
            success = capcut.monitor_and_extract_srt(source_video_path, srt_output)
            if not success:
                self._thread_safe_log("❌ Không lấy được SRT từ CapCut.\n")
                return ""
            write_meta = getattr(self, "_write_capcut_srt_meta", None)
            if callable(write_meta):
                write_meta(srt_output, source_video_path)

        ensure_vi = getattr(self, "_ensure_vietnamese_source_srt", None)
        source_for_ai = ensure_vi(srt_output) if callable(ensure_vi) else srt_output
        source_for_ai = source_for_ai or srt_output
        self.source_srt_path = source_for_ai
        setter = getattr(self, "_set_source_srt_path_threadsafe", None)
        if callable(setter):
            setter(source_for_ai, auto_detected=True)
        elif hasattr(self, "_set_source_srt_path"):
            self._set_source_srt_path(source_for_ai, auto_detected=True)
        return source_for_ai

    def _run_advanced_workflow_with_auto_srt(self):
        self.run_advanced_workflow()

    def _run_review_package_with_auto_srt(self):
        if self._generate_source_srt_for_ai():
            self._generate_review_package_worker()

    def _run_manual_review_prompt_with_auto_srt(self):
        if self._generate_source_srt_for_ai():
            self._manual_review_prompt_worker()

    def _run_web_srt_translate_then_prompt_with_auto_srt(self):
        if self._generate_source_srt_for_ai():
            self._web_srt_translate_then_prompt_worker()

    def _run_revise_review_with_auto_srt(self):
        if self._generate_source_srt_for_ai():
            self._revise_review_with_srt_worker()

    def _extract_voice_script(self, raw_text):
        """Use pasted scripts as the TTS source instead of asking AI to rewrite them shorter."""
        if not raw_text or not raw_text.strip():
            return "", False

        vo_lines = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.search(r"(?:voiceover\s*\(vo\)|voiceover|vo)\s*:\s*(.+)", stripped, flags=re.IGNORECASE)
            if match:
                vo_lines.append(match.group(1).strip())

        if vo_lines:
            return "\n\n".join(vo_lines), True

        words = raw_text.split()
        if len(words) >= 1200:
            cleaned_lines = []
            for line in raw_text.splitlines():
                stripped = line.strip()
                if not stripped:
                    cleaned_lines.append("")
                    continue
                lower = stripped.lower()
                if lower.startswith("(video:") or lower.startswith("video:"):
                    continue
                cleaned_lines.append(stripped)
            return "\n".join(cleaned_lines).strip(), True

        return raw_text.strip(), False

    def _get_review_script_text(self):
        if hasattr(self, "review_script_box"):
            return self._get_textbox_text(self.review_script_box)
        return ""

    def _get_review_srt_text(self):
        if hasattr(self, "review_srt_box"):
            return self._get_textbox_text(self.review_srt_box)
        return ""

    def _estimate_review_target_seconds(self, keep_seconds=3, skip_seconds=10):
        _label, _minutes, seconds = self._get_preview_duration_settings()
        if seconds:
            return int(seconds)

        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if video_path and os.path.exists(video_path):
            try:
                input_seconds = VideoCalculator.get_video_duration(video_path)
                cycle = keep_seconds + skip_seconds if keep_seconds + skip_seconds > 0 else 1
                return input_seconds * (keep_seconds / cycle)
            except Exception:
                return None
        return None

    def _get_source_video_duration(self):
        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path or not os.path.exists(video_path):
            return None
        cached_path = getattr(self, "_source_duration_cache_path", "")
        cached_seconds = getattr(self, "_source_duration_cache_seconds", None)
        if cached_path == video_path and cached_seconds:
            return float(cached_seconds)
        try:
            duration = VideoCalculator.get_video_duration(video_path)
            if duration and duration > 0:
                self._source_duration_cache_path = video_path
                self._source_duration_cache_seconds = float(duration)
            return duration
        except Exception:
            try:
                import cv2
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    cap.release()
                    if frame_count and fps:
                        duration = frame_count / fps
                        self._source_duration_cache_path = video_path
                        self._source_duration_cache_seconds = float(duration)
                        return duration
                cap.release()
            except Exception:
                pass
            return None

    def _build_review_srt(self, subtitle_chunks, target_seconds=None):
        chunks = [str(chunk).strip() for chunk in (subtitle_chunks or []) if str(chunk).strip()]
        if not chunks:
            return ""

        weights = [max(1, len(chunk.split())) for chunk in chunks]
        total_weight = sum(weights) or len(chunks)
        if not target_seconds or target_seconds <= 0:
            target_seconds = max(len(chunks) * 2.0, total_weight / 2.3)

        lines = []
        current = 0.0
        for idx, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            duration = max(1.4, target_seconds * (weight / total_weight))
            start = current
            end = start + duration
            lines.append(
                f"{idx}\n"
                f"{SRTParser.seconds_to_timecode(start)} --> {SRTParser.seconds_to_timecode(end)}\n"
                f"{chunk}\n"
            )
            current = end + 0.05
        return "\n".join(lines).strip()

    def _save_review_package_assets(self, script_text, srt_text):
        out_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
        if not out_dir:
            out_dir = os.getcwd()
        os.makedirs(out_dir, exist_ok=True)

        base = self.movie_name.get().strip() if hasattr(self, "movie_name") else "review"
        base = re.sub(r"[^A-Za-z0-9À-ỹ_-]+", "_", base).strip("_") or "review"
        base = base[:80]

        script_path = os.path.join(out_dir, f"{base}_review_script.txt")
        srt_path = os.path.join(out_dir, f"{base}_review_vi.srt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text.strip() + "\n")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text.strip() + "\n")
        return script_path, srt_path

    def start_review_package_thread(self):
        if self._get_source_srt_path() or self._ensure_source_srt(prompt_if_missing=False):
            target = self._generate_review_package_worker
        else:
            self.log.insert("end", "ℹ️ Chưa có SRT nguồn. AI sẽ mở CapCut để lấy SRT trước khi viết review.\n")
            self.log.see("end")
            target = self._run_review_package_with_auto_srt
        threading.Thread(target=target, daemon=True).start()

    def start_manual_review_prompt_thread(self):
        if self._get_source_srt_path() or self._ensure_source_srt(prompt_if_missing=False):
            target = self._manual_review_prompt_worker
        else:
            self.log.insert("end", "ℹ️ Chưa có SRT nguồn. App sẽ mở CapCut lấy SRT trước khi tạo prompt tay.\n")
            self.log.see("end")
            target = self._run_manual_review_prompt_with_auto_srt
        threading.Thread(target=target, daemon=True).start()

    def start_web_srt_translate_then_prompt_thread(self):
        if self._get_source_srt_path() or self._ensure_source_srt(prompt_if_missing=False):
            target = self._web_srt_translate_then_prompt_worker
        else:
            self.log.insert("end", "ℹ️ Chưa có SRT nguồn. App sẽ mở CapCut lấy SRT trước khi dịch bằng Gemini web.\n")
            self.log.see("end")
            target = self._run_web_srt_translate_then_prompt_with_auto_srt
        threading.Thread(target=target, daemon=True).start()

    def start_revise_review_thread(self):
        if self._get_source_srt_path() or self._ensure_source_srt(prompt_if_missing=False):
            target = self._revise_review_with_srt_worker
        else:
            self.log.insert("end", "ℹ️ Chưa có SRT nguồn. AI sẽ mở CapCut để lấy SRT trước khi sửa review.\n")
            self.log.see("end")
            target = self._run_revise_review_with_auto_srt
        threading.Thread(target=target, daemon=True).start()

    # ── NÚT TỔNG HỢP: TẠO KỊCH BẢN HOÀN CHỈNH ─────────────────────────────
    def start_smart_review_thread(self):
        """Workflow tổng hợp 1 nút:
        1. Kiểm tra SRT (lấy qua CapCut nếu chưa có)
        2. Dịch SRT → mở Gemini web → auto paste → chờ kết quả
        3. Khi nhận SRT dịch → AI Tạo kịch bản + SRT tiếng Việt
        4. AI Sửa kịch bản theo thoại SRT
        5. Copy kết quả về app tự động
        """
        threading.Thread(target=self._smart_review_worker, daemon=True).start()

    def _smart_review_worker(self):
        """Workflow đơn giản:
        B1: Lấy SRT CapCut nguồn
        B2: Mở Gemini Web + auto paste prompt dịch SRT (tay copy về)
        B3: Tạo kịch bản bằng Gemini Web/text fallback (API key chỉ dùng Gemini Vision)
        """
        def _set_btn(text, state="disabled", color=None):
            if hasattr(self, "btn_smart_review"):
                def _do():
                    kw = {"state": state, "text": text}
                    if color:
                        kw["fg_color"] = color
                    self.btn_smart_review.configure(**kw)
                self.after(0, _do)

        _set_btn("⏳ ĐANG XỬ LÝ...", "disabled", "#6b7280")
        try:
            self._thread_safe_log("\n" + "="*55 + "\n")
            self._thread_safe_log("🎬 TẠO KỊCH BẢN HOÀN CHỈNH\n")
            self._thread_safe_log("="*55 + "\n")

            # ── BƯỚC 1: Lấy SRT CapCut nguồn ─────────────────────
            _set_btn("📋 B1: Kiểm tra SRT...", "disabled", "#6b7280")
            source_srt = self._get_source_srt_path()
            if not source_srt:
                self._thread_safe_log("📋 Chưa có SRT → thử CapCut...\n")
                source_srt = self._generate_source_srt_for_ai()
            if not source_srt:
                self._thread_safe_log("❌ Không có SRT nguồn. Tạo SRT CapCut (mục 2) trước.\n")
                return
            self._thread_safe_log(f"✅ SRT nguồn: {os.path.basename(source_srt)}\n")

            # ── BƯỚC 2: Dịch SRT → Gemini Web (tay) ──────────────
            out_srt_path = self._translated_srt_output_path(source_srt)
            need_translate = True

            # Kiểm tra cache
            if out_srt_path and self._is_valid_srt_path(out_srt_path):
                try:
                    cached = SRTParser.parse_srt(out_srt_path)
                    if (os.path.getmtime(out_srt_path) >= os.path.getmtime(source_srt)
                            and self._srt_text_has_enough_vietnamese_marks(cached)):
                        vi_srt = self._mirror_translated_srt_to_output_dir(out_srt_path)
                        self._set_source_srt_path_threadsafe(vi_srt, auto_detected=True)
                        self._thread_safe_log(f"⏭️  B2: Dùng lại SRT đã dịch: {os.path.basename(vi_srt)}\n")
                        need_translate = False
                except Exception:
                    pass

            if need_translate:
                try:
                    src_subs = SRTParser.parse_srt(source_srt)
                    if self._srt_text_has_enough_vietnamese_marks(src_subs):
                        self._thread_safe_log("⏭️  B2: SRT nguồn đã tiếng Việt, bỏ qua dịch.\n")
                        need_translate = False
                except Exception:
                    pass

            if need_translate:
                _set_btn("🌐 B2: Mở Gemini dịch SRT...", "disabled", "#0891b2")
                self._thread_safe_log("\n🌐 BƯỚC 2: Mở Gemini Web dịch SRT → tiếng Việt...\n")
                self._thread_safe_log("💡 Gemini trả kết quả SRT → Ctrl+A → Ctrl+C → app tự nhận.\n")

                prompt_srt, out_srt_path = self._build_ai_studio_srt_translation_prompt(source_srt)
                if not prompt_srt:
                    self._thread_safe_log("❌ Không build được prompt dịch SRT.\n")
                    return

                # Mở Gemini Web + auto paste + watch loop
                self._copy_srt_translation_prompt_and_open_ai_studio(prompt_srt, source_srt, out_srt_path)

                _set_btn("⏳ Chờ SRT từ Gemini...", "disabled", "#0891b2")
                deadline = time.time() + 600
                while time.time() < deadline:
                    time.sleep(2)
                    cur = self._get_source_srt_path()
                    if cur and cur != source_srt and self._is_valid_srt_path(cur):
                        try:
                            if self._srt_text_has_enough_vietnamese_marks(SRTParser.parse_srt(cur)):
                                self._thread_safe_log(f"✅ SRT tiếng Việt về app: {os.path.basename(cur)}\n")
                                break
                        except Exception:
                            pass
                    if out_srt_path and self._is_valid_srt_path(out_srt_path):
                        try:
                            if self._srt_text_has_enough_vietnamese_marks(SRTParser.parse_srt(out_srt_path)):
                                self._set_source_srt_path_threadsafe(out_srt_path, auto_detected=True)
                                self._thread_safe_log(f"✅ SRT tiếng Việt về app: {os.path.basename(out_srt_path)}\n")
                                break
                        except Exception:
                            pass
                else:
                    self._thread_safe_log("⏰ Hết giờ chờ SRT, tiếp tục với SRT gốc.\n")

            # ── BƯỚC 3: Tạo kịch bản bằng Gemini Web ─────────────────
            _set_btn("🤖 B3: AI tạo kịch bản...", "disabled", "#7c3aed")
            self._thread_safe_log("\n🤖 BƯỚC 3: AI tạo kịch bản bằng Gemini Web...\n")

            self._generate_review_package_worker()

            self._thread_safe_log("✅ Kịch bản đã tạo xong.\n")

            self._thread_safe_log("\n" + "="*55 + "\n")
            self._thread_safe_log("🎉 HOÀN TẤT!\n")
            self._thread_safe_log("="*55 + "\n")

        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi: {e}\n")
        finally:
            _set_btn("🎬 TẠO KỊCH BẢN HOÀN CHỈNH", "normal", "#7c3aed")

    def _manual_review_prompt_worker(self):
        try:
            if not self.movie_name.get().strip():
                self._thread_safe_log("❌ Lỗi: Chưa nhập tên phim\n")
                return

            if hasattr(self, "btn_manual_review_prompt"):
                self.after(0, lambda: self.btn_manual_review_prompt.configure(state="disabled", text="ĐANG COPY..."))

            prompt, target_duration = self._build_ai_studio_review_prompt()
            if not prompt:
                return
            self._copy_prompt_and_open_ai_studio(prompt, target_duration)
        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi tạo prompt tay AI Studio: {e}\n")
        finally:
            if hasattr(self, "btn_manual_review_prompt"):
                self.after(0, lambda: self.btn_manual_review_prompt.configure(state="normal", text="PROMPT TAY"))

    def _web_srt_translate_then_prompt_worker(self):
        try:
            if hasattr(self, "btn_web_srt_review_prompt"):
                self.after(0, lambda: self.btn_web_srt_review_prompt.configure(state="disabled", text="ĐANG COPY..."))

            source_srt = self._get_source_srt_path()
            if not source_srt:
                self._thread_safe_log("❌ Cần SRT nguồn để dịch bằng Gemini web\n")
                return
            if not self._is_valid_srt_path(source_srt):
                self._thread_safe_log(f"❌ SRT nguồn không hợp lệ: {source_srt}\n")
                return

            output_path = self._translated_srt_output_path(source_srt)
            if output_path and self._is_valid_srt_path(output_path):
                try:
                    cached_subtitles = SRTParser.parse_srt(output_path)
                    if (
                        os.path.getmtime(output_path) >= os.path.getmtime(source_srt)
                        and self._srt_text_has_enough_vietnamese_marks(cached_subtitles)
                    ):
                        self._thread_safe_log(f"🇻🇳 Dùng lại SRT đã dịch: {output_path}\n")
                        output_path = self._mirror_translated_srt_to_output_dir(output_path)
                        self.source_srt_path = output_path
                        self._set_source_srt_path_threadsafe(output_path, auto_detected=True)
                        threading.Thread(target=self._manual_review_prompt_worker, daemon=True).start()
                        return
                    if cached_subtitles and not self._srt_text_has_enough_vietnamese_marks(cached_subtitles):
                        self._thread_safe_log("⚠️ SRT dịch cache thiếu dấu tiếng Việt; mở Gemini web để dịch lại.\n")
                except Exception:
                    pass

            prompt, output_path = self._build_ai_studio_srt_translation_prompt(source_srt)
            if not prompt:
                return
            self._copy_srt_translation_prompt_and_open_ai_studio(prompt, source_srt, output_path)
        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi tạo prompt dịch SRT web: {e}\n")
        finally:
            if hasattr(self, "btn_web_srt_review_prompt"):
                self.after(
                    0,
                    lambda: self.btn_web_srt_review_prompt.configure(
                        state="normal",
                        text="DỊCH SRT WEB→PROMPT",
                    ),
                )

    def _generate_review_package_worker(self):
        try:
            if not self.movie_name.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa nhập tên phim\n")
                self.log.see("end")
                return

            self.after(0, lambda: self.btn_generate_review.configure(state="disabled", text="ĐANG TẠO..."))
            self._thread_safe_log("🧠 Gemini đang tạo kịch bản review + SRT tiếng Việt...\n")

            keep_seconds, skip_seconds, cut_mode = self._get_cut_settings()
            self._thread_safe_log(f"✂️ Kiểu preview: {cut_mode} | độ dài review theo lựa chọn user\n")
            target_seconds = self._estimate_review_target_seconds(keep_seconds, skip_seconds)
            target_words = int(target_seconds * 2.2) if target_seconds else 900
            target_words = max(450, min(target_words, 4500))

            srt_context = self._read_srt_context(self._get_source_srt_path())
            story_context = self._build_story_context(
                self.movie_description.get("1.0", "end").strip(),
                srt_context,
            )
            tts_language_label = self.tts_language.get() if hasattr(self, "tts_language") else "Tiếng Việt"
            tts_language = "Vietnamese" if tts_language_label == "Tiếng Việt" else "English"
            source_duration_seconds = self._get_source_video_duration()

            self.save_config({
                'gemini_api_key': self.api_key.get(),
                'gemini_keys_file': self.api_keys_file.get(),
                'openrouter_api_key': self.openrouter_api_key.get() if hasattr(self, "openrouter_api_key") else '',
                'review_style': self._selected_review_style_key(),
            })
            ai = AIEngine(self._get_gemini_keys())
            keep_segments = VideoCutter.get_keep_segments(source_duration_seconds or 0, keep_seconds, skip_seconds)
            raw_render_blocks = VideoCalculator.get_render_blocks(keep_segments)
            book_map = PremiumReviewPipeline.build_book_map(raw_render_blocks, srt_context.get("subtitles", []))
            beat_plan = PremiumReviewPipeline.build_beat_plan(book_map)
            book_context = PremiumReviewPipeline.build_book_context(book_map, beat_plan)
            self._thread_safe_log(
                f"✓ Premium Book Map: {len(book_map)} book | Beat Plan: {len(beat_plan)} nhịp\n"
            )
            package = ai.generate_review_package(
                movie_name=self.movie_name.get().strip(),
                movie_description=story_context,
                subtitle_context=srt_context.get("text", ""),
                timed_subtitles=srt_context.get("timed_text", ""),
                language=tts_language,
                target_words=target_words,
                target_duration_seconds=target_seconds,
                character_focus=self.movie_name.get().strip(),
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
                render_blocks=book_map,
                block_context=book_context,
            )

            if package.get("sync_rewrite_used"):
                self._thread_safe_log("↺ Gemini đã tự viết lại kịch bản để khớp block/timing hơn.\n")
            if package.get("length_rewrite_used"):
                self._thread_safe_log("↺ Gemini đã mở rộng kịch bản theo quota từng book để voice đủ thời lượng.\n")
            summary_text = AIEngine._clean_review_script_text(package.get("summary", "").strip())
            script_text = AIEngine._clean_review_script_text(package.get("script", "").strip())
            srt_text = self._build_review_srt(package.get("subtitle_chunks", []), target_seconds)
            if not script_text:
                raise ValueError("Gemini không trả về kịch bản")
            package["book_map"] = book_map
            package["beat_plan"] = beat_plan
            package["sync_report"] = PremiumReviewPipeline.validate_sync(
                package.get("script_blocks"),
                book_map,
                target_seconds,
            )
            if package["sync_report"].get("issues"):
                self._thread_safe_log(
                    f"↺ Sync Validator: {', '.join(package['sync_report']['issues'][:4])}\n"
                )
            if package.get("quality_report", {}).get("issues"):
                self._thread_safe_log(
                    f"↺ Story Polish: {', '.join(package['quality_report']['issues'][:4])}\n"
                )
            if not srt_text:
                srt_text = self._build_review_srt(AIEngine._split_subtitle_chunks(script_text), target_seconds)

            script_path, srt_path = self._save_review_package_assets(script_text, srt_text)

            def _apply():
                if summary_text:
                    self._set_textbox_text(self.movie_description, summary_text)
                self._set_textbox_text(self.review_script_box, script_text)
                self._set_textbox_text(self.review_srt_box, srt_text)
                self.btn_generate_review.configure(state="normal", text="AI TẠO KỊCH BẢN + SRT")
                self.log.insert("end", f"✅ Đã tự thêm kịch bản vào app: {len(script_text.split())} từ\n")
                self.log.insert("end", f"✅ Đã lưu SRT tiếng Việt: {srt_path}\n")
                self.log.insert("end", f"✅ Đã lưu script: {script_path}\n")
                self.log.see("end")

            self.after(0, _apply)
        except Exception as e:
            def _fail():
                if hasattr(self, "btn_generate_review"):
                    self.btn_generate_review.configure(state="normal", text="AI TẠO KỊCH BẢN + SRT")
                self._thread_safe_log(f"❌ Lỗi tạo kịch bản + SRT: {str(e)}\n")
            self.after(0, _fail)

    def _revise_review_with_srt_worker(self):
        try:
            existing_script = self._get_review_script_text()
            if not existing_script:
                self.log.insert("end", "❌ Chưa có kịch bản review để sửa. Hãy tạo kịch bản trước.\n")
                self.log.see("end")
                return

            source_srt = self._get_source_srt_path()
            srt_context = self._read_srt_context(source_srt)
            if not srt_context.get("timed_text"):
                self.log.insert("end", "❌ Hãy chọn file SRT tiếng Việt đã dịch trước khi sửa kịch bản.\n")
                self.log.see("end")
                return

            self.after(0, lambda: self.btn_revise_review.configure(state="disabled", text="ĐANG SỬA THEO THOẠI SRT..."))
            self._thread_safe_log("🧠 Gemini đang đối chiếu kịch bản với thoại SRT có mốc thời gian...\n")

            keep_seconds, skip_seconds, _ = self._get_cut_settings()
            target_seconds = self._estimate_review_target_seconds(keep_seconds, skip_seconds)
            target_words = int(target_seconds * 2.2) if target_seconds else len(existing_script.split())
            target_words = max(300, min(target_words, 4500))
            story_context = self._build_story_context(
                self.movie_description.get("1.0", "end").strip(),
                srt_context,
            )
            tts_language_label = self.tts_language.get() if hasattr(self, "tts_language") else "Tiếng Việt"
            tts_language = "Vietnamese" if tts_language_label == "Tiếng Việt" else "English"
            source_duration_seconds = self._get_source_video_duration()

            ai = AIEngine(self._get_gemini_keys())
            package = ai.revise_review_script_with_dialogue(
                movie_name=self.movie_name.get().strip(),
                existing_script=existing_script,
                timed_subtitles=srt_context.get("timed_text", ""),
                movie_description=story_context,
                language=tts_language,
                target_words=target_words,
                target_duration_seconds=target_seconds,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=source_duration_seconds,
            )
            summary_text = AIEngine._clean_review_script_text(package.get("summary", "").strip())
            revised_script = AIEngine._clean_review_script_text(package.get("script", "").strip())
            revised_srt = self._build_review_srt(package.get("subtitle_chunks", []), target_seconds)
            if not revised_script:
                raise ValueError("Gemini không trả về kịch bản đã sửa")
            script_path, srt_path = self._save_review_package_assets(revised_script, revised_srt)

            def _apply():
                if summary_text:
                    self._set_textbox_text(self.movie_description, summary_text)
                self._set_textbox_text(self.review_script_box, revised_script)
                self._set_textbox_text(self.review_srt_box, revised_srt)
                self.btn_revise_review.configure(state="normal", text="AI SỬA KỊCH BẢN THEO THOẠI SRT")
                self.log.insert("end", "✅ Đã sửa kịch bản theo thoại thật và mốc thời gian SRT.\n")
                self.log.insert("end", f"✅ Script: {script_path}\n")
                self.log.insert("end", f"✅ SRT xuất ra: {srt_path}\n")
                self.log.see("end")

            self.after(0, _apply)
        except Exception as e:
            def _fail():
                self.btn_revise_review.configure(state="normal", text="AI SỬA KỊCH BẢN THEO THOẠI SRT")
                self._thread_safe_log(f"❌ Lỗi sửa kịch bản theo SRT: {str(e)}\n")
            self.after(0, _fail)

    def run_process(self):
        try:
            # Validation: check required fields
            if not self.video_path.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa chọn video gốc\n")
                self.log.see("end")
                return
            if os.path.splitext(self.video_path.get().strip())[1].lower() == ".srt":
                self._set_source_srt_path(self.video_path.get().strip(), auto_detected=False)
                self.video_path.delete(0, "end")
                self.log.insert("end", "❌ Bạn đang để file SRT ở ô Video gốc. Hãy chọn file video .mp4/.mkv trước.\n")
                self.log.see("end")
                return
            if not self.output_dir.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa chọn thư mục lưu video\n")
                self.log.see("end")
                return
            if not self.movie_name.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa nhập tên phim\n")
                self.log.see("end")
                return
            
            self.log.insert("end", "🚀 Bắt đầu quy trình V2...\n")
            self.log.see("end")

            try:
                cfg_data = {
                    'gemini_api_key': self.api_key.get(),
                    'gemini_keys_file': self.api_keys_file.get(),
                    'openrouter_api_key': self.openrouter_api_key.get() if hasattr(self, "openrouter_api_key") else '',
                    'review_style': self._selected_review_style_key(),
                }
                self.save_config(cfg_data)
            except Exception:
                pass

            keep_seconds, skip_seconds, cut_mode = self._get_cut_settings()
            self.log.insert("end", f"✂️ Kiểu preview: {cut_mode} | độ dài review theo lựa chọn user\n")
            self.log.see("end")

            # The preset is a RECAP2 narration budget, not a hard render cutoff.
            preview_label, preview_minutes, max_duration = self._get_preview_duration_settings()

            # Estimate input video duration and expected output (kept) duration
            est_input_seconds = None
            try:
                import cv2
                cap = cv2.VideoCapture(self.video_path.get())
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    if frame_count and fps:
                        est_input_seconds = frame_count / fps
                cap.release()
            except Exception:
                est_input_seconds = None

            est_output_seconds = None
            if max_duration and max_duration > 0:
                est_output_seconds = max_duration
            elif est_input_seconds:
                cycle = keep_seconds + skip_seconds if (keep_seconds + skip_seconds) > 0 else 1
                est_output_seconds = est_input_seconds * (keep_seconds / cycle)

            try:
                ai = AIEngine(self._get_gemini_keys())
                self.log.insert("end", "📝 Đang tạo kịch bản...\n")
                self.log.see("end")
                movie_description = self.movie_description.get("1.0", "end").strip()
                tts_language_label = self.tts_language.get() if hasattr(self, 'tts_language') else "Tiếng Việt"
                tts_language = "Vietnamese" if self._is_vietnamese_language(tts_language_label) else "English"
                selected_voice = self._resolve_voice_id(self.voice_choice.get(), tts_language_label) if hasattr(self, 'voice_choice') else "vi-VN-HoaiMyNeural"
                if self._is_vietnamese_language(tts_language_label) and not selected_voice.startswith("vi-") and not selected_voice.startswith("piper:"):
                    selected_voice = "vi-VN-HoaiMyNeural"
                script_type = self.script_type.get() if hasattr(self, 'script_type') else "Mô-đun (Intro+Body+Outro)"
                prebuilt_review_script = self._get_review_script_text()
                if prebuilt_review_script:
                    script = prebuilt_review_script
                    use_pasted_script = True
                    script_type = "AI review package"
                else:
                    script, use_pasted_script = self._extract_voice_script(movie_description)
                if "Mô-đun" in script_type:
                    use_pasted_script = False

                # Compute target words based on estimated output duration
                target_words = None
                if est_output_seconds and est_output_seconds > 0:
                    words_per_sec = 2.5
                    target_words = int(est_output_seconds * words_per_sec)
                # Clamp sensible bounds
                if target_words:
                    target_words = max(300, min(target_words, 5000))

                if est_output_seconds:
                    self.log.insert("end", f"ℹ️ Video giữ dự kiến ~{int(est_output_seconds)} giây; kịch bản sẽ khoảng {target_words} từ nếu tạo audio phù hợp.\n")
                    self.log.see("end")

                # Choose script generation method based on user selection
                if prebuilt_review_script:
                    self.log.insert("end", f"✓ Dùng kịch bản review AI đã tự thêm vào app ({len(script.split())} từ)\n")
                elif use_pasted_script:
                    script_type = "Pasted script"
                    self.log.insert("end", f"✓ Dùng kịch bản đã dán để tạo voice ({len(script.split())} từ)\n")
                elif "Mô-đun" in script_type:
                    self.log.insert("end", "📝 Đang tạo kịch bản (chế độ Mô-đun)...\n")
                    script = ai.generate_script_modular(
                        self.movie_name.get(),
                        movie_description,
                        keep_seconds,
                        skip_seconds,
                        est_output_seconds,
                        target_words,
                        language=tts_language,
                        source_duration_seconds=est_input_seconds,
                    )
                else:
                    self.log.insert("end", "📝 Đang tạo kịch bản (chế độ Liên tục)...\n")
                    script = ai.generate_script(
                        self.movie_name.get(),
                        movie_description,
                        keep_seconds,
                        skip_seconds,
                        est_output_seconds,
                        target_words,
                        language=tts_language,
                        source_duration_seconds=est_input_seconds,
                    )
                self.log.insert("end", "✓ Kịch bản hoàn thành\n")
                self.log.see("end")
            except Exception as e:
                self.log.insert("end", f"❌ Lỗi: Không tạo được kịch bản ({str(e)}). Dừng quy trình.\n")
                self.log.see("end")
                return

            # Text-to-speech generation. Stop on TTS errors so we never render
            # a silent video while pretending voice generation succeeded.
            tts_path = "temp_v2.mp3"
            try:
                script_seconds = len(script.split()) / 2.5 if script else 0
                voice_rate = AIEngine.calculate_tts_rate(est_output_seconds or script_seconds, script_seconds)
                self.log.insert("end", f"🎙️ Đang tạo giọng nói ({tts_language_label}, {selected_voice}, {voice_rate})...\n")
                self.log.see("end")
                chosen_voice = asyncio.run(
                    ai.text_to_speech(script, tts_path, voice=selected_voice, rate=voice_rate)
                )
                self.log.insert("end", f"✓ Giọng nói hoàn thành: {chosen_voice}\n")
                if "vi-" in chosen_voice:
                    self.log.insert("end", "  → Tiếng Việt ✓\n")
                else:
                    self.log.insert("end", f"  ⚠️ Không phải tiếng Việt, hệ thống dùng: {chosen_voice}\n")
                self.log.see("end")
            except Exception as e:
                self.log.insert("end", f"❌ Lỗi TTS: {str(e)}\n")
                self.log.insert("end", "   Dừng render vì không tạo được voice theo kịch bản.\n")
                self.log.see("end")
                return
            
            self.log.insert("end", "🎬 Đang bánh video & vẽ khung tiêu đề...\n")
            self.log.see("end")
            out = os.path.join(self.output_dir.get(), "recap_v2_pro.mp4")
            
            ve = VideoEngine()
            render_keep_seconds = keep_seconds
            render_skip_seconds = skip_seconds
            voice_intro_path = getattr(self, 'voice_intro_path', None)
            if not voice_intro_path:
                default_intro = r"C:\Users\Nguyen Tuan Computer\Desktop\chao_tat_ca_cac_ban_den_voi_rewphim_chu_ba_tool_54836a66-5eea-4c3d-be05-b2eb1132992c.mp3"
                if os.path.exists(default_intro):
                    voice_intro_path = default_intro
            if script_type == "Pasted script":
                render_keep_seconds = 1
                render_skip_seconds = 0
                self.log.insert("end", "ℹ️ Kịch bản dán sẵn: render video liên tục, không dùng chế độ băm.\n")
                self.log.see("end")
            
            if "Mô-đun" in script_type:
                self.log.insert("end", f"ℹ️ Render đúng công thức: giữ {render_keep_seconds}s, bỏ {render_skip_seconds}s. AI đã viết script bám theo mạch phim.\n")
                self.log.see("end")

            header_pos, footer_pos = self._get_overlay_positions()
            success, video_error = ve.process_video_v2(
                self.video_path.get(), out, "temp_v2.mp3", self.bgm_path.get(),
                self.header_text.get(), self.footer_text.get(),
                render_keep_seconds, render_skip_seconds, max_duration,
                self.header_font_size, self.footer_font_size,
                self.header_color, self.footer_color,
                voice_intro_path,
                self.header_bar_color,
                self.footer_bar_color,
                header_pos,
                footer_pos,
            )
            
            if success:
                self.log.insert("end", f"✨ XONG! Lưu tại: {out}\n")
                self.log.see("end")
            else:
                self.log.insert("end", f"❌ Lỗi xử lý video: {video_error}\n")
                self.log.see("end")
        except Exception as e:
            self.log.insert("end", f"❌ Lỗi: {str(e)}\n")
            self.log.see("end")

    def _get_source_srt_path(self):
        source_path = getattr(self, "source_srt_path", "").strip()
        if source_path and os.path.exists(source_path):
            lower_name = os.path.basename(source_path).lower()
            if self._is_generated_review_srt_path(source_path):
                return ""
            if lower_name.endswith("_generated.srt"):
                return ""
            return source_path
        entry_path = self.srt_path.get().strip() if hasattr(self, "srt_path") else ""
        if entry_path and os.path.exists(entry_path):
            lower_name = os.path.basename(entry_path).lower()
            if self._is_generated_review_srt_path(entry_path):
                return ""
            if lower_name.endswith("_generated.srt"):
                return ""
            return entry_path
        return ""

    def _set_source_srt_path(self, srt_path, auto_detected=False):
        self.source_srt_path = srt_path
        self.srt_path.delete(0, "end")
        self.srt_path.insert(0, srt_path)
        reason = "tự nhận diện" if auto_detected else "đã chọn"
        self.log.insert("end", f"📝 SRT thoại nguồn ({reason}): {srt_path}\n")
        self.log.see("end")
        self._invalidate_review_package_fields()

    def _detect_source_srt_next_to_video(self):
        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path or not os.path.exists(video_path):
            return ""

        video_dir = os.path.dirname(video_path)
        video_stem = os.path.splitext(os.path.basename(video_path))[0].lower()
        # Chi dung ten file video hien tai. Movie name co the van la ten phim cu
        # khi user vua doi video, nen dua no vao reference se nhan nham SRT.
        reference_text = video_stem
        reference_tokens = self._normalize_srt_reference_tokens(reference_text)
        reference_words = {token for token in reference_tokens if not token.isdigit()}
        try:
            candidates = []
            for name in os.listdir(video_dir):
                if not name.lower().endswith(".srt"):
                    continue
                if self._is_generated_review_srt_path(name):
                    continue
                if name.lower().endswith("_generated.srt"):
                    continue
                path = os.path.join(video_dir, name)
                score = self._score_source_srt_candidate(reference_text, path)
                lower = name.lower()

                candidate_stem = os.path.splitext(lower)[0]
                for suffix in ("_capcut_translated", "_translated", "_capcut", "_vi"):
                    if candidate_stem.endswith(suffix):
                        candidate_stem = candidate_stem[:-len(suffix)]
                        break
                candidate_tokens = self._normalize_srt_reference_tokens(candidate_stem)
                candidate_words = {token for token in candidate_tokens if not token.isdigit()}
                shared_words = reference_words & candidate_words
                exact_base = candidate_tokens == reference_tokens
                required_shared = 1 if len(reference_words) <= 2 else max(2, (len(reference_words) + 1) // 2)

                # Khong cho diem ten duoi (_translated/_capcut) bien SRT phim
                # khac thanh ung vien hop le. Ten noi dung phai khop video truoc.
                if not exact_base and len(shared_words) < required_shared:
                    continue
                if lower.endswith("_capcut_translated.srt") or lower.endswith("_translated.srt"):
                    score += 12
                elif lower.endswith("_vi.srt"):
                    score += 10
                elif lower.endswith("_capcut.srt"):
                    score += 4
                candidates.append((score, path))
            if not candidates:
                return ""
            candidates.sort(key=lambda item: item[0], reverse=True)
            if candidates[0][0] >= 8:
                return candidates[0][1]
        except Exception:
            return ""
        return ""

    def _ensure_source_srt(self, prompt_if_missing=False):
        source_path = self._get_source_srt_path()
        if source_path:
            return source_path

        source_path = self._detect_source_srt_next_to_video()
        if source_path:
            self._set_source_srt_path(source_path, auto_detected=True)
            return source_path

        if prompt_if_missing:
            source_path = filedialog.askopenfilename(
                title="Chọn SRT thoại gốc đã dịch để AI bám nội dung",
                filetypes=[("SRT subtitles", "*.srt"), ("All files", "*.*")]
            )
            if source_path:
                self._set_source_srt_path(source_path, auto_detected=False)
                return source_path
        return ""

    def _read_srt_context(self, srt_file):
        if not srt_file or not os.path.exists(srt_file):
            return {
                "text": "",
                "timed_text": "",
                "scenes": [],
                "subtitle_count": 0,
            }

        subtitles = SRTParser.parse_srt(srt_file)
        scenes = SRTParser.extract_scenes_from_subtitles(subtitles)
        merged = []
        timed_lines = []
        if len(subtitles) <= 100:
            sampled_subtitles = subtitles
        else:
            sampled_indexes = {
                int(round(index * (len(subtitles) - 1) / 99))
                for index in range(100)
            }
            sampled_subtitles = [subtitles[index] for index in sorted(sampled_indexes)]
        for sub in sampled_subtitles:
            text = re.sub(r"\s+", " ", sub.get("text", "")).strip()
            if text:
                timed_lines.append(f"[{sub.get('start', '')} --> {sub.get('end', '')}] {text}")
                if len(merged) < 25:
                    merged.append(text)
        return {
            "text": "\n".join(merged),
            "timed_text": "\n".join(timed_lines),
            "scenes": scenes,
            "subtitles": subtitles,
            "subtitle_count": len(subtitles),
        }

    def _build_story_context(self, movie_description, srt_context):
        parts = []
        if movie_description:
            parts.append(movie_description.strip())
        if srt_context.get("text"):
            parts.append("Tư liệu SRT:\n" + srt_context["text"])
        if srt_context.get("timed_text"):
            parts.append("Thoại SRT có mốc thời gian:\n" + srt_context["timed_text"][:8000])
        if srt_context.get("scenes"):
            scene_lines = []
            for start, end, label in srt_context["scenes"][:12]:
                scene_lines.append(f"{label}: {start:.1f}s -> {end:.1f}s")
            if scene_lines:
                parts.append("Timeline cảnh:\n" + "\n".join(scene_lines))
        return "\n\n".join(parts).strip()

    def _build_block_srt_context(self, render_blocks, subtitles, max_blocks=50, max_snippets=4):
        if not render_blocks or not subtitles:
            return ""

        lines = []
        sampled_blocks = render_blocks[:max_blocks]
        for block in sampled_blocks:
            block_id = block.get("block_id")
            start = float(block.get("start_in_final_video", 0.0) or 0.0)
            end = float(block.get("end_in_final_video", start + float(block.get("duration", 0.0) or 0.0)) or start)
            scene_role = block.get("scene_role_label") or block.get("scene_role") or ""
            book_title = block.get("book_title") or scene_role or ""
            beat = block.get("beat") or ""
            emotion = block.get("emotion") or ""
            visual_anchor = block.get("visual_anchor") or ""
            srt_anchor = block.get("srt_anchor") or ""
            target_words = block.get("target_words")
            source_count = block.get("source_block_count")
            source_ids = block.get("source_block_ids") or ""
            reason = block.get("cut_reason") or block.get("reason") or ""
            snippets = []
            for sub in subtitles:
                sub_start = float(sub.get("start_seconds", 0.0) or 0.0)
                sub_end = float(sub.get("end_seconds", sub_start) or sub_start)
                if sub_end < start or sub_start > end:
                    continue
                text = re.sub(r"\s+", " ", sub.get("text", "")).strip()
                if text:
                    snippets.append(text)
                if len(snippets) >= max_snippets:
                    break
            if not snippets:
                continue
            srt_text = " | ".join(snippets)
            if len(srt_text) > 520:
                srt_text = srt_text[:517].rstrip() + "..."
            lines.append(
                f"- Book {block_id} [{scene_role}] {start:.1f}s-{end:.1f}s"
                f" | title: {book_title}"
                f" | beat: {beat}"
                f" | emotion: {emotion}"
                f" | visual: {visual_anchor}"
                f" | srt_anchor: {srt_anchor}"
                f" | target_words: {target_words if target_words is not None else ''}"
                f" | source_blocks: {source_count if source_count is not None else ''}"
                f" | source_ids: {source_ids}"
                f" | reason: {reason}"
                f" | SRT neo: {srt_text}"
            )
        return "\n".join(lines)

    def _build_voice_books_from_render_blocks(self, render_blocks, subtitles=None, target_book_seconds=24.0):
        return PremiumReviewPipeline.build_book_map(render_blocks, subtitles or [], target_book_seconds)

    def _build_review_srt_from_script_blocks(self, script_blocks, render_blocks, target_seconds=None):
        if not script_blocks or not render_blocks:
            return ""

        render_map = {block.get("block_id"): block for block in render_blocks}
        lines = []
        subtitle_index = 1
        for block in script_blocks:
            text = str(block.get("text", "") if isinstance(block, dict) else block).strip()
            if not text:
                continue
            block_id = block.get("block_id") if isinstance(block, dict) else None
            render_block = render_map.get(block_id)
            if not render_block:
                continue
            start = float(render_block.get("start_in_final_video", 0.0) or 0.0)
            end = float(render_block.get("end_in_final_video", start + float(render_block.get("duration", 0.0) or 0.0)) or start)
            if end <= start:
                continue
            chunks = []
            for chunk in AIEngine._split_subtitle_chunks(text):
                words = chunk.split()
                while len(words) > 16:
                    chunks.append(" ".join(words[:14]))
                    words = words[14:]
                if words:
                    chunks.append(" ".join(words))
            chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
            if not chunks:
                continue
            weights = [max(1, len(chunk.split())) for chunk in chunks]
            total_weight = sum(weights) or len(chunks)
            cursor = start
            block_duration = end - start
            for chunk, weight in zip(chunks, weights):
                duration = max(1.0, block_duration * (weight / total_weight))
                chunk_start = cursor
                chunk_end = min(end, chunk_start + duration)
                if chunk_end <= chunk_start:
                    chunk_end = min(end, chunk_start + 1.0)
                lines.append(
                    f"{subtitle_index}\n"
                    f"{SRTParser.seconds_to_timecode(chunk_start)} --> {SRTParser.seconds_to_timecode(chunk_end)}\n"
                    f"{chunk}\n"
                )
                subtitle_index += 1
                cursor = min(end, chunk_end + 0.04)
                if cursor >= end:
                    break
        if not lines:
            return ""
        if target_seconds:
            # Keep subtitles within the final rendered video duration.
            return "\n".join(lines).strip()
        return "\n".join(lines).strip()

    def _build_visual_context_from_cut_video(self, video_path, render_blocks, max_blocks=50):
        if not video_path or not os.path.exists(video_path) or not render_blocks:
            return ""
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return ""
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            duration = frame_count / fps if fps and frame_count else 0
            lines = []
            face_cascade = None
            try:
                cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if os.path.exists(cascade_path):
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    if face_cascade.empty():
                        face_cascade = None
            except Exception:
                face_cascade = None
            sampled_blocks = render_blocks[:max_blocks]
            for block in sampled_blocks:
                block_id = block.get("block_id")
                start = float(block.get("start_in_final_video", 0.0) or 0.0)
                end = float(block.get("end_in_final_video", start + float(block.get("duration", 0.0) or 0.0)) or start)
                cut_reason = block.get("cut_reason") or block.get("reason") or ""
                scene_role = block.get("scene_role_label") or block.get("scene_role") or ""
                reason_suffix = f", ly do cat: {cut_reason}" if cut_reason else ""
                midpoint = max(0.0, min(duration, (start + end) / 2.0))
                cap.set(cv2.CAP_PROP_POS_MSEC, midpoint * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue

                face_label = ""
                if face_cascade is not None:
                    try:
                        preview_width = 320
                        preview_height = max(1, int(frame.shape[0] * (preview_width / max(1, frame.shape[1]))))
                        face_frame = cv2.resize(frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
                        gray_face = cv2.cvtColor(face_frame, cv2.COLOR_BGR2GRAY)
                        faces = face_cascade.detectMultiScale(
                            gray_face,
                            scaleFactor=1.1,
                            minNeighbors=4,
                            minSize=(24, 24),
                        )
                        if len(faces) >= 3:
                            face_label = "nhieu nhan vat/khuon mat"
                        elif len(faces) > 0:
                            largest_face = max((w * h for (_x, _y, w, h) in faces), default=0)
                            prominence = largest_face / max(1, gray_face.shape[0] * gray_face.shape[1])
                            if prominence >= 0.08:
                                face_label = "can mat nhan vat/noi tam"
                            else:
                                face_label = "co nhan vat trong khung"
                    except Exception:
                        face_label = ""

                small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                brightness = float(np.mean(hsv[:, :, 2]))
                saturation = float(np.mean(hsv[:, :, 1]))
                hue = float(np.mean(hsv[:, :, 0]))

                if brightness < 70:
                    light_label = "toi/cang thang"
                elif brightness > 170:
                    light_label = "sang/ro mat"
                else:
                    light_label = "anh sang trung binh"

                if saturation < 45:
                    color_label = "mau tram/it mau"
                elif hue < 20 or hue > 160:
                    color_label = "tong do/cam"
                elif hue < 45:
                    color_label = "tong vang"
                elif hue < 85:
                    color_label = "tong xanh la"
                elif hue < 130:
                    color_label = "tong xanh/lanh"
                else:
                    color_label = "tong tim/hong"

                motion_label = ""
                if end - start >= 0.8:
                    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, midpoint - 0.35) * 1000)
                    ok_prev, prev = cap.read()
                    cap.set(cv2.CAP_PROP_POS_MSEC, min(duration, midpoint + 0.35) * 1000)
                    ok_next, nxt = cap.read()
                    if ok_prev and ok_next and prev is not None and nxt is not None:
                        prev_gray = cv2.cvtColor(cv2.resize(prev, (96, 54), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                        next_gray = cv2.cvtColor(cv2.resize(nxt, (96, 54), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                        motion = float(np.mean(cv2.absdiff(prev_gray, next_gray)))
                        if motion > 28:
                            motion_label = "chuyen dong manh/hanh dong"
                        elif motion > 12:
                            motion_label = "chuyen dong vua"
                        else:
                            motion_label = "canh tinh/doi thoai"

                lines.append(
                    f"- Block {block_id} ({start:.1f}s-{end:.1f}s)"
                    + (f" [{scene_role}]" if scene_role else "")
                    + f": {light_label}, {color_label}"
                    + (f", {motion_label}" if motion_label else "")
                    + (f", {face_label}" if face_label else "")
                    + reason_suffix
                )
            if len(render_blocks) > len(sampled_blocks):
                lines.append(f"... con {len(render_blocks) - len(sampled_blocks)} block khac.")
            return "\n".join(lines)
        except Exception:
            return ""
        finally:
            if cap is not None:
                cap.release()

    def _analyze_script_timing(self, script_text, target_seconds, words_per_second=2.5):
        clean_script = AIEngine._clean_tts_text(script_text or "")
        words = len(clean_script.split())
        estimated_seconds = words / words_per_second if words_per_second > 0 else 0
        ratio = estimated_seconds / target_seconds if target_seconds and target_seconds > 0 else 1.0
        if ratio > 1.08:
            action = "increase_speed"
        elif ratio < 0.92:
            action = "decrease_speed"
        else:
            action = "match"
        return {
            "words": words,
            "estimated_seconds": estimated_seconds,
            "ratio": ratio,
            "action": action,
        }

    def _read_full_srt_timed_text_for_prompt(self, srt_path, max_chars=180000):
        if not srt_path or not os.path.exists(srt_path):
            return ""
        subtitles = SRTParser.parse_srt(srt_path)
        lines = []
        for sub in subtitles:
            text = re.sub(r"\s+", " ", str(sub.get("text") or "")).strip()
            if not text:
                continue
            lines.append(
                f"{sub.get('index')}. [{sub.get('start')} --> {sub.get('end')}] {text}"
            )
        full_text = "\n".join(lines).strip()
        if len(full_text) <= max_chars:
            return full_text
        average = max(40, int(len(full_text) / max(1, len(lines))))
        sample_count = max(180, min(len(lines), int(max_chars / average)))
        indexes = {
            int(round(i * (len(lines) - 1) / max(1, sample_count - 1)))
            for i in range(sample_count)
        }
        sampled = [lines[index] for index in sorted(indexes)]
        return (
            f"[SRT quá dài: đưa mẫu đều toàn phim {len(sampled)}/{len(lines)} dòng, "
            "giữ đầu-giữa-cuối để tránh lệch mạch]\n"
            + "\n".join(sampled)
        ).strip()

    def _build_ai_studio_srt_translation_prompt(self, source_srt_path=None):
        source_srt_path = source_srt_path or self._get_source_srt_path()
        if not source_srt_path or not os.path.exists(source_srt_path):
            self._thread_safe_log("❌ Cần SRT nguồn để tạo prompt dịch trên Gemini web\n")
            return "", ""
        subtitles = SRTParser.parse_srt(source_srt_path)
        if not subtitles:
            self._thread_safe_log(f"❌ SRT nguồn không có subtitle hợp lệ: {source_srt_path}\n")
            return "", ""

        try:
            with open(source_srt_path, "r", encoding="utf-8-sig") as handle:
                raw_srt = handle.read()
        except UnicodeDecodeError:
            with open(source_srt_path, "r", encoding="utf-8", errors="replace") as handle:
                raw_srt = handle.read()
        raw_srt = raw_srt.replace("\r\n", "\n").replace("\r", "\n").strip()

        movie_name = self.movie_name.get().strip() if hasattr(self, "movie_name") else ""
        output_path = self._translated_srt_output_path(source_srt_path)
        prompt = f"""Bạn là biên dịch phụ đề phim chuyên nghiệp.

NHIỆM VỤ: Dịch TOÀN BỘ SRT nguồn bên dưới sang tiếng Việt CÓ DẤU, tự nhiên như phụ đề phim.

THÔNG TIN:
- Tên phim/tập: {movie_name}
- File nguồn: {os.path.basename(source_srt_path)}
- Số cue nguồn: {len(subtitles)}

LUẬT BẮT BUỘC:
1. GIỮ NGUYÊN số thứ tự cue.
2. GIỮ NGUYÊN timestamp từng cue, không sửa thời gian.
3. KHÔNG thêm cue, KHÔNG xóa cue, KHÔNG gộp cue.
4. Nếu dòng gốc là tiếng Việt không dấu, hãy phục hồi dấu tiếng Việt theo ngữ cảnh.
5. Nếu dòng gốc là tiếng Trung/Anh/Hàn/Nhật, dịch nghĩa sang tiếng Việt có dấu.
6. Giữ tên riêng nhất quán, không tự bịa tình tiết.
7. Chỉ trả về nội dung .srt hợp lệ, KHÔNG markdown, KHÔNG giải thích.
8. Đây chỉ là bước dịch SRT, KHÔNG viết kịch bản review ở bước này.

SRT NGUỒN CẦN DỊCH:
{raw_srt}

Trả về ngay DUY NHẤT file SRT tiếng Việt có dấu."""
        return prompt, output_path

    def _copy_srt_translation_prompt_and_open_ai_studio(self, prompt, source_srt_path, output_path, auto_continue_review=True, run_async=True):
        try:
            prompt_file = self._save_manual_ai_studio_prompt_file(
                prompt,
                filename="manual_srt_translation_prompt.txt",
            )
            copied, method = self._copy_text_to_system_clipboard(prompt)
            if copied:
                self._thread_safe_log(
                    f"✅ Prompt dịch SRT đã copy vào clipboard ({method}).\n"
                )
            else:
                self._thread_safe_log(f"⚠️ Chưa copy được clipboard: {method}\n")
                self._thread_safe_log("ℹ️ Prompt dịch SRT đã lưu ra file dự phòng.\n")
            self._thread_safe_log(f"📝 Prompt dịch SRT: {prompt_file}\n")

            def _send_and_save_srt_translation():
                try:
                    from core.gemini_web import (
                        create_auto_driver,
                        driver_is_headless,
                        send_prompt_to_gemini,
                    )

                    source_subtitles = SRTParser.parse_srt(source_srt_path)
                    if not source_subtitles:
                        self._thread_safe_log("❌ SRT nguồn không có cue hợp lệ để dịch.\n")
                        return ""

                    def _format_batch_srt(items):
                        parts = []
                        for item in items:
                            parts.append(
                                f"{item.get('index')}\n"
                                f"{item.get('start')} --> {item.get('end')}\n"
                                f"{str(item.get('text') or '').strip()}"
                            )
                        return "\n\n".join(parts).strip()

                    def _build_batch_prompt(items, batch_no, total_batches):
                        return f"""Bạn là biên dịch phụ đề phim chuyên nghiệp.

NHIỆM VỤ: Dịch PHẦN {batch_no}/{total_batches} của SRT sang tiếng Việt CÓ DẤU.

LUẬT BẮT BUỘC:
1. GIỮ NGUYÊN số thứ tự cue.
2. GIỮ NGUYÊN timestamp từng cue.
3. KHÔNG thêm cue, KHÔNG xóa cue, KHÔNG gộp cue.
4. Dịch tiếng Trung/Anh/Hàn/Nhật sang tiếng Việt tự nhiên như phụ đề phim.
5. Nếu có tiếng Việt không dấu, phục hồi dấu theo ngữ cảnh.
6. Chỉ trả về nội dung .srt hợp lệ cho đúng phần này, KHÔNG markdown, KHÔNG giải thích.

SRT NGUỒN PHẦN {batch_no}/{total_batches}:
{_format_batch_srt(items)}

Trả về ngay DUY NHẤT SRT tiếng Việt của phần này."""

                    def _build_batch_retry_prompt(items, batch_no, total_batches, previous_text="", reason=""):
                        previous_text = str(previous_text or "").strip()
                        if len(previous_text) > 9000:
                            previous_text = previous_text[:9000]
                        return f"""Bạn là biên dịch phụ đề phim chuyên nghiệp.

KẾT QUẢ TRƯỚC BỊ LỖI: {reason or 'thiếu dấu tiếng Việt hoặc thiếu cue'}.
Hãy DỊCH LẠI PHẦN {batch_no}/{total_batches} sang tiếng Việt CÓ DẤU đầy đủ.

LUẬT BẮT BUỘC:
1. GIỮ NGUYÊN số thứ tự cue.
2. GIỮ NGUYÊN timestamp từng cue.
3. PHẢI trả đúng {len(items)} cue, không thiếu, không gộp.
4. Tiếng Việt phải CÓ DẤU đầy đủ: không viết kiểu không dấu như 'toi khong biet'.
5. Nếu câu gốc là tiếng Việt không dấu, hãy phục hồi dấu theo ngữ cảnh.
6. Chỉ trả về nội dung .srt hợp lệ, KHÔNG markdown, KHÔNG giải thích.

SRT NGUỒN PHẦN {batch_no}/{total_batches}:
{_format_batch_srt(items)}

KẾT QUẢ LẦN TRƯỚC ĐỂ THAM KHẢO, KHÔNG COPY NẾU THIẾU DẤU:
{previous_text}

Trả về ngay DUY NHẤT SRT tiếng Việt CÓ DẤU của phần này."""

                    batch_size = int(os.environ.get("AUTORECAP_SRT_TRANSLATE_BATCH_SIZE", "50") or "50")
                    batch_size = max(20, min(batch_size, 80))
                    batches = [
                        source_subtitles[i:i + batch_size]
                        for i in range(0, len(source_subtitles), batch_size)
                    ]
                    self._thread_safe_log(
                        f"🌐 Gemini Web tự động: dịch SRT theo {len(batches)} lượt "
                        f"({len(source_subtitles)} cue, {batch_size} cue/lượt)...\n"
                    )
                    timeout = int(os.environ.get("AUTORECAP_SRT_WEB_WAIT_SECONDS", "900") or "900")
                    translated_chunks = []
                    driver = create_auto_driver(
                        log=lambda msg: self._thread_safe_log(str(msg).rstrip() + "\n"),
                    )
                    web_loaded = [False]
                    visible_recovery = [False]
                    def _translate_batch_items(items, batch_label):
                        nonlocal driver
                        self._thread_safe_log(
                            f"   Gemini Web: dich SRT batch {batch_label}/{len(batches)} "
                            f"({len(items)} cue)...\n"
                        )
                        last_error = None
                        previous_response = ""
                        for attempt in range(1, 4):
                            if attempt == 1:
                                prompt_text = _build_batch_prompt(items, batch_label, len(batches))
                            else:
                                prompt_text = _build_batch_retry_prompt(
                                    items,
                                    batch_label,
                                    len(batches),
                                    previous_response,
                                    last_error,
                                )
                            response = send_prompt_to_gemini(
                                prompt_text,
                                timeout=timeout,
                                log=lambda msg: self._thread_safe_log(str(msg).rstrip() + "\n"),
                                driver=driver,
                                close_after=False,
                                navigate=not web_loaded[0],
                            )
                            web_loaded[0] = True
                            previous_response = response or previous_response
                            if not response:
                                if not visible_recovery[0] and driver_is_headless(driver):
                                    self._thread_safe_log(
                                        "   ⚠️ Gemini chạy ẩn chưa dùng được; mở Chrome để đăng nhập lại.\n"
                                    )
                                    try:
                                        driver.quit()
                                    except Exception:
                                        pass
                                    driver = create_auto_driver(
                                        log=lambda msg: self._thread_safe_log(str(msg).rstrip() + "\n"),
                                        force_visible=True,
                                    )
                                    visible_recovery[0] = True
                                    web_loaded[0] = False
                                    continue
                                last_error = "empty response"
                                self._thread_safe_log(
                                    f"   Batch {batch_label} response rong lan {attempt} -> doi trong cung phien roi thu lai.\n"
                                )
                                try:
                                    import time as _time
                                    _time.sleep(6 + attempt * 2)
                                except Exception:
                                    pass
                                continue
                            candidate = self._extract_srt_payload_from_ai_studio_text(response)
                            parsed = self._parse_srt_payload_text(candidate)
                            expected = len(items)
                            if len(parsed) == expected:
                                if not self._srt_text_has_enough_vietnamese_marks(parsed):
                                    last_error = "SRT thiếu dấu tiếng Việt"
                                    self._thread_safe_log(
                                        f"   Batch {batch_label} thiếu dấu tiếng Việt lần {attempt}; "
                                        "gọi lại Gemini Web để sửa dấu.\n"
                                    )
                                    try:
                                        import time as _time
                                        _time.sleep(5 + attempt * 2)
                                    except Exception:
                                        pass
                                    previous_response = candidate
                                    continue
                                return [candidate.strip()]
                            last_error = f"{len(parsed)}/{expected}"
                            self._thread_safe_log(
                                f"   Batch {batch_label} sai cue lan {attempt}: "
                                f"{last_error}. Thu lai hoac chia nho.\n"
                            )

                        if len(items) > 10:
                            mid = len(items) // 2
                            self._thread_safe_log(
                                f"   Batch {batch_label} bi thieu cue, tu chia "
                                f"{len(items)} -> {mid}+{len(items) - mid}.\n"
                            )
                            return (
                                _translate_batch_items(items[:mid], f"{batch_label}a")
                                + _translate_batch_items(items[mid:], f"{batch_label}b")
                            )

                        raise RuntimeError(f"Batch {batch_label} sai so cue sau khi chia nho: {last_error}")

                    for batch_no, items in enumerate(batches, 1):
                        translated_chunks.extend(_translate_batch_items(items, str(batch_no)))
                        try:
                            import time as _time
                            _time.sleep(float(os.environ.get("AUTORECAP_SRT_TRANSLATE_BATCH_PAUSE", "4") or "4"))
                        except Exception:
                            pass
                        continue
                        self._thread_safe_log(f"   🌐 Dịch SRT batch {batch_no}/{len(batches)}...\n")
                        response = send_prompt_to_gemini(
                            _build_batch_prompt(items, batch_no, len(batches)),
                            timeout=timeout,
                            log=lambda msg: self._thread_safe_log(str(msg).rstrip() + "\n"),
                            driver=driver,
                            close_after=False,
                        )
                        if not response:
                            self._thread_safe_log(f"❌ Gemini Web không trả về batch {batch_no}/{len(batches)}.\n")
                            raise RuntimeError(f"Gemini Web khong tra ve batch {batch_no}/{len(batches)}")
                        candidate = self._extract_srt_payload_from_ai_studio_text(response)
                        parsed = self._parse_srt_payload_text(candidate)
                        expected = len(items)
                        if len(parsed) != expected:
                            self._thread_safe_log(
                                f"❌ Batch {batch_no} bị thiếu cue: {len(parsed)}/{expected}. "
                                "App dừng để tránh SRT thiếu dòng.\n"
                            )
                            raise RuntimeError(f"Batch {batch_no} sai so cue: {len(parsed)}/{expected}")
                        translated_chunks.append(candidate.strip())

                    try:
                        driver.quit()
                    except Exception:
                        pass

                    response = "\n\n".join(translated_chunks).strip()
                    ok = self._apply_ai_studio_srt_translation_result(
                        response,
                        source_srt_path,
                        output_path,
                        auto_continue_review=auto_continue_review,
                    )
                    if ok:
                        self._thread_safe_log("✅ Đã tự động nhận và lưu SRT dịch từ Gemini Web.\n")
                        try:
                            current = self._get_source_srt_path()
                        except Exception:
                            current = ""
                        if current and os.path.exists(current):
                            return current
                        mirrored = self._mirror_translated_srt_to_output_dir(output_path)
                        return mirrored if mirrored and os.path.exists(mirrored) else output_path
                    else:
                        self._thread_safe_log("❌ Gemini Web trả về nhưng app chưa đọc được SRT hợp lệ.\n")
                        return ""
                except Exception as ae:
                    try:
                        if "driver" in locals() and driver is not None:
                            driver.quit()
                    except Exception:
                        pass
                    self._thread_safe_log(f"❌ Tự động dịch SRT bằng Gemini Web lỗi: {ae}\n")
                    return ""

            if run_async:
                threading.Thread(target=_send_and_save_srt_translation, daemon=True).start()
                return True
            return _send_and_save_srt_translation()
        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi mở Gemini web dịch SRT: {e}\n")
            return False

    def _build_ai_studio_review_prompt(self, cut_duration: float = None):
        movie_name = self.movie_name.get().strip() if hasattr(self, "movie_name") else ""
        movie_desc = self.movie_description.get("1.0", "end").strip() if hasattr(self, "movie_description") else ""
        srt_path = self._get_source_srt_path()

        if not srt_path:
            self._thread_safe_log("❌ Cần SRT nguồn để tạo prompt tay cho AI Studio\n")
            return "", None

        keep_seconds, skip_seconds, cut_mode = self._get_cut_settings()
        target_duration = cut_duration if cut_duration and cut_duration > 0 else None
        if not target_duration:
            target_duration = self._estimate_review_target_seconds(keep_seconds, skip_seconds)
        if not target_duration or target_duration <= 0:
            target_duration = 75

        target_words = max(450, min(int(target_duration * 2.2), 4500))
        srt_context = self._read_srt_context(srt_path)
        timed_text = self._read_full_srt_timed_text_for_prompt(srt_path)
        if not timed_text:
            timed_text = (srt_context.get("timed_text") or srt_context.get("text") or "").strip()

        prompt = f"""Bạn là biên kịch recap/review phim YouTube chuyên nghiệp.

Hãy viết kịch bản voiceover tiếng Việt CÓ DẤU, mạch phim rõ đầu - giữa - cuối, bám sát thoại/phân cảnh trong SRT theo đúng thứ tự thời gian.

THÔNG TIN:
- Tên phim/nhân vật trọng tâm: {movie_name}
- Mô tả/tóm tắt hiện có: {movie_desc}
- Kiểu preview video băm: {cut_mode}
- Thời lượng voice mục tiêu: khoảng {int(target_duration)} giây
- Độ dài mục tiêu: khoảng {target_words} từ

SRT THOẠI NGUỒN CÓ TIMESTAMP:
{timed_text}

YÊU CẦU BẮT BUỘC:
1. Viết như recap review phim chuyên nghiệp, có hook mở đầu, diễn biến, cao trào, kết.
2. Không viết quá ngắn. Voice phải đủ gần thời lượng mục tiêu.
3. Khi có chi tiết đắt giá trong SRT thì câu voice phải nhắc đúng thời điểm/cảnh đó.
4. Không bỏ qua tình tiết quan trọng ở giữa/cuối phim chỉ vì phần mở đầu dễ kể hơn.
5. Không bịa cảnh ngoài SRT. Nếu SRT ít dòng, hãy viết dựa trên đúng bằng chứng đang có.
6. Không mở nhiều block bằng cùng một kiểu câu, tránh lặp "câu chuyện bắt đầu".
7. Không dùng tiếng Việt không dấu.
8. Không trả markdown, không giải thích ngoài lề.
9. Trả về DUY NHẤT JSON hợp lệ theo mẫu này:
{{
  "summary": "Tóm tắt phim ngắn gọn bằng tiếng Việt có dấu",
  "script": "Toàn bộ kịch bản voiceover hoàn chỉnh, tiếng Việt có dấu",
  "subtitle_chunks": [
    "Câu phụ đề 1 ngắn, tự nhiên",
    "Câu phụ đề 2 ngắn, tự nhiên"
  ]
}}

Tạo JSON ngay."""
        return prompt, target_duration

    def _copy_prompt_and_open_ai_studio(self, prompt: str, target_duration: float = None):
        try:
            prompt_file = self._save_manual_ai_studio_prompt_file(prompt)
            copied, method = self._copy_text_to_system_clipboard(prompt)
            if copied:
                self._thread_safe_log(
                    f"✅ Prompt đã copy vào clipboard ({method}).\n"
                )
            else:
                self._thread_safe_log(f"⚠️ Chưa copy được clipboard: {method}\n")
                self._thread_safe_log("⚠️ Prompt đã lưu ra file để copy tay.\n")
            self._thread_safe_log(f"📝 Prompt tay: {prompt_file}\n")

            gemini_url = "https://gemini.google.com/app"
            webbrowser.open(gemini_url)
            self._thread_safe_log("🌐 Đã mở Gemini Web. Đợi 5 giây rồi tự động paste + gửi...\n")

            # Auto-paste prompt vào Gemini Web
            def _auto_paste_review():
                try:
                    import pyautogui
                    import time as _t
                    pyautogui.FAILSAFE = False
                    _t.sleep(5)
                    self._copy_text_to_system_clipboard(prompt)
                    _t.sleep(0.3)
                    pyautogui.hotkey("alt", "tab")
                    _t.sleep(0.5)
                    screen_w, screen_h = pyautogui.size()
                    pyautogui.click(screen_w // 2, int(screen_h * 0.85))
                    _t.sleep(0.4)
                    pyautogui.hotkey("ctrl", "v")
                    _t.sleep(0.5)
                    pyautogui.press("enter")
                    self._thread_safe_log(
                        "🚀 Đã tự động paste + gửi prompt vào Gemini.\n"
                        "👀 Khi Gemini trả lời xong → copy toàn bộ kết quả → App tự nhận.\n"
                    )
                except ImportError:
                    self._thread_safe_log("⚠️ Paste thủ công: click Gemini → Ctrl+V → Enter.\n")
                except Exception as ae:
                    self._thread_safe_log(f"⚠️ Tự động paste lỗi ({ae}). Paste thủ công: Ctrl+V → Enter.\n")

            threading.Thread(target=_auto_paste_review, daemon=True).start()

            self.after(0, lambda: self._start_ai_studio_clipboard_watch(prompt, target_duration))
            self._thread_safe_log(
                "👀 App đang chờ clipboard: khi bạn copy kết quả từ Gemini, app sẽ tự đưa vào ô Kịch bản review và SRT tiếng Việt.\n"
            )
            return True
        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi mở Gemini web: {e}\n")
            return False

    def _handle_gemini_quota_error(self, error_message: str, cut_duration: float = None) -> str:
        """
        Handle Gemini API quota error by preparing a manual AI Studio fallback prompt.
        Returns empty string if user cancels, otherwise returns manually provided result.
        """
        self._thread_safe_log(f"\n⚠️ Gemini API quota exceeded: {error_message}\n")
        self._thread_safe_log("🌐 Chuyển sang prompt tay AI Studio. Web mở lên chỉ cần Ctrl+V.\n")
        prompt, target_duration = self._build_ai_studio_review_prompt(cut_duration)
        if prompt:
            self._copy_prompt_and_open_ai_studio(prompt, target_duration)
        return ""
    
    def run_advanced_workflow(self):
        """Advanced workflow: video -> cut -> CapCut SRT -> Vietnamese review script -> voice -> render."""
        try:
            video_input_path = self.video_path.get().strip()
            if not video_input_path:
                self.log.insert("end", "❌ Lỗi: Chưa chọn video gốc\n")
                self.log.see("end")
                return
            if not os.path.exists(video_input_path):
                self.log.insert("end", f"❌ Lỗi: File video không tồn tại: {video_input_path}\n")
                self.log.see("end")
                return
            if os.path.splitext(video_input_path)[1].lower() == ".srt":
                self._set_source_srt_path(video_input_path, auto_detected=False)
                self.video_path.delete(0, "end")
                self.log.insert("end", "❌ Bạn đang để file SRT ở ô Video gốc. Hãy chọn file video .mp4/.mkv trước.\n")
                self.log.see("end")
                return
            if not self.output_dir.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa chọn thư mục lưu video\n")
                self.log.see("end")
                return
            if not self.movie_name.get().strip():
                self.log.insert("end", "❌ Lỗi: Chưa nhập tên phim\n")
                self.log.see("end")
                return
            
            self.log.insert("end", "🚀 Bắt đầu workflow mới...\n")
            self.log.insert("end", "1) Add video và tạo preview video băm\n")
            self.log.insert("end", "2) Tạo SRT từ video GỐC bằng CapCut\n")
            self.log.insert("end", "3) Gemini tạo kịch bản review tiếng Việt từ SRT CapCut\n")
            self.log.insert("end", "4) Tạo voice khớp kịch bản và hình video đã băm\n")
            self.log.insert("end", "5) Render video + voice\n\n")
            self.log.see("end")
            
            keep_seconds, skip_seconds, cut_mode = self._get_cut_settings()
            self.log.insert("end", f"✂️ Kiểu preview: {cut_mode} | độ dài review theo lựa chọn user\n")
            self.log.see("end")
            
            preview_label, preview_minutes, max_duration = self._get_preview_duration_settings()
            
            workflow = VideoProcessingWorkflow(temp_dir=self.output_dir.get())

            input_duration = None
            try:
                input_duration = VideoCalculator.get_video_duration(self.video_path.get())
            except Exception:
                input_duration = None

            if input_duration:
                self.log.insert("end", f"ℹ️ Thời lượng video gốc: {input_duration:.1f}s\n")
                self.log.see("end")
            
            # Step 1: Cut video, or reuse a cut video prepared manually.
            existing_cut_video_path = self._find_existing_cut_video_path()
            if existing_cut_video_path:
                self.log.insert("end", "📍 BƯỚC 1: Dùng lại video băm đã có...\n")
                self.log.insert("end", f"✓ Video băm: {existing_cut_video_path}\n")
                self.log.see("end")
                cut_video_path = existing_cut_video_path
                cut_duration = VideoCalculator.get_video_duration(cut_video_path) or 0
                if cut_duration <= 0:
                    self.log.insert("end", f"❌ Không lấy được thời lượng video băm: {cut_video_path}\n")
                    self.log.see("end")
                    return
                workflow.last_keep_segments = [{
                    "start": 0.0,
                    "end": cut_duration,
                    "reason": "video băm đã có",
                    "score": 0,
                }]
                self.log.insert("end", f"✓ Thời lượng video băm: {cut_duration:.1f}s\n\n")
                self.log.see("end")
            else:
                self.log.insert("end", "📍 BƯỚC 1: Cắt video theo công thức...\n")
                self.log.see("end")

                cut_video_path = os.path.join(self.output_dir.get(), "video_cut_temp.mp4")
                success, cut_duration, error = workflow.step_1_cut_video(
                    self.video_path.get(),
                    cut_video_path,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    max_duration=max_duration,
                    smart_cut=self._is_smart_cut_mode(cut_mode),
                )

                if not success:
                    self.log.insert("end", f"❌ Lỗi cắt video: {error}\n")
                    self.log.see("end")
                    return

                self.cut_video_output_path = cut_video_path
                self.capcut_srt_output_path = self._get_capcut_srt_output_path(cut_video_path)
                self.log.insert("end", f"✓ Video đã cắt: {cut_duration:.1f}s\n\n")
                self.log.see("end")

            if hasattr(self, "srt_gen_video"):
                original_video = self.video_path.get()
                self.after(0, lambda path=original_video: (self.srt_gen_video.delete(0, "end"), self.srt_gen_video.insert(0, path)))

            # Step 2: Create/load CapCut SRT from the ORIGINAL video.
            # SRT phai giu timestamp goc de AI map lai dung canh sau khi bam/cat.
            self.log.insert("end", "📍 BƯỚC 2: Tạo SRT bằng CapCut từ video GỐC...\n")
            self.log.see("end")

            original_video = self.video_path.get().strip() if hasattr(self, "video_path") else ""
            source_srt_path = self._get_source_srt_path() or self._ensure_source_srt(prompt_if_missing=False)
            if not source_srt_path:
                source_srt_path = self._generate_source_srt_for_ai(
                    preferred_video_path=original_video,
                    force_for_video=False,
                )
            if source_srt_path:
                self.source_srt_path = source_srt_path
                if not self._same_path(source_srt_path, self._get_source_srt_path()):
                    self.after(0, lambda path=source_srt_path: self._set_source_srt_path(path, auto_detected=True))
            if not source_srt_path:
                self.log.insert("end", "❌ Dừng workflow vì chưa lấy được SRT nguồn từ video gốc.\n")
                self.log.see("end")
                return
            srt_context = self._read_srt_context(source_srt_path)
            if not srt_context["subtitle_count"]:
                self.log.insert("end", f"❌ SRT CapCut không có subtitle hợp lệ: {source_srt_path}\n")
                self.log.see("end")
                return

            self.log.insert("end", f"✓ SRT CapCut: {source_srt_path} ({srt_context['subtitle_count']} subtitle)\n\n")
            self.log.see("end")

            estimated_words = VideoCalculator.estimate_script_words(cut_duration)
            self.log.insert("end", f"ℹ️ Duration sau cut: {cut_duration:.1f}s | Ước lượng script: {estimated_words} từ\n\n")
            self.log.see("end")
            
            # Step 3: Generate Vietnamese review script from CapCut SRT
            self.log.insert("end", "📍 BƯỚC 3: Tạo kịch bản review tiếng Việt từ SRT CapCut...\n")
            self.log.see("end")
            
            story_context = self._build_story_context(
                self.movie_description.get("1.0", "end").strip(),
                srt_context,
            )
            tts_language_label = self.tts_language.get() if hasattr(self, 'tts_language') else "Tiếng Việt"
            tts_language = "Vietnamese" if self._is_vietnamese_language(tts_language_label) else "English"
            selected_voice = self._resolve_voice_id(self.voice_choice.get(), tts_language_label) if hasattr(self, 'voice_choice') else "vi-VN-HoaiMyNeural"
            if self._is_vietnamese_language(tts_language_label) and not selected_voice.startswith("vi-") and not selected_voice.startswith("piper:"):
                selected_voice = "vi-VN-HoaiMyNeural"
            character_focus = self.movie_name.get().strip()
            
            script_config = workflow.step_2_prepare_script_config(
                movie_title=self.movie_name.get(),
                movie_description=story_context,
                video_duration=cut_duration,
                character_focus=character_focus
            )
            
            self.log.insert("end", f"✓ Config: {script_config['total_estimated_words']} từ\n\n")
            self.log.see("end")
            
            try:
                ai = AIEngine(self._get_gemini_keys())
                keep_segments = getattr(workflow, "last_keep_segments", None) or VideoCutter.get_keep_segments(input_duration or 0, keep_seconds, skip_seconds)
                raw_render_blocks = VideoCalculator.get_render_blocks(keep_segments)
                book_map = self._build_voice_books_from_render_blocks(raw_render_blocks, srt_context.get("subtitles", []))
                if not book_map:
                    book_map = raw_render_blocks
                beat_plan = PremiumReviewPipeline.build_beat_plan(book_map)
                book_context = PremiumReviewPipeline.build_book_context(book_map, beat_plan)
                render_blocks = book_map
                self.log.insert(
                    "end",
                    f"✓ Book Map: {len(book_map)} book | Beat Plan: {len(beat_plan)} nhịp | từ {len(raw_render_blocks)} block video băm\n"
                )
                self.log.see("end")
                visual_context = self._build_visual_context_from_cut_video(cut_video_path, book_map)
                block_context = book_context or self._build_block_srt_context(book_map, srt_context.get("subtitles", []))
                if visual_context:
                    self.log.insert("end", "✓ Đã phân tích visual timeline của video đã băm để đưa vào prompt Gemini\n")
                    self.log.see("end")
                if block_context:
                    self.log.insert("end", "✓ Đã ghép SRT anchor theo từng book để giữ mạch voice xuyên suốt\n")
                    self.log.see("end")

                package = ai.generate_review_package(
                    movie_name=self.movie_name.get(),
                    movie_description=story_context,
                    subtitle_context=srt_context.get("text", ""),
                    timed_subtitles=srt_context.get("timed_text", ""),
                    language=tts_language,
                    target_words=script_config['total_estimated_words'],
                    target_duration_seconds=cut_duration,
                    character_focus=character_focus,
                    keep_seconds=keep_seconds,
                    skip_seconds=skip_seconds,
                    source_duration_seconds=input_duration,
                    render_blocks=book_map,
                    visual_context=visual_context,
                    block_context=block_context,
                )
                if package.get("sync_rewrite_used"):
                    self.log.insert("end", "↺ Gemini đã tự viết lại kịch bản để khớp block/timing hơn.\n")
                    self.log.see("end")
                if package.get("length_rewrite_used"):
                    self.log.insert("end", "↺ Gemini đã mở rộng kịch bản theo quota từng book để voice đủ thời lượng.\n")
                    self.log.see("end")
                summary_text = AIEngine._clean_review_script_text(package.get("summary", "").strip())
                full_script = AIEngine._clean_review_script_text(package.get("script", "").strip())
                if not full_script:
                    raise ValueError("Gemini không trả về kịch bản review")
                voice_blocks = package.get("script_blocks") or []
                voice_render_blocks = book_map or package.get("render_blocks") or []
                package["book_map"] = book_map
                package["beat_plan"] = beat_plan
                if voice_render_blocks:
                    if len(voice_blocks) != len(voice_render_blocks):
                        self.log.insert(
                            "end",
                            f"↺ Tự căn lại script_blocks theo {len(voice_render_blocks)} book map để giữ đồng bộ voice/SRT.\n",
                        )
                        self.log.see("end")
                    voice_blocks = PremiumReviewPipeline.align_script_blocks_to_book_map(
                        voice_blocks,
                        full_script,
                        voice_render_blocks,
                    )
                    voice_blocks = PremiumReviewPipeline.enforce_block_word_targets(
                        voice_blocks,
                        full_script,
                        voice_render_blocks,
                    )
                    spoken_script = AIEngine._clean_review_script_text(
                        "\n\n".join(
                            str(block.get("text", "")).strip()
                            for block in voice_blocks
                            if str(block.get("text", "")).strip()
                        )
                    )
                    if spoken_script:
                        full_script = spoken_script
                        package["script"] = full_script
                package["script_blocks"] = voice_blocks
                package["render_blocks"] = voice_render_blocks
                package["sync_report"] = PremiumReviewPipeline.validate_sync(
                    voice_blocks,
                    voice_render_blocks,
                    cut_duration,
                )
                if package["sync_report"].get("issues"):
                    self.log.insert("end", f"↺ Sync Validator: {', '.join(package['sync_report']['issues'][:4])}\n")
                    self.log.see("end")
                if package.get("quality_report", {}).get("issues"):
                    self.log.insert("end", f"↺ Story Polish: {', '.join(package['quality_report']['issues'][:4])}\n")
                    self.log.see("end")
                generated_srt = self._build_review_srt_from_script_blocks(
                    voice_blocks,
                    voice_render_blocks,
                    cut_duration,
                )
                if not generated_srt:
                    generated_srt = self._build_review_srt(package.get("subtitle_chunks", []), cut_duration)
                script_path, srt_path = self._save_review_package_assets(full_script, generated_srt)
                if summary_text:
                    self._set_textbox_text(self.movie_description, summary_text)
                self._set_textbox_text(self.review_script_box, full_script)
                self._set_textbox_text(self.review_srt_box, generated_srt)
                modular_script = full_script
                script_timeline = []
                script_from_review_package = True
                self.log.insert("end", f"✓ Gemini đã tạo review script tiếng Việt + SRT và tự thêm vào app\n")
                self.log.insert("end", f"  Script: {script_path}\n")
                self.log.insert("end", f"  SRT: {srt_path}\n\n")
                self.log.see("end")
                
            except Exception as e:
                error_msg = str(e)
                # Detect quota error
                if "429" in error_msg or "quota" in error_msg.lower() or "rate limit" in error_msg.lower():
                    self._handle_gemini_quota_error(error_msg)
                    self.log.insert("end", "\n⏸️ Workflow tạm dừng. Paste kịch bản từ AI Studio vào ô 'Kịch bản review', sau đó click 'BẮT ĐẦU RENDER VIDEO V2' để tiếp tục.\n")
                    self.log.see("end")
                else:
                    self.log.insert("end", f"❌ Lỗi AI: {error_msg}\n")
                    self.log.see("end")
                return
            
            # Validate timing before voice generation
            self.log.insert("end", "📍 Kiểm tra timing kịch bản với video đã băm...\n")
            self.log.see("end")
            
            script_timing = self._analyze_script_timing(full_script, cut_duration)
            if script_timing["action"] != "match":
                if script_timing["action"] == "increase_speed":
                    timing_hint = (
                        f"Kịch bản đang dài hơn mục tiêu khoảng {((script_timing['ratio'] - 1.0) * 100):.0f}%. "
                        "Hãy rút gọn câu, bỏ bớt vòng vo, tăng nhịp và giữ ý chính."
                    )
                else:
                    timing_hint = (
                        f"Kịch bản đang ngắn hơn mục tiêu khoảng {((1.0 - script_timing['ratio']) * 100):.0f}%. "
                        "Hệ thống sẽ làm chậm voice để bám video đã băm; nếu vẫn thiếu thời lượng thì hãy mở rộng mô tả và nhịp thoại."
                    )
                if script_from_review_package:
                    self.log.insert("end", f"↺ Timing lệch, workflow sẽ đồng bộ bằng tốc độ voice: {timing_hint}\n")
                    self.log.see("end")
                else:
                    self.log.insert("end", f"↺ Timing lệch, AI sẽ hiệu chỉnh lại: {timing_hint}\n")
                    self.log.see("end")

                    modular_script = ai.generate_script_modular(
                        self.movie_name.get(),
                        story_context,
                        keep_seconds=keep_seconds,
                        skip_seconds=skip_seconds,
                        max_duration_seconds=cut_duration,
                        target_words=script_config['total_estimated_words'],
                        character_focus=character_focus,
                        subtitle_context=srt_context.get("text", ""),
                        timing_hint=timing_hint,
                        language=tts_language,
                        target_duration_seconds=cut_duration,
                        source_duration_seconds=input_duration,
                    )
                    processor = ScriptProcessor(modular_script, keep_seconds=keep_seconds, skip_seconds=skip_seconds)
                    script_timeline = processor.generate_timeline()
                    full_script = re.sub(r"\[(KEEP|CUT)\]\s*", "", modular_script).strip()
                    script_timing = self._analyze_script_timing(full_script, cut_duration)

            voice_rate = AIEngine.calculate_tts_rate(cut_duration, script_timing["estimated_seconds"])

            timing_label = {
                "increase_speed": "tăng tốc giọng",
                "decrease_speed": "làm chậm voice để khớp video",
                "match": "giữ tốc độ",
            }[script_timing["action"]]

            self.log.insert(
                "end",
                f"✓ Script: {script_timing['words']} từ | Dự kiến: {script_timing['estimated_seconds']:.1f}s | "
                f"Mục tiêu: {cut_duration:.1f}s | Quyết định: {timing_label} ({voice_rate})\n\n"
            )
            self.log.see("end")
            
            # Step 4: Generate voice with video sync using enhanced pipeline
            self.log.insert("end", "📍 BƯỚC 4: Tạo voice khớp kịch bản và video đã băm (Enhanced Pipeline)...\n")
            self.log.see("end")
            
            try:
                if voice_blocks and voice_render_blocks and len(voice_blocks) > 1:
                    self.log.insert(
                        "end",
                        "🧩 Đã có book map: bỏ voice phẳng, chuyển sang TTS từng book để khóa đúng timeline video băm.\n"
                    )
                    self.log.see("end")
                    tts_path = None
                else:
                    voice_project_dir = os.path.join(self.output_dir.get(), "voice_output")
                    os.makedirs(voice_project_dir, exist_ok=True)
                    
                    # Initialize enhanced voice pipeline
                    voice_pipeline = EnhancedVoiceProcessingPipeline(
                        project_dir=voice_project_dir,
                        project_name=self.movie_name.get()
                    )
                    
                    # Set progress callback
                    voice_pipeline.render_plan.set_progress_callback(
                        lambda msg: self._thread_safe_log(f"🎤 {msg}\n")
                    )
                    
                    # Get voice ID
                    voice_id = self._voice_id(self.voice_choice.get()) if hasattr(self, 'voice_choice') else "vi-VN-HoaiMyNeural"
                    
                    # Process script with video sync
                    self.log.insert("end", f"🎤 Voice ID: {voice_id}\n")
                    self.log.insert("end", f"📊 Cut video duration: {cut_duration:.1f}s\n")
                    self.log.see("end")
                    
                    voice_success = voice_pipeline.process_script_with_video_sync(
                        script=full_script,
                        cut_video_duration=cut_duration,
                        voice=voice_id,
                        book_map=render_blocks if render_blocks else [],
                        min_voice_duration=cut_duration * 0.8
                    )
                    
                    if voice_success:
                        voice_outputs = voice_pipeline.get_output_files()
                        tts_path = voice_outputs['concatenated_audio']
                        
                        self.log.insert("end", voice_pipeline.get_summary())
                        self.log.insert("end", "\n")
                        self.log.see("end")
                        
                        if not os.path.exists(tts_path):
                            raise Exception(f"Voice audio not generated: {tts_path}")
                    else:
                        self.log.insert("end", "⚠️ Enhanced pipeline không thành công, chuyển sang TTS block-based...\n")
                        self.log.see("end")
                        tts_path = None  # will be set below by fallback
                    
            except Exception as e:
                self.log.insert("end", f"⚠️ Lỗi enhanced voice pipeline: {str(e)}\n")
                self.log.see("end")
                # Fallback: try block-based or traditional TTS
                self.log.insert("end", "⚠️ Sử dụng TTS truyền thống làm dự phòng...\n")
                self.log.see("end")
                tts_path = None
            # Step 4: Generate TTS (fallback - only runs if enhanced pipeline failed)
            if tts_path and os.path.exists(tts_path):
                self.log.insert("end", "✅ Đã có voice từ enhanced pipeline, bỏ qua TTS fallback.\n")
                self.log.see("end")
            else:
            # Step 4 fallback TTS
                self.log.insert("end", "📍 BƯỚC 4 (fallback): Tạo voice khớp kịch bản và video đã băm...\n")
                self.log.see("end")
            
            try:
                if tts_path and os.path.exists(tts_path):
                    raise StopIteration  # skip fallback, already have tts from enhanced pipeline
                output_dir = self.output_dir.get().strip() if hasattr(self, "output_dir") else ""
                if not output_dir:
                    output_dir = os.getcwd()
                os.makedirs(output_dir, exist_ok=True)
                tts_path = os.path.abspath(os.path.join(output_dir, "temp_v2_advanced.mp3"))
                tts_clean_script = AIEngine._clean_tts_text(full_script)
                script_seconds = len(tts_clean_script.split()) / 2.5 if tts_clean_script else 0
                voice_rate = AIEngine.calculate_tts_rate(cut_duration, script_seconds)
                self.log.insert("end", f"🎙️ Voice: {tts_language_label}, {selected_voice}, {voice_rate}\n")
                self.log.see("end")
                last_voice_progress = {"value": None}

                def _voice_progress(percent, done=None, total=None):
                    if percent == last_voice_progress["value"]:
                        return
                    last_voice_progress["value"] = percent
                    self._thread_safe_log(f"⏳ Tạo voice: {percent}%\n")

                script_blocks = voice_blocks or []
                render_blocks = voice_render_blocks or []
                render_block_map = {}
                for idx, render_block in enumerate(render_blocks, 1):
                    if not isinstance(render_block, dict):
                        continue
                    for key in (render_block.get("block_id"), render_block.get("book_id"), idx):
                        try:
                            render_block_map[int(key)] = render_block
                        except Exception:
                            continue
                if render_blocks:
                    if len(script_blocks) != len(render_blocks):
                        self.log.insert("end", f"↺ Đã đồng bộ lại {len(script_blocks)} script block theo {len(render_blocks)} book map.\n")
                        self.log.see("end")
                    script_blocks = PremiumReviewPipeline.align_script_blocks_to_book_map(
                        script_blocks,
                        full_script,
                        render_blocks,
                    )
                    script_blocks = PremiumReviewPipeline.enforce_block_word_targets(
                        script_blocks,
                        full_script,
                        render_blocks,
                    )
                    package["script_blocks"] = script_blocks
                    spoken_script = AIEngine._clean_review_script_text(
                        "\n\n".join(
                            str(block.get("text", "")).strip()
                            for block in script_blocks
                            if str(block.get("text", "")).strip()
                        )
                    )
                    if spoken_script:
                        full_script = spoken_script
                        package["script"] = full_script
                try:
                    from core.vietnamese_text import VietnameseTextGuard
                    vi_report = VietnameseTextGuard.report_blocks(script_blocks)
                    if vi_report.get("needs_repair") or VietnameseTextGuard.needs_diacritic_repair(full_script):
                        self.log.insert(
                            "end",
                            f"↺ Sửa dấu tiếng Việt trước khi tạo voice ({vi_report.get('weak_block_count', 0)} block)...\n",
                        )
                        self.log.see("end")
                        package, vi_report = ai.repair_vietnamese_diacritics_package(package, render_blocks)
                        if vi_report.get("needs_repair"):
                            raise RuntimeError("Kịch bản vẫn thiếu dấu tiếng Việt sau khi sửa")
                        script_blocks = package.get("script_blocks") or script_blocks
                        full_script = package.get("script") or full_script
                except Exception as vi_error:
                    raise RuntimeError(f"Không sửa được dấu tiếng Việt trước TTS: {vi_error}")
                if script_blocks and len(script_blocks) > 1:
                    self.log.insert("end", "🧩 Tạo giọng theo book và khóa audio vào timeline video băm...\n")
                    self.log.see("end")
                    block_paths = []
                    block_starts = []
                    block_durations = []
                    for block_index, block in enumerate(script_blocks, 1):
                        text = block.get("text", "").strip()
                        if not text:
                            continue
                        block_id = block.get("block_id", 0)
                        try:
                            block_id_int = int(block_id)
                        except Exception:
                            block_id_int = block_index
                        render_block = render_block_map.get(block_id_int) or (
                            render_blocks[block_index - 1] if block_index - 1 < len(render_blocks) else {}
                        )
                        scene_role = render_block.get("scene_role_label") or render_block.get("scene_role") or ""
                        start_time = float(
                            render_block.get("start_in_final_video")
                            or block.get("render_start")
                            or 0.0
                        )
                        target_block_seconds = float(
                            block.get("duration_hint_seconds")
                            or block.get("render_duration")
                            or render_block.get("duration", 0.0)
                            or 0.0
                        )
                        if target_block_seconds <= 0:
                            try:
                                render_end = float(render_block.get("end_in_final_video") or block.get("render_end") or 0.0)
                                if render_end > start_time:
                                    target_block_seconds = render_end - start_time
                            except Exception:
                                target_block_seconds = 0.0
                        tts_clean_text = AIEngine._clean_tts_text(text)
                        speakable_words = AIEngine._speakable_word_count(tts_clean_text)
                        if speakable_words < 1:
                            self._thread_safe_log(
                                f"   Block {block_id}{f' [{scene_role}]' if scene_role else ''}: "
                                "bỏ qua vì không còn nội dung đọc sau khi clean\n"
                            )
                            continue
                        block_words = len(tts_clean_text.split())
                        block_estimated_seconds = block_words / 2.5 if block_words else 0.0
                        block_rate = AIEngine.calculate_tts_rate(
                            target_block_seconds,
                            block_estimated_seconds,
                        ) if target_block_seconds else voice_rate
                        pace = str(block.get("pace") or "").strip().lower()
                        if pace in {"slow", "calm", "fast", "urgent"}:
                            block_rate = AIEngine._pace_rate(pace, block_rate)
                        block_path = tempfile.NamedTemporaryFile(suffix=f"_block_{block_id}.mp3", delete=False).name
                        block_paths.append(block_path)
                        block_starts.append(start_time)
                        block_durations.append(target_block_seconds if target_block_seconds > 0 else None)
                        self._thread_safe_log(
                            f"   Block {block_id}{f' [{scene_role}]' if scene_role else ''}: {block_words} từ, mục tiêu {target_block_seconds:.1f}s, pace {pace or 'normal'}, rate {block_rate}\n"
                        )
                        asyncio.run(ai.text_to_speech(text, block_path, voice=selected_voice, rate=block_rate))

                    if block_paths:
                        if os.path.exists(tts_path):
                            try:
                                os.remove(tts_path)
                            except Exception:
                                pass
                        VideoEngine.assemble_block_audio(
                            block_paths,
                            block_starts,
                            tts_path,
                            block_durations=block_durations,
                            total_duration=cut_duration,
                        )
                        for block_path in block_paths:
                            try:
                                if os.path.exists(block_path):
                                    os.unlink(block_path)
                            except Exception:
                                pass
                        self.log.insert("end", "✓ Voice đã được ghép từ nhiều block\n")
                    else:
                        raise ValueError("Không có block audio nào để tạo voice")
                else:
                    chosen_voice = asyncio.run(
                        ai.text_to_speech(
                            full_script,
                            tts_path,
                            voice=selected_voice,
                            rate=voice_rate,
                            progress_callback=_voice_progress,
                        )
                    )
                    self.log.insert("end", f"✓ Voice: {chosen_voice} | Rate: {voice_rate}\n\n")
                self.log.see("end")
            except StopIteration:
                pass  # enhanced pipeline already produced tts_path, skip fallback
            except Exception as e:
                self.log.insert("end", f"❌ Lỗi TTS: {str(e)}\n")
                self.log.see("end")
                return
            self.log.insert("end", "📍 BƯỚC 5: Render video + voice...\n")
            self.log.see("end")
            
            try:
                out = os.path.join(self.output_dir.get(), "recap_advanced_final.mp4")
                
                ve = VideoEngine()
                header_pos, footer_pos = self._get_overlay_positions()
                success, video_error = ve.process_video_v2(
                    cut_video_path, out, tts_path, self.bgm_path.get(),
                    self.header_text.get(), self.footer_text.get(),
                    1, 0, cut_duration,
                    self.header_font_size, self.footer_font_size,
                    self.header_color, self.footer_color,
                    None,
                    self.header_bar_color,
                    self.footer_bar_color,
                    header_pos,
                    footer_pos,
                )
                
                if success:
                    self.log.insert("end", f"✨ XONG! {out}\n")
                    self.log.insert("end", "\n" + workflow.get_workflow_summary() + "\n")
                else:
                    self.log.insert("end", f"❌ Lỗi: {video_error}\n")
                self.log.see("end")
                
            except Exception as e:
                self.log.insert("end", f"❌ Lỗi render: {str(e)}\n")
                self.log.see("end")
                
        except Exception as e:
            self.log.insert("end", f"❌ Lỗi: {str(e)}\n")
            self.log.see("end")

    def load_config(self):
        if not hasattr(self, "config_manager") or self.config_manager is None:
            self.config_manager = ConfigManager()
        return self.config_manager.load()

    def save_config(self, data: dict):
        if not hasattr(self, "config_manager") or self.config_manager is None:
            self.config_manager = ConfigManager()
        return self.config_manager.save(data)

    def _review_style_choice_pairs(self):
        preferred = [
            ("Review YouTube chuyên nghiệp", "professional_youtube_movie_recap"),
            ("Tình cảm lãng mạn", "romantic_emotional_recap"),
            ("Kinh dị căng thẳng", "horror_suspense_recap"),
            ("Hài hước duyên dáng", "comedy_witty_recap"),
            ("Hành động dồn dập", "action_high_energy_recap"),
            ("Kinh dị + hài + hành động", "horror_comedy_action_mix"),
            ("Điện ảnh căng thẳng", "cinematic_suspense"),
            ("Phân tích vụ án", "detective_case_analysis"),
            ("Cổ trang quyền mưu", "historical_palace_intrigue"),
            ("Tâm lý cảm xúc", "emotional_drama"),
            ("Hook nhanh giữ chân", "fast_hook_shorts"),
            ("Giải thích mạch lạc", "calm_explainer"),
        ]
        known = set()
        pairs = []
        for label, key in preferred:
            norm = normalize_review_style(key)
            if norm in known:
                continue
            known.add(norm)
            pairs.append((label, norm))
        try:
            for key, label in available_review_styles().items():
                norm = normalize_review_style(key)
                if norm not in known:
                    known.add(norm)
                    pairs.append((label, norm))
        except Exception:
            pass
        return pairs

    def _setup_review_style_selector(self):
        self._review_style_pairs = self._review_style_choice_pairs()
        self._review_style_label_to_key = {label: key for label, key in self._review_style_pairs}
        self._review_style_key_to_label = {key: label for label, key in self._review_style_pairs}
        frame = ctk.CTkFrame(self.container, fg_color="transparent")
        frame.pack(fill="x", pady=2)
        ctk.CTkLabel(frame, text="Kiểu AI review:", width=120, anchor="w").pack(side="left")
        self.review_style = ctk.CTkComboBox(
            frame,
            values=[label for label, _key in self._review_style_pairs],
            state="readonly",
            width=250,
            command=self._on_review_style_change,
        )
        self.review_style.pack(side="left", padx=5)
        self._set_review_style(os.environ.get("AUTORECAP_REVIEW_STYLE") or "professional_youtube_movie_recap", save=False)

    def _selected_review_style_key(self) -> str:
        if not hasattr(self, "review_style"):
            return normalize_review_style(os.environ.get("AUTORECAP_REVIEW_STYLE"))
        label = self.review_style.get()
        return normalize_review_style(self._review_style_label_to_key.get(label, label))

    def _set_review_style(self, style_key: str, save: bool = True):
        key = normalize_review_style(style_key)
        os.environ["AUTORECAP_REVIEW_STYLE"] = key
        if hasattr(self, "review_style"):
            label = self._review_style_key_to_label.get(key)
            if label:
                self.review_style.set(label)
        if save:
            self.save_config({"review_style": key})
        return key

    def _on_review_style_change(self, value=None):
        key = self._set_review_style(self._review_style_label_to_key.get(str(value or ""), value), save=True)
        try:
            self._thread_safe_log(f"🎭 Kiểu AI review: {self._review_style_key_to_label.get(key, key)}\n")
        except Exception:
            pass

    
    def _selected_capcut_srt_mode(self):
        if not hasattr(self, "capcut_srt_mode"):
            return "auto"
        return "manual" if self.capcut_srt_mode.get() == self.CAPCUT_SRT_MANUAL else "auto"

    def _capcut_button_text(self, mode=None):
        mode = mode or self._selected_capcut_srt_mode()
        return "🎬 MỞ CAPCUT TẠO SRT" if mode == "manual" else "⚡ AUTO CAPCUT TẠO SRT"

    def _on_capcut_srt_mode_change(self, value=None, save=True):
        mode = "manual" if value == self.CAPCUT_SRT_MANUAL else "auto"
        if hasattr(self, "btn_capcut_srt"):
            self.btn_capcut_srt.configure(text=self._capcut_button_text(mode))
        if hasattr(self, "capcut_srt_mode_hint"):
            hint = (
                "App mở đúng video nguồn; tạo Auto Caption rồi đóng CapCut."
                if mode == "manual"
                else "Nhận dạng nền, không mở cửa sổ CapCut."
            )
            self.capcut_srt_mode_hint.configure(text=hint)
        if save:
            self.save_config({"capcut_srt_mode": mode})
        return mode

    def start_capcut_workflow_thread(self):
        """Start CapCut workflow in thread"""
        mode = self._selected_capcut_srt_mode()
        thread = threading.Thread(target=self._capcut_workflow_worker, args=(mode,), daemon=True)
        thread.start()
    
    def _capcut_workflow_worker(self, mode="auto"):
        """Worker to launch CapCut and extract SRT from ORIGINAL video (not cut)."""
        try:
            # Lấy video từ ô "Video tạo SRT" hoặc ô video gốc
            video_path = self.srt_gen_video.get().strip() if hasattr(self, "srt_gen_video") else ""
            if not video_path:
                video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""

            original_video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
            if video_path and self._is_cut_video_output_path(video_path) and original_video_path:
                self._thread_safe_log("[CAPCUT] Phat hien video bam -> doi ve video goc de lay SRT dung timestamp.\n")
                video_path = original_video_path

            if not video_path or not os.path.exists(video_path):
                self._thread_safe_log("❌ Vui lòng chọn video hợp lệ\n")
                return

            # KHÔNG băm video — đưa thẳng video GỐC vào CapCut
            # SRT sẽ có timestamp gốc để AI map đúng cảnh sau khi băm
            self._thread_safe_log(f"📹 Tạo SRT từ video GỐC: {os.path.basename(video_path)}\n")

            # Đặt SRT output cạnh video gốc
            video_base = os.path.splitext(video_path)[0]
            srt_output = video_base + "_capcut.srt"
            self.capcut_srt_output_path = srt_output

            # Nut CapCut la thao tac tao SRT goc moi cho dung video hien tai.
            # Xoa cache cu cung ten de khong tai su dung noi dung cua lan truoc.
            self._delete_capcut_srt_cache(srt_output)

            def _clear_previous_srt_from_ui():
                self.source_srt_path = ""
                if hasattr(self, "srt_path"):
                    self.srt_path.delete(0, "end")

            self.after(0, _clear_previous_srt_from_ui)
            
            self.after(0, lambda: self.btn_capcut_srt.configure(state="disabled", text="⏳ ĐANG TẠO SRT..."))

            if mode == "auto":
                self._thread_safe_log("⚡ Auto CapCut: nhận dạng phụ đề trong nền...\n")
                try:
                    from engine.capcut_asr import CapCutDirectASR

                    recognizer = CapCutDirectASR(
                        progress_callback=lambda msg, pct: self._thread_safe_log(
                            f"   Auto CapCut {pct}%: {msg}\n"
                        )
                    )
                    cue_count = recognizer.transcribe(video_path, srt_output)
                    self._write_capcut_srt_meta(srt_output, video_path)

                    def _auto_success():
                        self.btn_capcut_srt.configure(
                            state="normal", text=self._capcut_button_text("auto")
                        )
                        self._set_source_srt_path(srt_output, auto_detected=True)
                        self._thread_safe_log(
                            f"✅ Auto CapCut hoàn tất: {cue_count} dòng → {srt_output}\n"
                        )

                    self.after(0, _auto_success)
                except Exception as auto_error:
                    auto_error_text = str(auto_error)
                    def _auto_failed():
                        self.btn_capcut_srt.configure(
                            state="normal", text=self._capcut_button_text("auto")
                        )
                        self._thread_safe_log(
                            f"❌ Auto CapCut lỗi: {auto_error_text}\n"
                            "ℹ️ Chọn 'Thủ công (mở CapCut)' để app mở đúng video và lấy Auto Caption.\n"
                        )

                    self.after(0, _auto_failed)
                return
            
            self._thread_safe_log("🎬 Khởi động CapCut...\n")
            self._thread_safe_log("📝 Vui lòng dùng chức năng tự động chú thích (Auto Caption)\n")
            self._thread_safe_log("💾 Khi hoàn tất, thoát CapCut để lấy SRT\n\n")
            
            # Create CapCut integration
            def progress_callback(msg):
                self._thread_safe_log(f"   {msg}\n")
            
            capcut = CapCutIntegration(progress_callback=progress_callback)
            
            # Run workflow: open CapCut → wait for exit → extract SRT
            def workflow_callback(success):
                def update_ui():
                    self.btn_capcut_srt.configure(
                        state="normal", text=self._capcut_button_text("manual")
                    )
                    
                    if success:
                        self._thread_safe_log(f"✅ SRT từ CapCut: {srt_output}\n")
                        self._write_capcut_srt_meta(srt_output, video_path)
                        self._set_source_srt_path(srt_output, auto_detected=True)
                        self._thread_safe_log(
                            "ℹ️ CapCut chỉ lấy SRT gốc; app không tự gọi Gemini dịch ở bước này.\n"
                        )
                    else:
                        self._thread_safe_log("❌ Không thể lấy SRT từ CapCut\n")
                
                self.after(0, update_ui)
            
            capcut.monitor_and_extract_srt(
                video_path=video_path,
                output_srt=srt_output,
                on_complete=workflow_callback
            )
        
        except Exception as e:
            error_text = str(e)
            def update_ui():
                self.btn_capcut_srt.configure(
                    state="normal", text=self._capcut_button_text(mode)
                )
                self._thread_safe_log(f"❌ Lỗi CapCut: {error_text}\n")
            self.after(0, update_ui)
    
    def start_auto_workflow_thread(self, srt_path: str):
        """Start auto-workflow (SRT→Translation→Script) in thread"""
        thread = threading.Thread(
            target=self._auto_workflow_worker,
            args=(srt_path,),
            daemon=True
        )
        thread.start()
    
    def _auto_workflow_worker(self, srt_path: str):
        """Worker to translate SRT and generate script automatically"""
        try:
            
            movie_name = self.movie_name.get().strip()
            if not movie_name:
                self._thread_safe_log("❌ Lỗi: Chưa nhập tên phim\n")
                return
            
            if not os.path.exists(srt_path):
                self._thread_safe_log(f"❌ Lỗi: File SRT không tồn tại: {srt_path}\n")
                return
            
            output_dir = self.output_dir.get().strip() or os.path.dirname(srt_path)
            base_name = os.path.splitext(os.path.basename(srt_path))[0]
            script_output = os.path.join(output_dir, f"{base_name}_script.txt")
            srt_translated_output = os.path.join(output_dir, f"{base_name}_translated.srt")
            
            # Get movie description
            movie_description = self.movie_description.get("1.0", "end").strip()
            
            # Get TTS language
            tts_language_label = self.tts_language.get() if hasattr(self, "tts_language") else "Tiếng Việt"
            tts_language = "Vietnamese" if tts_language_label == "Tiếng Việt" else "English"
            
            # Get target words
            keep_seconds, skip_seconds, _ = self._get_cut_settings()
            target_seconds = self._estimate_review_target_seconds(keep_seconds, skip_seconds)
            target_words = int(target_seconds * 2.2) if target_seconds else 900
            target_words = max(450, min(target_words, 4500))
            
            self._thread_safe_log(f"🧠 Gemini đang dịch + tạo kịch bản từ SRT...\n")
            
            # Create auto workflow handler
            def progress_callback(msg):
                self._thread_safe_log(f"   {msg}\n")
            
            def completion_callback(success, script_path, srt_path_result):
                if success:
                    # Load into UI
                    def update_ui():
                        try:
                            with open(script_path, 'r', encoding='utf-8') as f:
                                script_text = f.read()
                            self._set_textbox_text(self.review_script_box, script_text)
                            
                            with open(srt_path_result, 'r', encoding='utf-8') as f:
                                srt_text = f.read()
                            self._set_textbox_text(self.review_srt_box, srt_text)
                            
                            self._thread_safe_log(f"✅ Đã tạo kịch bản + SRT!\n")
                            self._thread_safe_log(f"📄 Script: {script_path}\n")
                            self._thread_safe_log(f"📄 SRT: {srt_path_result}\n")
                        except:
                            pass
                    
                    self.after(0, update_ui)
                else:
                    self._thread_safe_log(f"❌ Lỗi tạo kịch bản: {srt_path_result}\n")
            
            handler = AutoWorkflowHandler(progress_callback=progress_callback)
            handler.auto_translate_srt_and_generate_script(
                srt_path=srt_path,
                gemini_keys=self._get_gemini_keys(),
                movie_name=movie_name,
                movie_description=movie_description,
                output_script_path=script_output,
                output_srt_path=srt_translated_output,
                tts_language=tts_language,
                target_words=target_words,
                keep_seconds=keep_seconds,
                skip_seconds=skip_seconds,
                source_duration_seconds=self._get_source_video_duration(),
                on_progress=progress_callback,
                on_complete=completion_callback
            )
        
        except Exception as e:
            self._thread_safe_log(f"❌ Lỗi auto-workflow: {str(e)}\n")

    # ──────────────────────────────────────────────────────────────
    # FULL PIPELINE - 10 bước tự động
    # ──────────────────────────────────────────────────────────────

    def _start_full_pipeline_thread(self):
        """Khởi chạy Full Pipeline - có thể gọi trực tiếp từ thread hoặc từ UI."""
        video_path = self.video_path.get().strip() if hasattr(self, "video_path") else ""
        if not video_path or not os.path.exists(video_path):
            self._thread_safe_log("❌ Vui lòng chọn video gốc trước khi chạy Full Pipeline.\n")
            return

        output_dir = self._ensure_video_output_dir(video_path, force_new=False, update_entries=True)
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(video_path), "full_pipeline_output")

        # Reset step labels
        def reset_labels():
            for lbl in self._fp_step_labels.values():
                lbl.configure(text="⏳", text_color="gray")
        self.after(0, reset_labels)

        # Disable button if running from UI click (not from start_thread)
        import inspect
        caller = inspect.stack()[1].function
        if caller not in ("start_thread",):
            self.after(0, lambda: self.btn_full_pipeline.configure(
                state="disabled", text="⏳ Đang xử lý..."))

        self._full_pipeline_worker(video_path, output_dir)

    def _full_pipeline_worker(self, video_path: str, output_dir: str):
        """Worker thread cho Full Pipeline."""
        # Disable run button to prevent double execution
        self.after(0, lambda: self.btn_run.configure(
            state="disabled", text="⏳ Full Pipeline đang chạy..."))
        self.after(0, lambda: self.btn_full_pipeline.configure(
            state="disabled", text="⏳ Đang xử lý..."))
        STATUS_COLORS = {
            "running":  "#f59e0b",
            "done":     "#22c55e",
            "failed":   "#ef4444",
            "skipped":  "#6b7280",
            "pending":  "gray",
        }
        STATUS_ICONS = {
            "running":  "▶️",
            "done":     "✅",
            "failed":   "❌",
            "skipped":  "⏭️",
            "pending":  "⏳",
        }

        def on_step(name: str, status: str):
            """Update step label in UI thread."""
            icon = STATUS_ICONS.get(status, "⏳")
            color = STATUS_COLORS.get(status, "gray")
            if name in self._fp_step_labels:
                self.after(0, lambda n=name, ic=icon, c=color:
                           self._fp_step_labels[n].configure(text=ic, text_color=c))

        def log(msg: str):
            self._thread_safe_log(f"{msg}\n")

        try:
            # Collect settings from UI
            movie_title       = self.movie_name.get().strip() if hasattr(self, "movie_name") else ""
            movie_description = self._get_textbox_text(self.movie_description) if hasattr(self, "movie_description") else ""
            review_style      = self._set_review_style(self._selected_review_style_key(), save=True)
            voice_id          = self._voice_id(self.voice_choice.get()) if hasattr(self, "voice_choice") else "vi-VN-HoaiMyNeural"
            keep_s, skip_s, _ = self._get_cut_settings()
            preview_label, max_min, max_seconds = self._get_preview_duration_settings()
            capcut_srt_mode = self._selected_capcut_srt_mode()
            os.environ["AUTORECAP_CAPCUT_SRT_MODE"] = capcut_srt_mode
            os.environ["AUTORECAP_TARGET_REVIEW_LABEL"] = preview_label
            if max_min:
                os.environ["AUTORECAP_TARGET_REVIEW_MINUTES"] = str(max_min)
                os.environ["AUTORECAP_TARGET_REVIEW_SECONDS"] = str(max_seconds or max_min * 60.0)
            else:
                os.environ.pop("AUTORECAP_TARGET_REVIEW_MINUTES", None)
                os.environ.pop("AUTORECAP_TARGET_REVIEW_SECONDS", None)
            gemini_keys = self._get_gemini_keys()
            api_key = "\n".join(gemini_keys) if gemini_keys else ""
            source_srt = self.source_srt_path if hasattr(self, "source_srt_path") else ""
            # Also check the srt_path entry field
            if not source_srt and hasattr(self, "srt_path"):
                source_srt = self.srt_path.get().strip()
            if source_srt and os.path.exists(source_srt):
                source_srt = self._ensure_vietnamese_source_srt(source_srt) or source_srt
            smart = getattr(self, "_fp_smart_cut", True)
            log(f"   🎭 Kiểu AI review: {self._review_style_key_to_label.get(review_style, review_style)}")
            log(f"   ⏱️ Ngân sách review: {preview_label} -> AI chia ngân sách chapter; video đi theo voice thật")
            log(
                "   🎬 Chế độ SRT CapCut: "
                + ("Thủ công (mở CapCut)" if capcut_srt_mode == "manual" else "Tự động chạy nền")
            )

            pipeline = FullPipeline(
                video_path=video_path,
                output_dir=output_dir,
                movie_title=movie_title,
                movie_description=movie_description,
                voice=voice_id,
                keep_seconds=keep_s,
                skip_seconds=skip_s,
                max_video_minutes=max_min,
                smart_cut=smart,
                gemini_api_key=api_key,
                source_srt_path=source_srt,
                bg_music_path=getattr(self, "bgm_path", None) and self.bgm_path.get().strip() or "",
                header_text=self.header_text.get().strip() if hasattr(self, "header_text") else "",
                footer_text=self.footer_text.get().strip() if hasattr(self, "footer_text") else "",
                header_color=getattr(self, "header_color", (255, 255, 0)),
                footer_color=getattr(self, "footer_color", (255, 255, 255)),
                header_bar_color=getattr(self, "header_bar_color", (255, 0, 0)),
                footer_bar_color=getattr(self, "footer_bar_color", (0, 174, 255)),
                header_font_size=getattr(self, "header_font_size", 80),
                footer_font_size=getattr(self, "footer_font_size", 60),
                header_pos=getattr(self, "_get_overlay_positions", lambda: ({"x":0.5,"y":0.12}, {"x":0.5,"y":0.88}))()[0],
                footer_pos=getattr(self, "_get_overlay_positions", lambda: ({"x":0.5,"y":0.12}, {"x":0.5,"y":0.88}))()[1],
                review_style=review_style,
                script_review_callback=self._make_script_review_callback(),
                progress_callback=log,
                step_callback=on_step,
            )

            # Nếu chưa có SRT gốc → gọi CapCut lấy SRT từ video GỐC (chưa băm)
            # Đây là SRT chứa timestamp gốc để AI map thoại đúng cảnh
            if (
                (not source_srt or not os.path.exists(source_srt))
                and os.getenv("AUTORECAP_CAPCUT_DESKTOP_PREFLIGHT", "0").strip() == "1"
            ):
                log("   📌 Chưa có SRT gốc — gọi CapCut để tạo SRT từ video GỐC...\n")
                try:
                    from core.capcut_bridge import CapCutIntegration
                    # Đặt SRT output cạnh video gốc
                    video_base = os.path.splitext(video_path)[0]
                    srt_target = video_base + "_capcut.srt"

                    # Kiểm tra đã có chưa
                    if (
                        os.path.exists(srt_target)
                        and os.path.getsize(srt_target) > 0
                        and self._capcut_srt_matches_video(srt_target, video_path)
                    ):
                        log(f"   ✅ Dùng SRT gốc đã có: {os.path.basename(srt_target)}\n")
                        source_for_ai = self._ensure_vietnamese_source_srt(srt_target) or srt_target
                        pipeline.source_srt_path = source_for_ai
                        pipeline.transcript_srt   = source_for_ai
                    else:
                        if os.path.exists(srt_target):
                            log("   ⚠️ SRT CapCut cũ không khớp video hiện tại; tạo lại từ đúng project.\n")
                            self._delete_capcut_srt_cache(srt_target)
                        capcut = CapCutIntegration(
                            progress_callback=lambda msg: log(f"   CapCut: {msg}\n")
                        )
                        # Cập nhật ô Video tạo SRT = video GỐC
                        def _set_srt_gen_video():
                            if hasattr(self, "srt_gen_video"):
                                self.srt_gen_video.delete(0, "end")
                                self.srt_gen_video.insert(0, video_path)
                        self.after(0, _set_srt_gen_video)

                        success = capcut.monitor_and_extract_srt(video_path, srt_target)
                        if success and os.path.exists(srt_target):
                            self._write_capcut_srt_meta(srt_target, video_path)
                            log(f"   ✅ CapCut tạo SRT gốc: {os.path.basename(srt_target)}\n")
                            source_for_ai = self._ensure_vietnamese_source_srt(srt_target) or srt_target
                            pipeline.source_srt_path = source_for_ai
                            pipeline.transcript_srt   = source_for_ai
                            # Cập nhật ô SRT nguồn trên UI
                            def _update_srt_ui():
                                if hasattr(self, "srt_path"):
                                    self.srt_path.delete(0, "end")
                                    self.srt_path.insert(0, source_for_ai)
                                self.source_srt_path = source_for_ai
                            self.after(0, _update_srt_ui)
                        else:
                            log("   ⚠️  CapCut không trả về SRT — AI sẽ generate kịch bản không có thoại gốc\n")
                except Exception as _e:
                    log(f"   ⚠️  Lỗi gọi CapCut: {_e} — tiếp tục không có SRT\n")

            elif not source_srt or not os.path.exists(source_srt):
                log(
                    "   🎙️ Chưa có SRT nguồn — TRANSCRIPT sẽ nhận dạng bằng CapCut. "
                    "Nếu dịch vụ nền không phản hồi, pipeline sẽ dừng để bạn dùng nút "
                    "MỞ CAPCUT TẠO SRT; Whisper không tự chạy.\n"
                )

            skip_transcript = self._fp_skip_transcript.get() if hasattr(self, "_fp_skip_transcript") else False
            skip_scene      = self._fp_skip_scene.get()      if hasattr(self, "_fp_skip_scene")      else False
            skip_kf         = self._fp_skip_kf.get()         if hasattr(self, "_fp_skip_kf")         else False

            success = pipeline.run(
                skip_transcript=skip_transcript,
                skip_scene_detect=skip_scene,
                skip_keyframes=skip_kf,
            )

            outputs = pipeline.get_outputs()

            def finish():
                # Re-enable both buttons
                self.btn_full_pipeline.configure(
                    state="normal",
                    text="🚀 CHẠY FULL PIPELINE (10 BƯỚC)"
                )
                if hasattr(self, "btn_run"):
                    self.btn_run.configure(state="normal")
                if success:
                    # ── Tự động điền kết quả vào các ô workflow ──

                    # 1. SRT transcript → ô "SRT thoại nguồn"
                    transcript = outputs.get("transcript_srt", "")
                    if transcript and os.path.exists(transcript):
                        if hasattr(self, "srt_path"):
                            self.srt_path.delete(0, "end")
                            self.srt_path.insert(0, transcript)
                        self.source_srt_path = transcript
                        self._thread_safe_log(f"📋 SRT thoại → ô SRT nguồn: {os.path.basename(transcript)}\n")

                    # 2. Cut video → dùng cho render tiếp theo
                    cut_video = outputs.get("cut_video", "")
                    if cut_video and os.path.exists(cut_video):
                        # Lưu vào cut_output_dir nếu có
                        if hasattr(self, "cut_output_dir"):
                            self.cut_output_dir.delete(0, "end")
                            self.cut_output_dir.insert(0, os.path.dirname(cut_video))
                        self._thread_safe_log(f"🎥 Video băm: {os.path.basename(cut_video)}\n")

                    # 3. Voice SRT → ô review SRT
                    voice_srt = outputs.get("voice_srt", "")
                    if voice_srt and os.path.exists(voice_srt):
                        try:
                            srt_text = open(voice_srt, encoding="utf-8").read()
                            if hasattr(self, "review_srt_box"):
                                self._set_textbox_text(self.review_srt_box, srt_text)
                            self._thread_safe_log(f"📝 Phụ đề voice → ô SRT review\n")
                        except Exception:
                            pass

                    # 4. AI package script → ô kịch bản review
                    ai_pkg = outputs.get("ai_package", "")
                    if ai_pkg and os.path.exists(ai_pkg):
                        try:
                            pkg = json.load(open(ai_pkg, encoding="utf-8"))
                            script_text = pkg.get("script", "")
                            if not script_text:
                                blocks = pkg.get("script_blocks", [])
                                script_text = "\n\n".join(
                                    b.get("text", "") for b in blocks if b.get("text")
                                )
                            if script_text and hasattr(self, "review_script_box"):
                                self._set_textbox_text(self.review_script_box, script_text)
                                self._thread_safe_log(f"📄 Kịch bản AI → ô kịch bản review\n")
                        except Exception:
                            pass

                    self._thread_safe_log(
                        f"\n✅ FULL PIPELINE HOÀN TẤT! Output: {output_dir}\n"
                    )
                    final_video = outputs.get("final_video", "")
                    messagebox.showinfo(
                        "Full Pipeline hoàn tất ✅",
                        f"Đã hoàn tất 11 bước tự động!\n\n"
                        f"📁 Output: {output_dir}\n"
                        f"🎬 Video cuối: {os.path.basename(final_video) if final_video else 'N/A'}\n"
                        f"✂️  Video băm: {os.path.basename(outputs.get('cut_video','N/A'))}\n"
                        f"🎤 Voice track: {os.path.basename(outputs.get('voice_track','N/A'))}\n"
                        f"📝 Phụ đề: {os.path.basename(outputs.get('voice_srt','N/A'))}\n\n"
                        "Các ô SRT, kịch bản đã được tự động điền."
                    )
                else:
                    messagebox.showerror(
                        "Full Pipeline thất bại",
                        "Có bước bị lỗi. Xem log để biết chi tiết."
                    )

            self.after(0, finish)

        except Exception as e:
            import traceback
            err = traceback.format_exc()
            self._thread_safe_log(f"❌ Full Pipeline exception: {str(e)}\n{err}\n")
            self.after(0, lambda: self.btn_full_pipeline.configure(
                state="normal", text="🚀 CHẠY FULL PIPELINE (10 BƯỚC)"))

    def update_license_display(self, result=None):
        """License display — đã tắt hệ thống cũ, ẩn label nếu có."""
        label = getattr(self, "license_status_label", None)
        if label is None:
            return
        try:
            label.configure(text="", text_color="#22c55e")
        except Exception:
            pass


def run_app():
    app = App()

    # ── License check đã tắt → mở app thẳng ─────────────────────────────────
    def _check_license_after_start():
        app.update_license_display(None)
        app.deiconify()
        _start_update_check()

    def _start_update_check():
        """Kiểm tra update ngầm — không block UI, hiện dialog nếu có bản mới."""
        try:
            from engine.updater import check_update_async, show_update_dialog

            def on_update_result(result):
                if result:
                    # Chạy trên main thread qua after()
                    app.after(0, lambda: show_update_dialog(app, result))

            # Delay 5 giây sau khi app mở để không lag lúc khởi động
            def _delayed_check():
                check_update_async(on_update_result)

            app.after(5000, _delayed_check)
        except Exception:
            pass  # Update check không quan trọng, bỏ qua nếu lỗi

    app.after(100, _check_license_after_start)
    # ─────────────────────────────────────────────────────────────────────────
    app.mainloop()


if __name__ == "__main__":
    run_app()

