#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
폴더 동기화 프로그램 (SSD -> D드라이브)

기능:
  - 여러 개의 (원본 -> 대상) 폴더 쌍을 등록해 두고 버튼 하나로 한꺼번에 동기화
  - 동일한 파일(크기 + 수정시간 일치)은 건너뜀
  - 변경되었거나 새로 추가된 파일은 복사/덮어쓰기
  - 원본에서 삭제된 파일은 대상에서도 삭제 (삭제 전 사용자에게 확인)
  - 예약 실행(반복/매일), 진행률, 로그, 마우스오버 툴팁

표준 라이브러리(tkinter)만 사용하므로 별도 설치 없이 동작합니다.
화면이 흐리게 보이지 않도록 Windows 고해상도(High-DPI)에 대응합니다.
"""

import os
import sys
import time
import shutil
import threading
import queue
import json
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

import customtkinter as ctk

# 설정 파일 (등록한 폴더 쌍과 옵션을 기억)
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".folder_sync_config.json")

# ----- 색상 테마 (뉴모피즘 / Soft UI · 민트 + 딥그린) -----
BG = "#E8EEE9"          # 연한 민트빛 오프화이트 (앱 배경)
CARD = "#F1F6F1"        # 살짝 밝게 떠 있는 카드
CARD2 = "#E4ECE5"       # 보조 버튼 톤
INSET = "#DEE7E0"       # 안으로 들어간 느낌의 입력칸/리스트/트로프
SHADOW = "#CBD7CD"      # 부드러운 그림자(테두리 근사)
HILIGHT = "#FBFEFB"     # 밝은 하이라이트(테두리 근사)
TEAL = "#2D6A5A"        # 포인트 딥그린/청록
TEAL_DARK = "#235447"   # 진한 청록 (메인 버튼 hover)
TEAL_SOFT = "#3E8473"   # 연한 청록
TEXT = "#284A40"        # 본문 (딥그린 계열)
MUTED = "#5E7269"       # 보조 텍스트
BTN_HOVER = "#D6E1D8"   # 보조 버튼 hover
STOP_HOVER = "#DCE7DE"  # 멈추기(외곽선) hover

# 폰트: 깔끔하게 보이도록 OS 기본 산세리프 사용 (Windows=맑은 고딕)
if sys.platform == "win32":
    FONT_FAMILY = "Malgun Gothic"
elif sys.platform == "darwin":
    FONT_FAMILY = "Apple SD Gothic Neo"
else:
    FONT_FAMILY = "Pretendard"
BASE_SIZE = 13

# 파일 동일 여부를 판단할 때 수정시간 오차 허용치(초).
MTIME_TOLERANCE = 2.0


def resource_path(rel):
    """개발 실행과 PyInstaller(--onefile) 실행 모두에서 리소스 경로를 찾는다."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def enable_dpi_awareness():
    """Windows에서 고해상도 인식을 켜 글자/버튼이 흐릿하게 확대되지 않게 한다."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def list_relative_files(root):
    """root 아래의 모든 파일을 root 기준 상대경로 set 으로 반환."""
    result = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            result.add(os.path.relpath(full, root))
    return result


def list_relative_dirs(root):
    """root 아래의 모든 하위 폴더를 root 기준 상대경로 set 으로 반환."""
    result = set()
    for dirpath, dirnames, _filenames in os.walk(root):
        for name in dirnames:
            full = os.path.join(dirpath, name)
            result.add(os.path.relpath(full, root))
    return result


def files_identical(src, dst):
    """두 파일이 동일한지 빠르게 판단 (크기 + 수정시간)."""
    try:
        s = os.stat(src)
        d = os.stat(dst)
    except OSError:
        return False
    if s.st_size != d.st_size:
        return False
    if abs(s.st_mtime - d.st_mtime) > MTIME_TOLERANCE:
        return False
    return True


def validate_pair(src, dst):
    """폴더 쌍의 유효성 검사. 문제가 있으면 오류 메시지를, 없으면 None 을 반환."""
    if not src or not dst:
        return "원본과 대상 폴더가 모두 지정되어야 합니다."
    if not os.path.isdir(src):
        return f"원본 폴더가 존재하지 않습니다: {src}"
    src_abs = os.path.abspath(src)
    dst_abs = os.path.abspath(dst)
    if src_abs == dst_abs:
        return "원본과 대상이 같은 폴더입니다."
    if dst_abs.startswith(src_abs + os.sep):
        return "대상 폴더가 원본 폴더 안에 있을 수 없습니다."
    if src_abs.startswith(dst_abs + os.sep):
        return "원본 폴더가 대상 폴더 안에 있을 수 없습니다."
    return None


class SyncPlan:
    """소스/대상을 비교해서 해야 할 작업 목록을 만든다.

    overwrite=True  : 동일 파일명이 있을 때 용량/수정일자가 다르면 덮어쓰기
    overwrite=False : 동일 파일명이 있으면 무조건 건너뛰기
    """

    def __init__(self, src, dst, overwrite=True):
        self.src = src
        self.dst = dst
        self.overwrite = overwrite
        self.to_copy = []        # 새로 추가된 파일
        self.to_update = []      # 변경되어 덮어쓸 파일
        self.to_skip = []        # 동일/건너뛸 파일
        self.to_delete = []      # 대상에만 있어 삭제할 파일
        self.dirs_to_delete = []  # 대상에만 있어 삭제할 폴더

    def build(self):
        src_files = list_relative_files(self.src)
        dst_files = list_relative_files(self.dst) if os.path.isdir(self.dst) else set()

        for rel in sorted(src_files):
            s = os.path.join(self.src, rel)
            d = os.path.join(self.dst, rel)
            if rel not in dst_files:
                self.to_copy.append(rel)
            elif not self.overwrite:
                # 동일 파일명 → 무조건 건너뛰기
                self.to_skip.append(rel)
            elif files_identical(s, d):
                self.to_skip.append(rel)
            else:
                self.to_update.append(rel)

        for rel in sorted(dst_files - src_files):
            self.to_delete.append(rel)

        src_dirs = list_relative_dirs(self.src)
        dst_dirs = list_relative_dirs(self.dst) if os.path.isdir(self.dst) else set()
        self.dirs_to_delete = sorted(dst_dirs - src_dirs, reverse=True)


class Tooltip:
    """위젯에 마우스를 올리면 기능 설명을 말풍선으로 보여준다."""

    def __init__(self, widget, text, font, delay=400):
        self.widget = widget
        self.text = text
        self.font = font
        self.delay = delay
        self.tip = None
        self.after_id = None
        # CTk 버튼은 내부 캔버스/라벨로 구성되므로 자식까지 함께 바인딩한다.
        for w in [widget] + self._descendants(widget):
            w.bind("<Enter>", self._enter, add="+")
            w.bind("<Leave>", self._leave, add="+")
            w.bind("<ButtonPress>", self._leave, add="+")

    @staticmethod
    def _descendants(widget):
        out = []
        try:
            for ch in widget.winfo_children():
                out.append(ch)
                out.extend(Tooltip._descendants(ch))
        except Exception:
            pass
        return out

    def _enter(self, _=None):
        self._cancel()
        self.after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except tk.TclError:
            return
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tk.Label(tw, text=self.text, font=self.font, bg="#2B2B2E", fg="white",
                 padx=10, pady=6, justify="left").pack()
        tw.update_idletasks()
        w = tw.winfo_width()
        tw.wm_geometry(f"+{max(0, x - w // 2)}+{y}")

    def _leave(self, _=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None


class App:
    def __init__(self, root):
        self.root = root
        root.title("폴더 동기화 (SSD → D드라이브)")
        try:
            root.configure(fg_color=BG)
        except Exception:
            root.configure(bg=BG)

        cfg = load_config()

        self.pairs = [tuple(p) for p in cfg.get("pairs", []) if len(p) == 2]
        if not self.pairs and cfg.get("src") and cfg.get("dst"):
            self.pairs = [(cfg["src"], cfg["dst"])]

        self.delete_var = tk.BooleanVar(value=cfg.get("delete_enabled", True))
        self.confirm_delete_var = tk.BooleanVar(value=cfg.get("confirm_delete", True))
        # 동일 파일명 처리: "skip" = 무조건 건너뛰기, "diff" = 다르면 덮어쓰기
        self.conflict_mode = tk.StringVar(value=cfg.get("conflict_mode", "diff"))
        self.src_input = tk.StringVar()
        self.dst_input = tk.StringVar()

        self.sched_enabled = tk.BooleanVar(value=cfg.get("sched_enabled", False))
        self.sched_mode = tk.StringVar(value=cfg.get("sched_mode", "interval"))
        self.sched_time = tk.StringVar(value=cfg.get("sched_time", "03:00"))
        self.sched_interval = tk.StringVar(value=str(cfg.get("sched_interval", 60)))
        self._last_interval_run = time.time()
        self._last_daily_run_day = None

        self.busy = False
        self._cancel = threading.Event()   # 멈추기 요청
        self.log_queue = queue.Queue()
        self._ui_queue = queue.Queue()

        self.status_var = tk.StringVar(value="대기 중")
        self.count_var = tk.StringVar(value="동기화 폴더 0개")
        self.sched_status_var = tk.StringVar(value="")
        self.progress_var = tk.StringVar(value="")   # 진행률 % + 남은 시간

        self._setup_fonts()
        self._setup_style()
        self._build_ui()
        self._refresh_tree()
        self._update_sched_status()

        # 창 크기를 내용에 딱 맞춰 세로 스크롤이 생기지 않도록 한다.
        root.update_idletasks()
        w = min(root.winfo_reqwidth(), 820)
        h = root.winfo_reqheight()
        root.geometry(f"{max(w, 520)}x{h}")
        root.minsize(520, 460)

        self.root.after(100, self._drain_queues)
        self.root.after(1000, self._schedule_tick)

    # ---------------- 폰트 ----------------
    def _setup_fonts(self):
        self._load_bundled_font()
        for name, size in (("TkDefaultFont", BASE_SIZE), ("TkTextFont", BASE_SIZE),
                           ("TkMenuFont", BASE_SIZE), ("TkHeadingFont", BASE_SIZE)):
            try:
                tkfont.nametofont(name).configure(family=FONT_FAMILY, size=size)
            except tk.TclError:
                pass
        self.font_n = (FONT_FAMILY, BASE_SIZE)
        self.font_b = (FONT_FAMILY, BASE_SIZE, "bold")
        self.font_title = (FONT_FAMILY, 16, "bold")
        self.font_small = (FONT_FAMILY, 11)
        self.font_log = (FONT_FAMILY, 11)
        self.tree_font = tkfont.Font(family=FONT_FAMILY, size=BASE_SIZE)

    def _load_bundled_font(self):
        if sys.platform != "win32":
            return
        for fname in ("Pretendard-Regular.ttf", "Pretendard.ttf",
                      "Pretendard-Bold.ttf", "Pretendard.otf"):
            path = resource_path(os.path.join("assets", fname))
            if os.path.exists(path):
                try:
                    import ctypes
                    ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
                except Exception:
                    pass

    # ---------------- ttk 스타일 ----------------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        rowh = self.tree_font.metrics("linespace") + 14
        style.configure("Sync.Treeview", background=INSET, fieldbackground=INSET,
                        foreground=TEXT, borderwidth=0, relief="flat",
                        rowheight=rowh, font=self.font_n)
        style.map("Sync.Treeview", background=[("selected", TEAL)],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Sync.Vertical.TScrollbar", troughcolor=INSET,
                        background=SHADOW, bordercolor=INSET, arrowcolor=TEAL,
                        relief="flat", borderwidth=0)

    # ---------------- 위젯 헬퍼 (customtkinter, 뉴모피즘) ----------------
    def _card(self, parent, pady=(0, 7)):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=HILIGHT)
        card.pack(fill="x", pady=pady)
        return card

    def _button(self, parent, text, command, tooltip="", primary=False,
                danger=False, width=110):
        if danger:
            # 멈추기: 외곽선만 청록(메인과 같은 계열, 명도만 구분)
            opts = dict(fg_color="transparent", hover_color=STOP_HOVER,
                        text_color=TEAL, border_width=2, border_color=TEAL,
                        text_color_disabled=MUTED)
        elif primary:
            opts = dict(fg_color=TEAL, hover_color=TEAL_DARK,
                        text_color="#FFFFFF", text_color_disabled="#CFE0DA")
        else:
            opts = dict(fg_color=CARD2, hover_color=BTN_HOVER, text_color=TEAL,
                        text_color_disabled=MUTED)
        b = ctk.CTkButton(parent, text=text, command=command, width=width, height=30,
                          corner_radius=11,
                          font=self.font_b if (primary or danger) else self.font_n,
                          **opts)
        if tooltip:
            Tooltip(b, tooltip, self.font_small)
        return b

    def _entry(self, parent, var, width=140):
        return ctk.CTkEntry(parent, textvariable=var, width=width, height=30,
                            corner_radius=10, font=self.font_n, fg_color=INSET,
                            text_color=TEXT, border_color=SHADOW, border_width=1)

    def _check(self, parent, text, var):
        return ctk.CTkCheckBox(parent, text=text, variable=var,
                               command=self._persist, font=self.font_n,
                               text_color=TEXT, fg_color=TEAL, hover_color=TEAL_DARK,
                               checkmark_color="#FFFFFF", border_color=SHADOW,
                               corner_radius=6, checkbox_width=18, checkbox_height=18)

    def _radio(self, parent, text, value):
        return ctk.CTkRadioButton(parent, text=text, variable=self.sched_mode,
                                  value=value, command=self._persist, font=self.font_n,
                                  text_color=TEXT, fg_color=TEAL, hover_color=TEAL_DARK,
                                  border_color=SHADOW, radiobutton_width=20,
                                  radiobutton_height=20)

    def _mode_radio(self, parent, text, value):
        return ctk.CTkRadioButton(parent, text=text, variable=self.conflict_mode,
                                  value=value, command=self._persist, font=self.font_n,
                                  text_color=TEXT, fg_color=TEAL, hover_color=TEAL_DARK,
                                  border_color=SHADOW, radiobutton_width=20,
                                  radiobutton_height=20)

    def _label(self, parent, text, font=None, fg=TEXT, bg=None):
        return ctk.CTkLabel(parent, text=text, font=font or self.font_n,
                            text_color=fg, fg_color="transparent")

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        main = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        main.pack(fill="both", expand=True, padx=16, pady=12)

        # 헤더
        self._label(main, "폴더 동기화", font=self.font_title, fg=TEAL).pack(
            anchor="w", pady=(0, 8))

        # 폴더 선택 카드
        c = self._card(main)
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "원본 폴더").grid(row=0, column=0, sticky="w",
                                      padx=(14, 8), pady=(11, 5))
        self._entry(c, self.src_input).grid(row=0, column=1, sticky="ew", pady=(11, 5))
        self._button(c, "찾아보기", self.browse_src,
                     "동기화할 원본(SSD) 폴더를 선택합니다", width=92).grid(
            row=0, column=2, padx=(8, 14), pady=(11, 5))
        self._label(c, "대상 폴더").grid(row=1, column=0, sticky="w",
                                      padx=(14, 8), pady=5)
        self._entry(c, self.dst_input).grid(row=1, column=1, sticky="ew", pady=5)
        self._button(c, "찾아보기", self.browse_dst,
                     "복사될 대상(D드라이브) 폴더를 선택합니다", width=92).grid(
            row=1, column=2, padx=(8, 14), pady=5)
        self._button(c, "＋  목록에 추가", self.add_pair,
                     "위에서 고른 원본·대상 폴더를 동기화 목록에 추가합니다",
                     primary=True).grid(row=2, column=0, columnspan=3, sticky="ew",
                                        padx=14, pady=(5, 11))

        # 폴더 목록 카드
        c = self._card(main)
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 3))
        ctk.CTkLabel(head, textvariable=self.count_var, font=self.font_small,
                     text_color=TEXT).pack(side="left")
        self._label(head, "항목을 더블클릭하면 위 칸으로 불러와 수정합니다",
                    font=self.font_small, fg=TEXT).pack(side="right")
        holder = ctk.CTkFrame(c, fg_color=INSET, corner_radius=10)
        holder.pack(fill="both", expand=True, padx=14)
        tf = tk.Frame(holder, bg=INSET)
        tf.pack(fill="both", expand=True, padx=6, pady=6)
        self.tree = ttk.Treeview(tf, show="tree", style="Sync.Treeview", height=2)
        self.tree.column("#0", width=560, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.load_selected)
        sb = ttk.Scrollbar(tf, command=self.tree.yview, style="Sync.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        lb = ctk.CTkFrame(c, fg_color="transparent")
        lb.pack(fill="x", padx=14, pady=9)
        self._button(lb, "선택 제거", self.remove_pair,
                     "목록에서 선택한 폴더 쌍을 제거합니다", width=104).pack(side="left")
        self._button(lb, "전체 비우기", self.clear_pairs,
                     "동기화 목록을 모두 비웁니다", width=104).pack(side="left", padx=(8, 0))

        # 옵션 카드
        c = self._card(main)
        self._label(c, "같은 이름의 파일이 대상에 있을 때",
                    font=self.font_b, fg=TEXT).pack(anchor="w", padx=14, pady=(10, 3))
        self._mode_radio(c, "무조건 건너뛰기", "skip").pack(anchor="w", padx=14, pady=1)
        self._mode_radio(c, "파일 용량 또는 수정일자가 다르면 덮어쓰기", "diff").pack(
            anchor="w", padx=14, pady=(1, 6))
        ctk.CTkFrame(c, height=1, fg_color=SHADOW).pack(fill="x", padx=14, pady=2)
        self._check(c, "원본에서 삭제된 파일을 대상에서도 삭제",
                    self.delete_var).pack(anchor="w", padx=14, pady=(6, 3))
        self._check(c, "삭제 전 확인 (켜면 삭제 직전에 한 번 물어봅니다)",
                    self.confirm_delete_var).pack(anchor="w", padx=14, pady=(0, 9))

        # 예약 카드
        c = self._card(main)
        self._check(c, "예약 실행 (프로그램이 켜져 있는 동안 자동 동기화)",
                    self.sched_enabled).grid(row=0, column=0, columnspan=6,
                                             sticky="w", padx=14, pady=(10, 4))
        self._radio(c, "반복", "interval").grid(row=1, column=0, sticky="w", padx=(14, 4))
        self._entry(c, self.sched_interval, width=64).grid(row=1, column=1)
        self._label(c, "분마다").grid(row=1, column=2, sticky="w", padx=(6, 14))
        self._radio(c, "매일", "daily").grid(row=1, column=3, sticky="w")
        self._entry(c, self.sched_time, width=92).grid(row=1, column=4, padx=(4, 0))
        self._label(c, "에").grid(row=1, column=5, sticky="w", padx=(6, 14))
        ctk.CTkLabel(c, textvariable=self.sched_status_var, font=self.font_small,
                     text_color=MUTED).grid(row=2, column=0, columnspan=6, sticky="w",
                                            padx=14, pady=(4, 9))

        # 실행 버튼
        af = ctk.CTkFrame(main, fg_color="transparent")
        af.pack(fill="x", pady=(2, 10))
        self.preview_btn = self._button(
            af, "미리보기", self.preview,
            "실제 복사·삭제 없이 변경될 파일만 먼저 확인합니다", width=120)
        self.preview_btn.pack(side="left")
        self.stop_btn = self._button(
            af, "멈추기", self.stop_sync,
            "진행 중인 동기화를 중지합니다", danger=True, width=120)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.pack(side="right")
        self.sync_btn = self._button(
            af, "전체 동기화 시작", self.start_sync,
            "목록의 모든 폴더를 동기화합니다 (추가·변경 복사, 삭제 반영)",
            primary=True)
        self.sync_btn.pack(side="left", padx=(10, 8), fill="x", expand=True)

        # 진행 표시줄 + 퍼센트/남은 시간
        self.progress = ctk.CTkProgressBar(main, height=14, corner_radius=8,
                                           fg_color=INSET, progress_color=TEAL)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(main, textvariable=self.progress_var, font=self.font_small,
                     text_color=TEXT, anchor="w").pack(fill="x", pady=(0, 6))

        # 로그 카드
        c = self._card(main, pady=(0, 8))
        holder = ctk.CTkFrame(c, fg_color=INSET, corner_radius=10)
        holder.pack(fill="both", expand=True, padx=10, pady=10)
        self.log = tk.Text(holder, height=3, font=self.font_log, bg=INSET, fg=TEXT,
                           relief="flat", bd=0, highlightthickness=0, wrap="none",
                           state="disabled")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        sb = ttk.Scrollbar(holder, command=self.log.yview,
                           style="Sync.Vertical.TScrollbar")
        sb.pack(side="right", fill="y", pady=6, padx=(0, 4))
        self.log.configure(yscrollcommand=sb.set)

        # 상태 표시줄
        ctk.CTkLabel(main, textvariable=self.status_var, font=self.font_small,
                     text_color=MUTED, anchor="w").pack(fill="x")

    # ---------------- 폴더 선택 / 쌍 관리 ----------------
    def browse_src(self):
        p = filedialog.askdirectory(
            title="원본 폴더(SSD) 선택",
            initialdir=self.src_input.get() or os.path.expanduser("~"))
        if p:
            self.src_input.set(p)

    def browse_dst(self):
        p = filedialog.askdirectory(
            title="대상 폴더(D드라이브) 선택",
            initialdir=self.dst_input.get() or os.path.expanduser("~"))
        if p:
            self.dst_input.set(p)

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for src, dst in self.pairs:
            name = os.path.basename(src.rstrip("/\\")) or src
            self.tree.insert("", "end", text=f"  {name}    →    {dst}")
        self.count_var.set(f"동기화 폴더 {len(self.pairs)}개")

    def add_pair(self):
        src = self.src_input.get().strip()
        dst = self.dst_input.get().strip()
        err = validate_pair(src, dst)
        if err:
            messagebox.showerror("잘못된 폴더", err)
            return
        pair = (os.path.abspath(src), os.path.abspath(dst))
        if pair in self.pairs:
            messagebox.showinfo("안내", "이미 등록된 폴더 쌍입니다.")
            return
        self.pairs.append(pair)
        self._refresh_tree()
        self._persist()
        self.src_input.set("")
        self.dst_input.set("")

    def load_selected(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        src, dst = self.pairs[idx]
        self.src_input.set(src)
        self.dst_input.set(dst)
        del self.pairs[idx]
        self._refresh_tree()
        self._persist()

    def remove_pair(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "제거할 폴더 쌍을 목록에서 선택하세요.")
            return
        for i in sorted((self.tree.index(s) for s in sel), reverse=True):
            del self.pairs[i]
        self._refresh_tree()
        self._persist()

    def clear_pairs(self):
        if not self.pairs:
            return
        if messagebox.askyesno("확인", "등록된 폴더 쌍을 모두 지울까요?"):
            self.pairs = []
            self._refresh_tree()
            self._persist()

    def _persist(self):
        try:
            interval = int(self.sched_interval.get())
        except (ValueError, AttributeError):
            interval = 60
        save_config({
            "pairs": [list(p) for p in self.pairs],
            "delete_enabled": self.delete_var.get(),
            "confirm_delete": self.confirm_delete_var.get(),
            "conflict_mode": self.conflict_mode.get(),
            "sched_enabled": self.sched_enabled.get(),
            "sched_mode": self.sched_mode.get(),
            "sched_time": self.sched_time.get().strip(),
            "sched_interval": interval,
        })
        self._update_sched_status()

    # ---------------- 예약 ----------------
    def _schedule_tick(self):
        try:
            if (self.sched_enabled.get() and not self.busy and self.pairs
                    and self._should_run_now()):
                self.log_msg("[예약] 예약 시간에 도달하여 자동 동기화를 시작합니다.")
                self.start_sync(scheduled=True)
        finally:
            self.root.after(15000, self._schedule_tick)

    def _should_run_now(self):
        if self.sched_mode.get() == "interval":
            try:
                mins = max(1, int(self.sched_interval.get()))
            except ValueError:
                return False
            if time.time() - self._last_interval_run >= mins * 60:
                self._last_interval_run = time.time()
                return True
            return False
        now = time.localtime()
        hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        today = (now.tm_year, now.tm_yday)
        if self.sched_time.get().strip() == hhmm and self._last_daily_run_day != today:
            self._last_daily_run_day = today
            return True
        return False

    def _update_sched_status(self):
        if not self.sched_enabled.get():
            self.sched_status_var.set("예약 꺼짐")
        elif self.sched_mode.get() == "interval":
            self.sched_status_var.set(f"예약 켜짐 · {self.sched_interval.get()}분마다")
        else:
            self.sched_status_var.set(f"예약 켜짐 · 매일 {self.sched_time.get().strip()}")

    # ---------------- 로그 / 스레드→UI 전달 ----------------
    def log_msg(self, msg):
        self.log_queue.put(msg)

    def _ui(self, fn):
        self._ui_queue.put(fn)

    def set_status(self, text):
        self._ui(lambda: self.status_var.set(text))

    def _drain_queues(self):
        try:
            lines = []
            while True:
                lines.append(self.log_queue.get_nowait())
            # (도달하지 않음)
        except queue.Empty:
            pass
        if lines:
            self.log.configure(state="normal")
            for msg in lines:
                self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queues)

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ---------------- 유효한 쌍 목록 ----------------
    def _valid_pairs(self, silent=False):
        if not self.pairs:
            if not silent:
                messagebox.showwarning("경고", "동기화할 폴더 쌍을 먼저 추가하세요.")
            return None
        valid = []
        for src, dst in self.pairs:
            err = validate_pair(src, dst)
            if err:
                self.log_msg(f"[건너뜀] {src} → {dst} : {err}")
            else:
                valid.append((os.path.abspath(src), os.path.abspath(dst)))
        return valid

    # ---------------- 미리보기 ----------------
    def preview(self):
        if self.busy:
            return
        self.clear_log()
        self._persist()
        pairs = self._valid_pairs()
        if pairs is None:
            return
        self.busy = True
        self._cancel.clear()
        overwrite = self.conflict_mode.get() == "diff"
        self.progress_var.set("")
        self.set_status("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(target=self._preview_worker, args=(pairs, overwrite),
                         daemon=True).start()

    def _preview_worker(self, pairs, overwrite):
        try:
            tot_copy = tot_update = tot_skip = tot_delete = 0
            for src, dst in pairs:
                plan = SyncPlan(src, dst, overwrite)
                plan.build()
                tot_copy += len(plan.to_copy)
                tot_update += len(plan.to_update)
                tot_skip += len(plan.to_skip)
                tot_delete += len(plan.to_delete)
                self.log_msg(f"===== {src} → {dst} =====")
                self.log_msg(f"  추가 {len(plan.to_copy)} / 변경 {len(plan.to_update)} / "
                             f"건너뜀 {len(plan.to_skip)} / 삭제 {len(plan.to_delete)}")
                for rel in plan.to_copy:
                    self.log_msg(f"  [추가] {rel}")
                for rel in plan.to_update:
                    self.log_msg(f"  [변경] {rel}")
                for rel in plan.to_delete:
                    self.log_msg(f"  [삭제] {rel}")
            self.log_msg("")
            self.log_msg(f"### 전체 합계: 추가 {tot_copy} / 변경 {tot_update} / "
                         f"건너뜀 {tot_skip} / 삭제 {tot_delete}")
            self.set_status("미리보기 완료")
        finally:
            self.busy = False
            self._set_buttons(True)

    # ---------------- 동기화 ----------------
    def start_sync(self, scheduled=False):
        if self.busy:
            return
        self.clear_log()
        self._persist()
        pairs = self._valid_pairs(silent=scheduled)
        if pairs is None:
            return
        if not pairs:
            if not scheduled:
                messagebox.showwarning("경고", "동기화할 유효한 폴더 쌍이 없습니다.")
            return
        self.busy = True
        self._cancel.clear()
        self.set_status("변경사항 분석 중...")
        self._set_buttons(False, allow_stop=True)
        delete_enabled = self.delete_var.get()
        confirm_delete = self.confirm_delete_var.get()
        overwrite = self.conflict_mode.get() == "diff"
        threading.Thread(target=self._sync_worker,
                         args=(pairs, delete_enabled, confirm_delete, overwrite),
                         daemon=True).start()

    def _sync_worker(self, pairs, delete_enabled, confirm_delete, overwrite):
        try:
            plans = []
            for src, dst in pairs:
                os.makedirs(dst, exist_ok=True)
                plan = SyncPlan(src, dst, overwrite)
                plan.build()
                plans.append(plan)

            tot_copy = sum(len(p.to_copy) for p in plans)
            tot_update = sum(len(p.to_update) for p in plans)
            tot_skip = sum(len(p.to_skip) for p in plans)
            all_deletes = [(p, rel) for p in plans for rel in p.to_delete]
            self.log_msg(f"분석 완료 - 폴더 쌍 {len(plans)}개 / "
                         f"추가 {tot_copy} / 변경 {tot_update} / "
                         f"건너뜀 {tot_skip} / 삭제대상 {len(all_deletes)}")

            do_delete = False
            if delete_enabled and all_deletes:
                if confirm_delete:
                    do_delete = self._ask_delete([rel for _p, rel in all_deletes])
                else:
                    do_delete = True

            total = tot_copy + tot_update + (len(all_deletes) if do_delete else 0)
            self._set_progress_max(total)

            done = 0
            copied = updated = deleted = errors = 0
            cancelled = False

            # 3) 추가 + 변경 복사
            for plan in plans:
                for rel in plan.to_copy + plan.to_update:
                    if self._cancel.is_set():
                        cancelled = True
                        break
                    s = os.path.join(plan.src, rel)
                    d = os.path.join(plan.dst, rel)
                    is_add = rel in plan.to_copy
                    tag = "추가" if is_add else "변경"
                    try:
                        os.makedirs(os.path.dirname(d), exist_ok=True)
                        shutil.copy2(s, d)
                        self.log_msg(f"[{tag}] {rel}")
                        if is_add:
                            copied += 1
                        else:
                            updated += 1
                    except Exception as e:
                        self.log_msg(f"[오류] 복사 실패 {rel}: {e}")
                        errors += 1
                    done += 1
                    self._emit_progress(done)
                if cancelled:
                    break

            # 4) 삭제
            if do_delete and not cancelled:
                for plan in plans:
                    for rel in plan.to_delete:
                        if self._cancel.is_set():
                            cancelled = True
                            break
                        d = os.path.join(plan.dst, rel)
                        try:
                            os.remove(d)
                            self.log_msg(f"[삭제] {rel}")
                            deleted += 1
                        except Exception as e:
                            self.log_msg(f"[오류] 삭제 실패 {rel}: {e}")
                            errors += 1
                        done += 1
                        self._emit_progress(done)
                    if cancelled:
                        break
                    for rel in plan.dirs_to_delete:
                        d = os.path.join(plan.dst, rel)
                        try:
                            if os.path.isdir(d) and not os.listdir(d):
                                os.rmdir(d)
                                self.log_msg(f"[폴더삭제] {rel}")
                        except Exception:
                            pass
            elif all_deletes and not do_delete:
                self.log_msg(f"삭제 건너뜀 - {len(all_deletes)}개 파일은 "
                             "대상에 그대로 둡니다.")

            self._emit_progress(done, force=True)
            self.log_msg("")
            if cancelled:
                self.log_msg(f"===== 중지됨 ({datetime.now():%Y-%m-%d %H:%M:%S}) =====")
                self.log_msg(f"추가 {copied} / 변경 {updated} / 삭제 {deleted} / "
                             f"오류 {errors} (사용자가 중지)")
                self.set_status(
                    f"중지됨: 추가 {copied}, 변경 {updated}, 삭제 {deleted}")
            else:
                self.log_msg(f"===== 완료 ({datetime.now():%Y-%m-%d %H:%M:%S}) =====")
                self.log_msg(f"추가 {copied} / 변경 {updated} / 건너뜀 {tot_skip} / "
                             f"삭제 {deleted} / 오류 {errors}")
                self.set_status(
                    f"완료: 추가 {copied}, 변경 {updated}, 삭제 {deleted}, 오류 {errors}")
        except Exception as e:
            self.log_msg(f"[치명적 오류] {e}")
            self.set_status("오류로 중단됨")
        finally:
            self.busy = False
            self._cancel.clear()
            self._set_buttons(True)

    def _ask_delete(self, to_delete):
        result = {"ok": False}
        event = threading.Event()

        def ask():
            preview = "\n".join(to_delete[:20])
            more = "" if len(to_delete) <= 20 else f"\n... 외 {len(to_delete) - 20}개"
            msg = (f"원본에서 삭제된 파일 {len(to_delete)}개를 "
                   f"대상 폴더에서도 삭제할까요?\n\n{preview}{more}")
            result["ok"] = messagebox.askyesno("삭제 확인", msg)
            event.set()

        self._ui(ask)
        event.wait()
        return result["ok"]

    # ---------------- 멈추기 ----------------
    def stop_sync(self):
        if self.busy:
            self._cancel.set()
            self.set_status("중지 요청됨... 현재 파일을 마무리하는 중입니다")

    # ---------------- UI 상태 (메인 스레드에서 실행) ----------------
    def _set_buttons(self, enabled, allow_stop=False):
        def apply():
            st = "normal" if enabled else "disabled"
            self.sync_btn.configure(state=st)
            self.preview_btn.configure(state=st)
            # 멈추기 버튼은 동기화가 실제로 진행 중일 때만 활성화
            self.stop_btn.configure(state="normal" if allow_stop else "disabled")
        self._ui(apply)

    @staticmethod
    def _fmt_eta(seconds):
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}초"
        if seconds < 3600:
            return f"{seconds // 60}분 {seconds % 60}초"
        return f"{seconds // 3600}시간 {(seconds % 3600) // 60}분"

    def _set_progress_max(self, total):
        self._total = max(total, 1)
        self._start_time = time.time()
        self._last_emit = 0.0
        self._ui(lambda: (self.progress.set(0), self.progress_var.set("0%")))

    def _emit_progress(self, done, force=False):
        now = time.time()
        if not force and now - getattr(self, "_last_emit", 0) < 0.12:
            return
        self._last_emit = now
        total = getattr(self, "_total", 1)
        frac = min(1.0, done / total) if total else 1.0
        pct = int(frac * 100)
        elapsed = now - getattr(self, "_start_time", now)
        if 0 < done < total and elapsed > 0.5:
            eta = elapsed / done * (total - done)
            text = f"{pct}%   ·   남은 시간 약 {self._fmt_eta(eta)}   ({done}/{total})"
        elif done >= total:
            text = f"100%   ·   완료   ({total}/{total})"
        else:
            text = f"{pct}%   ({done}/{total})"
        self._ui(lambda: (self.progress.set(frac), self.progress_var.set(text)))


def main():
    enable_dpi_awareness()
    # customtkinter: 밝은 모드 + 고해상도에서 또렷하게 (자체 DPI 스케일링)
    ctk.set_appearance_mode("light")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
