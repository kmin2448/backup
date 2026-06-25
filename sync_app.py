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

# 색상 테마 — 밝은 모바일 카드 스타일
APP_BG = "#E8E8EC"       # 바깥 배경 (연한 회보라)
CARD = "#FFFFFF"         # 흰색 카드
CARD_GRAY = "#F1F1F4"    # 연회색 카드(아이콘 버튼 등)
TEXT = "#1B1B1F"         # 본문(거의 검정)
SUBT = "#9B9BA1"         # 보조 텍스트 / Off
DIVIDER = "#ECECEF"      # 구분선
ORANGE = "#F97316"       # 포인트 오렌지
NAVBLACK = "#141414"     # 하단 가운데 + 버튼 / 활성 탭
ICON = "#8A8A8E"         # 아이콘 기본
ICON_DK = "#2C2C2E"      # 진한 아이콘
WHITE = "#FFFFFF"

# (구버전 호환용 별칭 — 일부 코드에서 참조)
BG_PURPLE = APP_BG
PANEL_BG = CARD
TEXT_BG = CARD
FG = TEXT
ACCENT = ORANGE

# 폰트 (Pretendard, 없으면 시스템 기본 폰트로 대체됨)
FONT_FAMILY = "Pretendard"

# 흰색 0.5pt 라인. tkinter의 최소 선 두께는 1px 이므로 1px 로 근사한다.
LINE_W = 1


def make_outline(parent, **kw):
    """자식 위젯을 감싸 흰색 얇은 테두리를 만드는 프레임을 반환한다.
    내부 위젯은 padx=LINE_W, pady=LINE_W 로 pack/grid 하면 된다."""
    return tk.Frame(parent, bg=WHITE, **kw)


class _PlainVar:
    """값만 보관하는 간단한 변수(Tk 비의존). 백그라운드 스레드에서 안전하게 set 가능."""

    def __init__(self, v=""):
        self._v = v

    def set(self, v):
        self._v = v

    def get(self):
        return self._v


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
        root.configure(bg=APP_BG)

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

        # 백그라운드 작업과 통신용 큐 (UI 갱신은 모두 메인 스레드에서 처리)
        self.log_queue = queue.Queue()
        self._ui_queue = queue.Queue()

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

    # ---------------- 라이트 테마 스타일 ----------------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # 목록(Treeview): 흰 카드 위 깔끔한 리스트
        style.configure("Sync.Treeview",
                        background=CARD, fieldbackground=CARD,
                        foreground=TEXT, borderwidth=0, relief="flat",
                        bordercolor=CARD, lightcolor=CARD, darkcolor=CARD,
                        rowheight=28, font=self.font_n)
        style.map("Sync.Treeview",
                  background=[("selected", "#FFF1E6")],
                  foreground=[("selected", TEXT)])
        # 진행 표시줄: 오렌지 막대
        style.configure("Sync.Horizontal.TProgressbar",
                        troughcolor=CARD_GRAY, background=ORANGE,
                        borderwidth=0, thickness=6,
                        lightcolor=ORANGE, darkcolor=ORANGE)
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"Sync.{orient}.TScrollbar",
                            troughcolor=CARD, background="#D6D6DC",
                            bordercolor=CARD, arrowcolor=SUBT,
                            relief="flat", borderwidth=0)

    # ---------------- 캔버스 그리기 도우미 ----------------
    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r,
               x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
               x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _ln(self, *pts, c=ICON_DK, w=2):
        return self.canvas.create_line(*pts, fill=c, width=w,
                                       capstyle="round", joinstyle="round")

    def _ov(self, x0, y0, x1, y1, c=ICON_DK, w=2, fill=""):
        return self.canvas.create_oval(x0, y0, x1, y1, outline=c, width=w, fill=fill)

    def _bind(self, ids, command, guarded=False):
        tag = f"clk{ids[0]}"
        for i in ids:
            self.canvas.addtag_withtag(tag, i)

        def cb(_e):
            if guarded and self.busy:
                return
            command()
        self.canvas.tag_bind(tag, "<Button-1>", cb)
        self.canvas.tag_bind(tag, "<Enter>",
                             lambda e: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind(tag, "<Leave>",
                             lambda e: self.canvas.configure(cursor=""))
        return tag

    # ---------------- 라인 아이콘들 (단색, 얇은 선) ----------------
    def _ic_folder(self, cx, cy, c):
        return [self._round_rect(cx - 11, cy - 6, cx + 11, cy + 8, 3,
                                 outline=c, width=2, fill=""),
                self._ln(cx - 11, cy - 6, cx - 7, cy - 10, cx - 1, cy - 10,
                         cx + 1, cy - 6, c=c)]

    def _ic_plus(self, cx, cy, c):
        return [self._ln(cx, cy - 9, cx, cy + 9, c=c),
                self._ln(cx - 9, cy, cx + 9, cy, c=c)]

    def _ic_trash(self, cx, cy, c):
        return [self._ln(cx - 9, cy - 6, cx + 9, cy - 6, c=c),
                self._ln(cx - 3, cy - 6, cx - 3, cy - 9, cx + 3, cy - 9,
                         cx + 3, cy - 6, c=c),
                self._round_rect(cx - 7, cy - 6, cx + 7, cy + 9, 2,
                                 outline=c, width=2, fill=""),
                self._ln(cx - 2, cy - 2, cx - 2, cy + 5, c=c),
                self._ln(cx + 2, cy - 2, cx + 2, cy + 5, c=c)]

    def _ic_search(self, cx, cy, c):
        return [self._ov(cx - 9, cy - 9, cx + 3, cy + 3, c=c),
                self._ln(cx + 2, cy + 2, cx + 9, cy + 9, c=c)]

    def _ic_sync(self, cx, cy, c):
        ids = [self.canvas.create_arc(cx - 9, cy - 9, cx + 9, cy + 9,
                                      start=55, extent=250, style="arc",
                                      outline=c, width=2)]
        ids.append(self._ln(cx + 6, cy - 9, cx + 9, cy - 4, cx + 3, cy - 3, c=c))
        return ids

    def _ic_clock(self, cx, cy, c):
        return [self._ov(cx - 9, cy - 9, cx + 9, cy + 9, c=c),
                self._ln(cx, cy, cx, cy - 5, c=c),
                self._ln(cx, cy, cx + 5, cy + 2, c=c)]

    def _ic_home(self, cx, cy, c):
        return [self._ln(cx - 9, cy + 1, cx, cy - 8, cx + 9, cy + 1, c=c),
                self._ln(cx - 6, cy + 1, cx - 6, cy + 9, cx + 6, cy + 9,
                         cx + 6, cy + 1, c=c)]

    def _ic_chat(self, cx, cy, c):
        return [self._round_rect(cx - 9, cy - 8, cx + 9, cy + 4, 4,
                                 outline=c, width=2, fill=""),
                self._ln(cx - 3, cy + 4, cx - 6, cy + 9, cx + 1, cy + 4, c=c)]

    def _ic_person(self, cx, cy, c):
        return [self._ov(cx - 4, cy - 9, cx + 4, cy - 1, c=c),
                self.canvas.create_arc(cx - 8, cy - 1, cx + 8, cy + 15,
                                       start=20, extent=140, style="arc",
                                       outline=c, width=2)]

    def _chevron(self, cx, cy):
        return [self._ln(cx - 2, cy - 5, cx + 3, cy, cx - 2, cy + 5,
                         c=SUBT, w=2)]

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        self._setup_style()
        C = self.canvas = tk.Canvas(self.root, width=WIN_SIZE, height=WIN_SIZE,
                                    highlightthickness=0, bg=APP_BG)
        C.pack(fill="both", expand=True)

        # ===== 헤더 =====
        self._ov(30, 36, 76, 82, c=CARD_GRAY, w=1, fill=CARD_GRAY)
        self._ic_person(53, 60, ICON)
        C.create_text(92, 49, anchor="w", text="폴더 동기화",
                      fill=TEXT, font=self.font_title)
        self.status_var = _PlainVar("SSD → D드라이브 동기화")
        self._subtitle_id = C.create_text(
            92, 74, anchor="w", text=self.status_var.get(),
            fill=SUBT, font=self.font_small)
        # 오렌지 동기화 버튼
        self._orange_id = self._ov(648, 36, 696, 84, c=ORANGE, w=1, fill=ORANGE)
        sync_ic = self._ic_sync(672, 60, WHITE)
        self._bind([self._orange_id] + sync_ic, self.start_sync, guarded=True)

        # ===== 빠른 작업 버튼 4개 =====
        quick = [("원본", self._ic_folder, self.browse_src),
                 ("대상", self._ic_folder, self.browse_dst),
                 ("추가", self._ic_plus, self.add_pair),
                 ("제거", self._ic_trash, self.remove_pair)]
        qx, qy0, qy1, gap = 24, 100, 182, 14
        qw = (WIN_SIZE - 2 * qx - 3 * gap) / 4
        for i, (label, icon, cmd) in enumerate(quick):
            x = qx + i * (qw + gap)
            cxc = x + qw / 2
            rid = self._round_rect(x, qy0, x + qw, qy1, 18, fill=CARD_GRAY, outline="")
            ic = icon(cxc, 132, ICON_DK)
            tid = C.create_text(cxc, 165, text=label, fill=TEXT, font=self.font_small)
            self._bind([rid] + ic + [tid], cmd)

        # ===== 새 폴더 쌍(현재 선택) 카드 =====
        self._round_rect(24, 196, 696, 258, 16, fill=CARD, outline="")
        r1 = C.create_rectangle(40, 197, 680, 226, fill=CARD, outline="")
        sl = C.create_text(46, 212, anchor="w", text="원본", fill=SUBT, font=self.font_small)
        self._src_val_id = C.create_text(
            110, 212, anchor="w", text="선택 안 됨", fill=SUBT, font=self.font_n)
        ch1 = self._chevron(672, 212)
        self._bind([r1, sl, self._src_val_id] + ch1, self.browse_src)
        C.create_line(46, 227, 674, 227, fill=DIVIDER)
        r2 = C.create_rectangle(40, 229, 680, 257, fill=CARD, outline="")
        dl = C.create_text(46, 243, anchor="w", text="대상", fill=SUBT, font=self.font_small)
        self._dst_val_id = C.create_text(
            110, 243, anchor="w", text="선택 안 됨", fill=SUBT, font=self.font_n)
        ch2 = self._chevron(672, 243)
        self._bind([r2, dl, self._dst_val_id] + ch2, self.browse_dst)

        # ===== 폴더 목록 카드 =====
        C.create_text(34, 276, anchor="w", text="동기화 폴더",
                      fill=SUBT, font=self.font_small)
        self._count_id = C.create_text(686, 276, anchor="e", text="0개",
                                       fill=SUBT, font=self.font_small)
        self._round_rect(24, 288, 696, 380, 16, fill=CARD, outline="")
        self.tree = ttk.Treeview(C, show="tree", style="Sync.Treeview", height=3)
        self.tree.column("#0", width=632, anchor="w")
        self.tree.bind("<Double-1>", self.load_selected)
        C.create_window(34, 296, anchor="nw", window=self.tree,
                        width=652, height=76)

        # ===== 설정 카드 =====
        self._round_rect(24, 396, 696, 520, 16, fill=CARD, outline="")
        self._del_val_id = self._setting_row(
            396, "원본에서 삭제된 파일도 삭제", self._toggle_delete, divider=True)
        self._cfm_val_id = self._setting_row(
            437, "삭제 전 확인", self._toggle_confirm, divider=True)
        self._sched_val_id = self._setting_row(
            478, "예약 실행", self._open_schedule, chevron=True)

        # ===== 진행 표시줄 + 상태 + 로그 =====
        self.progress = ttk.Progressbar(C, mode="determinate",
                                        style="Sync.Horizontal.TProgressbar")
        C.create_window(360, 536, window=self.progress, width=672, height=6)

        self._round_rect(24, 548, 696, 612, 14, fill=CARD, outline="")
        self.log = tk.Text(C, wrap="none", height=3, state="disabled",
                           bg=CARD, fg=TEXT, insertbackground=TEXT,
                           relief="flat", bd=0, highlightthickness=0,
                           font=self.font_mono)
        C.create_window(36, 556, anchor="nw", window=self.log,
                        width=648, height=50)

        # ===== 하단 내비게이션 =====
        self._build_nav()

        self._refresh_selection_labels()
        self._update_sched_status()

    def _setting_row(self, y, title, command, divider=False, chevron=False):
        """설정 카드의 한 줄을 그리고, 우측 값 텍스트 id 를 돌려준다."""
        C = self.canvas
        hit = C.create_rectangle(40, y + 1, 680, y + 40, fill=CARD, outline="")
        t = C.create_text(46, y + 20, anchor="w", text=title,
                          fill=TEXT, font=self.font_n)
        vx = 648 if chevron else 674
        val = C.create_text(vx, y + 20, anchor="e", text="꺼짐",
                            fill=SUBT, font=self.font_n)
        ids = [hit, t, val]
        if chevron:
            ids += self._chevron(674, y + 20)
        self._bind(ids, command)
        if divider:
            C.create_line(46, y + 40, 674, y + 40, fill=DIVIDER)
        return val

    def _build_nav(self):
        C = self.canvas
        y = 660
        items = [("미리보기", self._ic_search, self.preview, 70),
                 ("예약", self._ic_clock, self._open_schedule, 178),
                 ("동기화", self._ic_sync, self.start_sync, 542),
                 ("비우기", self._ic_trash, self.clear_pairs, 650)]
        for label, icon, cmd, x in items:
            ic = icon(x, y, ICON)
            tid = C.create_text(x, y + 24, text=label, fill=SUBT, font=self.font_small)
            self._bind(ic + [tid], cmd, guarded=(cmd in (self.preview, self.start_sync)))
        # 가운데 큰 + 버튼 (추가)
        plus_bg = self._ov(330, y - 28, 390, y + 32, c=NAVBLACK, w=1, fill=NAVBLACK)
        plus_ic = self._ic_plus(360, y + 2, WHITE)
        self._bind([plus_bg] + plus_ic, self.add_pair)

    # ---------------- 예약 설정 팝업 ----------------
    def _open_schedule(self):
        if getattr(self, "_sched_win", None) is not None:
            try:
                if self._sched_win.winfo_exists():
                    self._sched_win.lift()
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        self._sched_win = win
        win.title("예약 설정")
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.transient(self.root)

        def L(parent, text, **kw):
            kw.setdefault("fg", TEXT)
            kw.setdefault("font", self.font_n)
            return tk.Label(parent, text=text, bg=CARD, **kw)

        def E(var, width):
            return tk.Entry(win, textvariable=var, width=width, bg=CARD_GRAY,
                            fg=TEXT, relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=DIVIDER, highlightcolor=ORANGE,
                            font=self.font_n)

        def R(text, value):
            return tk.Radiobutton(win, text=text, variable=self.sched_mode,
                                  value=value, bg=CARD, fg=TEXT, selectcolor=CARD,
                                  activebackground=CARD, font=self.font_n)

        pad = dict(padx=16)
        tk.Checkbutton(win, text="예약 실행 사용", variable=self.sched_enabled,
                       bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD,
                       font=self.font_b).grid(row=0, column=0, columnspan=3,
                                              sticky="w", pady=(16, 6), **pad)
        R("반복", "interval").grid(row=1, column=0, sticky="w", **pad)
        E(self.sched_interval, 5).grid(row=1, column=1, sticky="w")
        L(win, "분마다").grid(row=1, column=2, sticky="w")
        R("매일", "daily").grid(row=2, column=0, sticky="w", pady=6, **pad)
        E(self.sched_time, 7).grid(row=2, column=1, sticky="w")
        L(win, "에 (HH:MM)").grid(row=2, column=2, sticky="w")
        L(win, "※ 무인 운영 시 ‘삭제 전 확인’을 꺼 두세요.",
          fg=SUBT, font=self.font_small).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 4), **pad)

        def save():
            self._persist()
            self._update_sched_status()
            win.destroy()
            self._sched_win = None

        tk.Button(win, text="저장", command=save, bg=NAVBLACK, fg=WHITE,
                  relief="flat", bd=0, padx=18, pady=6, cursor="hand2",
                  font=self.font_b).grid(row=4, column=0, columnspan=3,
                                         sticky="e", padx=16, pady=(4, 16))
        win.bind("<Destroy>", lambda e: setattr(self, "_sched_win", None))

    # ---------------- 폴더 선택 / 쌍 관리 ----------------
    @staticmethod
    def _short(path, n=58):
        if len(path) <= n:
            return path
        return "…" + path[-(n - 1):]

    def _refresh_selection_labels(self):
        s = self.src_input.get().strip()
        d = self.dst_input.get().strip()
        self.canvas.itemconfig(self._src_val_id,
                               text=self._short(s) if s else "선택 안 됨",
                               fill=TEXT if s else SUBT)
        self.canvas.itemconfig(self._dst_val_id,
                               text=self._short(d) if d else "선택 안 됨",
                               fill=TEXT if d else SUBT)

    def browse_src(self):
        p = filedialog.askdirectory(
            title="원본 폴더(SSD) 선택",
            initialdir=self.src_input.get() or os.path.expanduser("~"))
        if p:
            self.src_input.set(p)
            self._refresh_selection_labels()

    def browse_dst(self):
        p = filedialog.askdirectory(
            title="대상 폴더(D드라이브) 선택",
            initialdir=self.dst_input.get() or os.path.expanduser("~"))
        if p:
            self.dst_input.set(p)
            self._refresh_selection_labels()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for src, dst in self.pairs:
            name = os.path.basename(src.rstrip("/\\")) or src
            self.tree.insert("", "end",
                             text=f"  {name}   →   {self._short(dst, 40)}")
        if hasattr(self, "_count_id"):
            self.canvas.itemconfig(self._count_id, text=f"{len(self.pairs)}개")

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
        self._refresh_selection_labels()

    def load_selected(self, event=None):
        """목록에서 더블클릭한 쌍을 위 칸으로 불러오고 목록에서 제거(수정용)."""
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
        self._refresh_selection_labels()

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

    # ---------------- 설정 토글 ----------------
    def _toggle_delete(self):
        self.delete_var.set(not self.delete_var.get())
        self._persist()
        self._refresh_setting_labels()

    def _toggle_confirm(self):
        self.confirm_delete_var.set(not self.confirm_delete_var.get())
        self._persist()
        self._refresh_setting_labels()

    def _refresh_setting_labels(self):
        for vid, on in ((self._del_val_id, self.delete_var.get()),
                        (self._cfm_val_id, self.confirm_delete_var.get())):
            self.canvas.itemconfig(vid, text="켜짐" if on else "꺼짐",
                                   fill=ORANGE if on else SUBT)

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
        # 설정 카드의 다른 값들도 함께 최신화
        self._refresh_setting_labels()
        if not self.sched_enabled.get():
            text, color = "꺼짐", SUBT
        elif self.sched_mode.get() == "interval":
            text, color = f"{self.sched_interval.get()}분마다", ORANGE
        else:
            text, color = f"매일 {self.sched_time.get().strip()}", ORANGE
        self.canvas.itemconfig(self._sched_val_id, text=text, fill=color)

    # ---------------- 로그 / 스레드→UI 전달 ----------------
    def log_msg(self, msg):
        self.log_queue.put(msg)

    def _ui(self, fn):
        """백그라운드 스레드에서 UI 갱신을 메인 스레드에 위임한다."""
        self._ui_queue.put(fn)

    def set_status(self, text):
        self.status_var.set(text)
        self._ui(lambda: self.canvas.itemconfig(self._subtitle_id, text=text))

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
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
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
        self.set_status("변경사항 분석 중...")
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
            self.set_status("미리보기 완료")
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
        self.set_status("변경사항 분석 중...")
        self._set_buttons(False)
        # Tk 변수는 메인 스레드에서 미리 읽어 일반 값으로 워커에 전달
        delete_enabled = self.delete_var.get()
        confirm_delete = self.confirm_delete_var.get()
        threading.Thread(target=self._sync_worker,
                         args=(pairs, delete_enabled, confirm_delete),
                         daemon=True).start()

    def _sync_worker(self, pairs, delete_enabled, confirm_delete):
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
            if delete_enabled and all_deletes:
                if confirm_delete:
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
            self.set_status(
                f"완료: 추가 {copied}, 변경 {updated}, 삭제 {deleted}, 오류 {errors}")
        except Exception as e:
            self.log_msg(f"[치명적 오류] {e}")
            self.set_status("오류로 중단됨")
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

        self._ui(ask)
        event.wait()
        return result["ok"]

    # ---------------- UI 상태 헬퍼 (스레드 안전, 메인 스레드에서 실행) ----------------
    def _set_buttons(self, enabled):
        # 동기화 중에는 오렌지 버튼을 흐리게 표시 (실제 차단은 busy 플래그가 담당)
        self._ui(lambda: self.canvas.itemconfig(
            self._orange_id, fill=ORANGE if enabled else "#F8C49B"))

    def _set_progress_max(self, total):
        self._ui(lambda: self.progress.configure(maximum=max(total, 1), value=0))

    def _set_progress(self, value):
        self._ui(lambda: self.progress.configure(value=value))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
