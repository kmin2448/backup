#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
폴더 동기화 프로그램 (SSD -> D드라이브)

기능:
  - 여러 개의 (원본 -> 대상) 폴더 쌍을 등록해 두고 버튼 하나로 한꺼번에 동기화
  - 동일한 파일(크기 + 수정시간 일치)은 건너뜀
  - 변경되었거나 새로 추가된 파일은 복사/덮어쓰기
  - 원본에서 삭제된 파일은 대상에서도 삭제 (삭제 전 사용자에게 확인)

표준 라이브러리(tkinter)만 사용하므로 별도 설치 없이 동작합니다.
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

# 설정 파일 (등록한 폴더 쌍과 옵션을 기억)
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".folder_sync_config.json")

# 창 크기 (정사각형). 배경 이미지도 이 크기로 맞춰져 있다.
WIN_SIZE = 720

# 색상 테마 (배경 이미지의 보라색과 맞춤)
BG_PURPLE = "#281438"
PANEL_BG = "#2a1640"
TEXT_BG = "#1c0f2b"
FG = "#f0e8f5"
ACCENT = "#c8403a"
WHITE = "#ffffff"

# 폰트 (Pretendard, 없으면 시스템 기본 폰트로 대체됨)
FONT_FAMILY = "Pretendard"

# 흰색 0.5pt 라인. tkinter의 최소 선 두께는 1px 이므로 1px 로 근사한다.
LINE_W = 1


def make_outline(parent, **kw):
    """자식 위젯을 감싸 흰색 얇은 테두리를 만드는 프레임을 반환한다.
    내부 위젯은 padx=LINE_W, pady=LINE_W 로 pack/grid 하면 된다."""
    return tk.Frame(parent, bg=WHITE, **kw)


def resource_path(rel):
    """개발 실행과 PyInstaller(--onefile) 실행 모두에서 리소스 경로를 찾는다."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

# 파일 동일 여부를 판단할 때 수정시간 오차 허용치(초).
# 파일시스템(FAT/NTFS) 간 시간 해상도 차이로 인한 오탐을 막기 위함.
MTIME_TOLERANCE = 2.0


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
            rel = os.path.relpath(full, root)
            result.add(rel)
    return result


def list_relative_dirs(root):
    """root 아래의 모든 하위 폴더를 root 기준 상대경로 set 으로 반환."""
    result = set()
    for dirpath, dirnames, _filenames in os.walk(root):
        for name in dirnames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            result.add(rel)
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
    """소스/대상을 비교해서 해야 할 작업 목록을 만든다."""

    def __init__(self, src, dst):
        self.src = src
        self.dst = dst
        self.to_copy = []      # 새로 추가된 파일 (상대경로)
        self.to_update = []    # 변경되어 덮어쓸 파일 (상대경로)
        self.to_skip = []      # 동일해서 건너뛸 파일 (상대경로)
        self.to_delete = []    # 대상에만 있어 삭제할 파일 (상대경로)
        self.dirs_to_delete = []  # 대상에만 있어 삭제할 폴더 (상대경로)

    def build(self):
        src_files = list_relative_files(self.src)
        dst_files = list_relative_files(self.dst) if os.path.isdir(self.dst) else set()

        for rel in sorted(src_files):
            s = os.path.join(self.src, rel)
            d = os.path.join(self.dst, rel)
            if rel not in dst_files:
                self.to_copy.append(rel)
            elif files_identical(s, d):
                self.to_skip.append(rel)
            else:
                self.to_update.append(rel)

        # 대상에만 존재하는 파일 = 원본에서 삭제된 파일
        for rel in sorted(dst_files - src_files):
            self.to_delete.append(rel)

        # 대상에만 존재하는 폴더(원본에 없는 폴더) 정리 대상
        src_dirs = list_relative_dirs(self.src)
        dst_dirs = list_relative_dirs(self.dst) if os.path.isdir(self.dst) else set()
        # 긴 경로(깊은 폴더)부터 지워야 하므로 역순 정렬
        self.dirs_to_delete = sorted(dst_dirs - src_dirs, reverse=True)


class App:
    def __init__(self, root):
        self.root = root
        root.title("폴더 동기화 (SSD → D드라이브)")
        # 정사각형 고정 창
        root.geometry(f"{WIN_SIZE}x{WIN_SIZE}")
        root.resizable(False, False)
        root.configure(bg=BG_PURPLE)

        cfg = load_config()

        # 등록된 폴더 쌍 목록: [(src, dst), ...]
        self.pairs = [tuple(p) for p in cfg.get("pairs", []) if len(p) == 2]
        # 과거 단일 쌍 설정과의 호환
        if not self.pairs and cfg.get("src") and cfg.get("dst"):
            self.pairs = [(cfg["src"], cfg["dst"])]

        self.delete_var = tk.BooleanVar(value=cfg.get("delete_enabled", True))
        self.confirm_delete_var = tk.BooleanVar(value=cfg.get("confirm_delete", True))

        # 원본/대상 폴더 선택 입력칸
        self.src_input = tk.StringVar()
        self.dst_input = tk.StringVar()

        # 예약 실행 설정
        self.sched_enabled = tk.BooleanVar(value=cfg.get("sched_enabled", False))
        self.sched_mode = tk.StringVar(value=cfg.get("sched_mode", "interval"))
        self.sched_time = tk.StringVar(value=cfg.get("sched_time", "03:00"))
        self.sched_interval = tk.StringVar(value=str(cfg.get("sched_interval", 60)))
        self._last_interval_run = time.time()
        self._last_daily_run_day = None

        # 동기화 진행 중 여부 (중복 실행 방지)
        self.busy = False

        # 백그라운드 작업과 통신용 큐
        self.log_queue = queue.Queue()

        self._setup_fonts()
        self._build_ui()
        self._refresh_tree()
        self.root.after(100, self._drain_log_queue)
        self.root.after(1000, self._schedule_tick)

    # ---------------- 폰트 (Pretendard) ----------------
    def _setup_fonts(self):
        # assets 폴더에 Pretendard 폰트 파일이 있으면 설치 없이 등록한다.
        self._load_bundled_font()
        # 이름있는 기본 폰트들을 Pretendard 로 바꾸면 tk/ttk 위젯 모두 적용된다.
        # Pretendard 가 없으면 시스템이 비슷한 폰트로 대체한다.
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkIconFont"):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=FONT_FAMILY, size=10)
            except tk.TclError:
                pass
        self.font_n = (FONT_FAMILY, 10)
        self.font_b = (FONT_FAMILY, 10, "bold")
        self.font_title = (FONT_FAMILY, 16, "bold")
        self.font_small = (FONT_FAMILY, 9)
        self.font_mono = (FONT_FAMILY, 9)

    def _load_bundled_font(self):
        """assets 폴더의 Pretendard 폰트 파일(.ttf/.otf)을 시스템 설치 없이 등록한다.
        Windows 에서만 동작하며, 파일이 없거나 실패해도 조용히 넘어간다."""
        if sys.platform != "win32":
            return
        for fname in ("Pretendard-Regular.ttf", "Pretendard.ttf",
                      "Pretendard-Bold.ttf", "Pretendard.otf"):
            path = resource_path(os.path.join("assets", fname))
            if os.path.exists(path):
                try:
                    import ctypes
                    FR_PRIVATE = 0x10
                    ctypes.windll.gdi32.AddFontResourceExW(path, FR_PRIVATE, 0)
                except Exception:
                    pass

    # ---------------- 다크 테마 스타일 ----------------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # 트리뷰: 회색 면 대신 보라 배경 + 흰색 얇은 라인
        style.configure("Sync.Treeview",
                        background=PANEL_BG, fieldbackground=PANEL_BG,
                        foreground=FG, borderwidth=0, relief="flat",
                        rowheight=22, font=self.font_n)
        style.map("Sync.Treeview",
                  background=[("selected", "#43275f")],
                  foreground=[("selected", WHITE)])
        style.configure("Sync.Treeview.Heading",
                        background=PANEL_BG, foreground=WHITE,
                        relief="flat", borderwidth=0, font=self.font_b)
        style.map("Sync.Treeview.Heading", background=[("active", "#3a2350")])
        # 진행 표시줄: 흰색 막대
        style.configure("Sync.Horizontal.TProgressbar",
                        troughcolor=PANEL_BG, background=WHITE,
                        borderwidth=0, lightcolor=WHITE, darkcolor=WHITE)
        # 스크롤바: 회색 제거, 흰색 얇은 라인 느낌
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"Sync.{orient}.TScrollbar",
                            troughcolor=PANEL_BG, background="#5a3f78",
                            bordercolor=WHITE, arrowcolor=WHITE,
                            relief="flat", borderwidth=0)

    def _btn(self, parent, text, command, white_bg=False):
        """흰색 얇은 테두리 버튼. white_bg=True 면 배경이 흰색."""
        return tk.Button(
            parent, text=text, command=command,
            bg=WHITE if white_bg else PANEL_BG,
            fg="#201030" if white_bg else WHITE,
            activebackground="#e9e3f0" if white_bg else "#3a2350",
            activeforeground="#201030" if white_bg else WHITE,
            relief="flat", bd=0,
            highlightthickness=LINE_W, highlightbackground=WHITE,
            highlightcolor=WHITE,
            padx=12, pady=4, cursor="hand2", font=self.font_b)

    def _entry(self, parent, var, width=None):
        return tk.Entry(
            parent, textvariable=var, width=width,
            bg=PANEL_BG, fg=WHITE, insertbackground=WHITE,
            relief="flat", bd=0, highlightthickness=LINE_W,
            highlightbackground=WHITE, highlightcolor=WHITE, font=self.font_n)

    def _check(self, parent, text, var, command=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            bg=PANEL_BG, fg=FG, selectcolor=PANEL_BG,
            activebackground=PANEL_BG, activeforeground=WHITE,
            highlightthickness=0, bd=0, font=self.font_n)

    def _radio(self, parent, text, var, value, command=None):
        return tk.Radiobutton(
            parent, text=text, variable=var, value=value, command=command,
            bg=PANEL_BG, fg=FG, selectcolor=PANEL_BG,
            activebackground=PANEL_BG, activeforeground=WHITE,
            highlightthickness=0, bd=0, font=self.font_n)

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        self._setup_style()

        # 배경 캔버스 + 이미지
        self.canvas = tk.Canvas(self.root, width=WIN_SIZE, height=WIN_SIZE,
                                highlightthickness=0, bg=BG_PURPLE)
        self.canvas.pack(fill="both", expand=True)

        self.bg_img = None
        try:
            self.bg_img = tk.PhotoImage(file=resource_path("assets/background.png"))
            self.canvas.create_image(0, 0, anchor="nw", image=self.bg_img)
        except Exception:
            # 이미지가 없어도 보라색 배경으로 동작
            pass

        self.canvas.create_text(
            24, 22, anchor="nw", text="폴더 동기화",
            fill=FG, font=self.font_title)
        self.canvas.create_text(
            24, 50, anchor="nw", text="SSD → D드라이브  ·  버튼 하나로 한꺼번에",
            fill="#c9b8d8", font=self.font_small)

        # 컨트롤 패널 (그림 아래쪽 보라 영역에 얹는다)
        panel = tk.Frame(self.canvas, bg=PANEL_BG)
        self.canvas.create_window(WIN_SIZE // 2, 300, anchor="n",
                                  window=panel, width=696)

        pad = {"padx": 6, "pady": 2}
        LBL = dict(bg=PANEL_BG, fg=FG, font=self.font_n)

        # 원본 폴더 선택
        rows = tk.Frame(panel, bg=PANEL_BG)
        rows.pack(fill="x", **pad)
        tk.Label(rows, text="원본 폴더 (SSD)", width=13, anchor="w",
                 **LBL).pack(side="left")
        self._entry(rows, self.src_input).pack(
            side="left", fill="x", expand=True, padx=4)
        self._btn(rows, "찾아보기", self.browse_src).pack(side="left")

        # 대상 폴더 선택
        rowd = tk.Frame(panel, bg=PANEL_BG)
        rowd.pack(fill="x", **pad)
        tk.Label(rowd, text="대상 폴더 (D드라이브)", width=13, anchor="w",
                 **LBL).pack(side="left")
        self._entry(rowd, self.dst_input).pack(
            side="left", fill="x", expand=True, padx=4)
        self._btn(rowd, "찾아보기", self.browse_dst).pack(side="left")

        # 목록 추가/제거 버튼
        listbtns = tk.Frame(panel, bg=PANEL_BG)
        listbtns.pack(fill="x", **pad)
        self._btn(listbtns, "＋ 목록에 추가", self.add_pair).pack(side="left", padx=3)
        self._btn(listbtns, "선택 제거", self.remove_pair).pack(side="left", padx=3)
        self._btn(listbtns, "전체 비우기", self.clear_pairs).pack(side="left", padx=3)
        tk.Label(listbtns, text="(목록 항목을 더블클릭하면 위 칸으로 불러와 수정)",
                 bg=PANEL_BG, fg="#a892c0", font=self.font_small).pack(
            side="left", padx=6)

        # 폴더 쌍 목록 (Treeview) — 흰색 테두리
        treebox = tk.Frame(panel, bg=WHITE)
        treebox.pack(fill="x", **pad)
        treefrm = tk.Frame(treebox, bg=PANEL_BG)
        treefrm.pack(fill="both", expand=True, padx=LINE_W, pady=LINE_W)
        self.tree = ttk.Treeview(
            treefrm, columns=("src", "dst"), show="headings", height=3,
            style="Sync.Treeview")
        self.tree.heading("src", text="원본 폴더 (SSD)")
        self.tree.heading("dst", text="대상 폴더 (D드라이브)")
        self.tree.column("src", width=320, anchor="w")
        self.tree.column("dst", width=320, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.load_selected)
        tsb = ttk.Scrollbar(treefrm, command=self.tree.yview,
                            style="Sync.Vertical.TScrollbar")
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)

        # 옵션
        opt = tk.Frame(panel, bg=PANEL_BG)
        opt.pack(fill="x", **pad)
        self._check(opt, "원본에서 삭제된 파일을 대상에서도 삭제",
                    self.delete_var, command=self._persist).pack(side="left")
        self._check(opt, "삭제 전 확인",
                    self.confirm_delete_var, command=self._persist).pack(side="left", padx=8)

        # 예약 실행
        sch = tk.Frame(panel, bg=PANEL_BG)
        sch.pack(fill="x", **pad)
        self._check(sch, "예약 실행", self.sched_enabled,
                    command=self._persist).pack(side="left")
        self._radio(sch, "반복", self.sched_mode, "interval",
                    command=self._persist).pack(side="left", padx=(8, 0))
        self._entry(sch, self.sched_interval, width=4).pack(side="left")
        tk.Label(sch, text="분마다", **LBL).pack(side="left", padx=(2, 0))
        self._radio(sch, "매일", self.sched_mode, "daily",
                    command=self._persist).pack(side="left", padx=(10, 0))
        self._entry(sch, self.sched_time, width=6).pack(side="left")
        tk.Label(sch, text="에", **LBL).pack(side="left", padx=(2, 0))
        self.sched_status_var = tk.StringVar(value="")
        tk.Label(sch, textvariable=self.sched_status_var,
                 bg=PANEL_BG, fg="#a892c0", font=self.font_small).pack(
            side="right")

        # 실행 버튼
        btns = tk.Frame(panel, bg=PANEL_BG)
        btns.pack(fill="x", **pad)
        self.preview_btn = self._btn(btns, "미리보기", self.preview)
        self.preview_btn.pack(side="left", padx=3)
        self.sync_btn = self._btn(btns, "전체 동기화 시작", self.start_sync,
                                  white_bg=True)
        self.sync_btn.pack(side="left", padx=3)

        # 진행 표시줄 — 흰색 테두리
        progbox = tk.Frame(panel, bg=WHITE)
        progbox.pack(fill="x", padx=6, pady=2)
        self.progress = ttk.Progressbar(progbox, mode="determinate",
                                        style="Sync.Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=LINE_W, pady=LINE_W)

        # 로그 영역 — 흰색 테두리
        logbox = tk.Frame(panel, bg=WHITE)
        logbox.pack(fill="both", expand=True, padx=6, pady=2)
        logfrm = tk.Frame(logbox, bg=PANEL_BG)
        logfrm.pack(fill="both", expand=True, padx=LINE_W, pady=LINE_W)
        self.log = tk.Text(logfrm, wrap="none", height=3, state="disabled",
                           bg=PANEL_BG, fg=FG, insertbackground=FG,
                           relief="flat", bd=0, highlightthickness=0,
                           font=self.font_mono)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logfrm, command=self.log.yview,
                           style="Sync.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # 상태 표시줄
        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(panel, textvariable=self.status_var, bg=PANEL_BG, fg="#bfa9d4",
                 anchor="w", font=self.font_small).pack(fill="x", padx=6, pady=(2, 6))

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
            self.tree.insert("", "end", values=(src, dst))

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
        """목록에서 더블클릭한 쌍을 위 입력칸으로 불러오고 목록에서 제거(수정용)."""
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
        idxs = sorted((self.tree.index(s) for s in sel), reverse=True)
        for i in idxs:
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
            "sched_enabled": self.sched_enabled.get(),
            "sched_mode": self.sched_mode.get(),
            "sched_time": self.sched_time.get().strip(),
            "sched_interval": interval,
        })

    # ---------------- 예약 실행 ----------------
    def _schedule_tick(self):
        """주기적으로 호출되어 예약 시간이 되면 자동 동기화를 시작한다."""
        try:
            self._update_sched_status()
            if (self.sched_enabled.get() and not self.busy and self.pairs
                    and self._should_run_now()):
                self.log_msg("[예약] 예약 시간에 도달하여 자동 동기화를 시작합니다.")
                self.start_sync(scheduled=True)
        finally:
            self.root.after(15000, self._schedule_tick)

    def _should_run_now(self):
        mode = self.sched_mode.get()
        if mode == "interval":
            try:
                mins = max(1, int(self.sched_interval.get()))
            except ValueError:
                return False
            if time.time() - self._last_interval_run >= mins * 60:
                self._last_interval_run = time.time()
                return True
            return False
        else:  # daily
            now = time.localtime()
            hhmm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
            today = (now.tm_year, now.tm_yday)
            if self.sched_time.get().strip() == hhmm \
                    and self._last_daily_run_day != today:
                self._last_daily_run_day = today
                return True
            return False

    def _update_sched_status(self):
        if not self.sched_enabled.get():
            self.sched_status_var.set("예약 꺼짐")
            return
        if self.sched_mode.get() == "interval":
            self.sched_status_var.set(
                f"예약 켜짐 · {self.sched_interval.get()}분마다")
        else:
            self.sched_status_var.set(
                f"예약 켜짐 · 매일 {self.sched_time.get().strip()}")

    # ---------------- 로그 ----------------
    def log_msg(self, msg):
        self.log_queue.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ---------------- 공통: 유효한 쌍 목록 만들기 ----------------
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
        self.clear_log()
        self._persist()
        pairs = self._valid_pairs()
        if pairs is None:
            return
        self.status_var.set("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(target=self._preview_worker, args=(pairs,),
                         daemon=True).start()

    def _preview_worker(self, pairs):
        try:
            tot_copy = tot_update = tot_skip = tot_delete = 0
            for src, dst in pairs:
                plan = SyncPlan(src, dst)
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
            self.status_var.set("미리보기 완료")
        finally:
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
        self.status_var.set("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(target=self._sync_worker, args=(pairs,),
                         daemon=True).start()

    def _sync_worker(self, pairs):
        try:
            # 1) 모든 쌍의 계획을 먼저 세운다 (진행률 총량 계산 + 삭제 확인용)
            plans = []
            for src, dst in pairs:
                os.makedirs(dst, exist_ok=True)
                plan = SyncPlan(src, dst)
                plan.build()
                plans.append(plan)

            tot_copy = sum(len(p.to_copy) for p in plans)
            tot_update = sum(len(p.to_update) for p in plans)
            tot_skip = sum(len(p.to_skip) for p in plans)
            all_deletes = [(p, rel) for p in plans for rel in p.to_delete]
            self.log_msg(f"분석 완료 - 폴더 쌍 {len(plans)}개 / "
                         f"추가 {tot_copy} / 변경 {tot_update} / "
                         f"건너뜀 {tot_skip} / 삭제대상 {len(all_deletes)}")

            # 2) 삭제 여부 결정 (전체에 대해 한 번만 확인)
            do_delete = False
            if self.delete_var.get() and all_deletes:
                if self.confirm_delete_var.get():
                    do_delete = self._ask_delete([rel for _p, rel in all_deletes])
                else:
                    do_delete = True

            total = tot_copy + tot_update + (len(all_deletes) if do_delete else 0)
            self._set_progress_max(total)

            done = 0
            copied = updated = deleted = errors = 0

            # 3) 추가 + 변경 복사
            for plan in plans:
                for rel in plan.to_copy + plan.to_update:
                    s = os.path.join(plan.src, rel)
                    d = os.path.join(plan.dst, rel)
                    is_add = rel in plan.to_copy
                    tag = "추가" if is_add else "변경"
                    try:
                        os.makedirs(os.path.dirname(d), exist_ok=True)
                        shutil.copy2(s, d)  # 메타데이터(수정시간) 보존
                        self.log_msg(f"[{tag}] {rel}")
                        if is_add:
                            copied += 1
                        else:
                            updated += 1
                    except Exception as e:
                        self.log_msg(f"[오류] 복사 실패 {rel}: {e}")
                        errors += 1
                    done += 1
                    self._set_progress(done)

            # 4) 삭제
            if do_delete:
                for plan in plans:
                    for rel in plan.to_delete:
                        d = os.path.join(plan.dst, rel)
                        try:
                            os.remove(d)
                            self.log_msg(f"[삭제] {rel}")
                            deleted += 1
                        except Exception as e:
                            self.log_msg(f"[오류] 삭제 실패 {rel}: {e}")
                            errors += 1
                        done += 1
                        self._set_progress(done)
                    # 빈 폴더 정리
                    for rel in plan.dirs_to_delete:
                        d = os.path.join(plan.dst, rel)
                        try:
                            if os.path.isdir(d) and not os.listdir(d):
                                os.rmdir(d)
                                self.log_msg(f"[폴더삭제] {rel}")
                        except Exception:
                            pass
            elif all_deletes:
                self.log_msg(f"삭제 건너뜀 - {len(all_deletes)}개 파일은 "
                             "대상에 그대로 둡니다.")

            self.log_msg("")
            self.log_msg(f"===== 완료 ({datetime.now():%Y-%m-%d %H:%M:%S}) =====")
            self.log_msg(f"추가 {copied} / 변경 {updated} / 건너뜀 {tot_skip} / "
                         f"삭제 {deleted} / 오류 {errors}")
            self.status_var.set(
                f"완료: 추가 {copied}, 변경 {updated}, 삭제 {deleted}, 오류 {errors}")
        except Exception as e:
            self.log_msg(f"[치명적 오류] {e}")
            self.status_var.set("오류로 중단됨")
        finally:
            self.busy = False
            self._set_buttons(True)

    def _ask_delete(self, to_delete):
        """삭제 확인 대화상자. 메인 스레드에서 띄우기 위해 이벤트로 동기화."""
        result = {"ok": False}
        event = threading.Event()

        def ask():
            preview = "\n".join(to_delete[:20])
            more = "" if len(to_delete) <= 20 else f"\n... 외 {len(to_delete) - 20}개"
            msg = (f"원본에서 삭제된 파일 {len(to_delete)}개를 "
                   f"대상 폴더에서도 삭제할까요?\n\n{preview}{more}")
            result["ok"] = messagebox.askyesno("삭제 확인", msg)
            event.set()

        self.root.after(0, ask)
        event.wait()
        return result["ok"]

    # ---------------- UI 상태 헬퍼 (스레드 안전) ----------------
    def _set_buttons(self, enabled):
        def apply():
            state = "normal" if enabled else "disabled"
            self.sync_btn.configure(state=state)
            self.preview_btn.configure(state=state)
        self.root.after(0, apply)

    def _set_progress_max(self, total):
        def apply():
            self.progress.configure(maximum=max(total, 1), value=0)
        self.root.after(0, apply)

    def _set_progress(self, value):
        self.root.after(0, lambda: self.progress.configure(value=value))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
