#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
폴더 동기화 프로그램

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
import hashlib
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

import customtkinter as ctk

# 설정 파일 (등록한 폴더 쌍과 옵션을 기억)
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".folder_sync_config.json")

# ----- 색상 테마 (쿨 그레이 뉴모피즘 + 네이비 포인트, 확정 시안 2a) -----
BG = "#EBECE7"          # 쿨 그레이 배경
CARD = "#F1F2ED"        # 배경보다 살짝 밝게 떠 있는 카드
CARD2 = "#F1F2ED"       # 일반 버튼 바탕(카드와 동일 톤)
INSET = "#E3E5DE"       # 안으로 들어간 입력칸/리스트/로그
SHADOW = "#D5D8CF"      # 부드러운 그림자 근사(경계)
HILIGHT = "#FBFBF8"     # 밝은 하이라이트(경계)
TEAL = "#2E3A59"        # 포인트 네이비 (글씨·채움·아이콘)
TEAL_DARK = "#3B4A70"   # hover 시 살짝 밝은 네이비
TEAL_SOFT = "#A24A3F"   # 멈추기(위험) 버튼의 차분한 레드
TEXT = "#2E3A59"        # 라벨·제목 = 네이비
MUTED = "#9A907C"       # 보조 텍스트(웜 그레이)
LOG_TEXT = "#6E6754"    # 입력칸/로그 본문
PRIMARY_FILL = "#2E3A59"  # 강조 버튼: 네이비 채움
PRIMARY_HOVER = "#3B4A70"
PRIMARY_TEXT = "#F5F1E8"  # 네이비 버튼 위 밝은 크림 글씨
BTN_HOVER = "#E7E9E1"     # 일반 버튼 hover
STOP_HOVER = "#EFE7E2"    # 멈추기(외곽선) hover

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


# 동기화에서 항상 제외할 OS 잔여물 파일(윈도/맥 시스템 파일, 오피스 임시 파일 등).
EXCLUDE_FILE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
EXCLUDE_FILE_PREFIXES = ("~$",)


def parse_exclude_words(text):
    """콤마로 구분된 제외 단어 문자열을 소문자 단어 리스트로 변환한다."""
    if not text:
        return []
    words = []
    for chunk in str(text).replace("\n", ",").split(","):
        w = chunk.strip().lower()
        if w:
            words.append(w)
    return words


def _is_excluded_dir(name, extra_words=(), include_words=()):
    """폴더를 제외 대상으로 볼지 판단.

    - include_words(예외/허용 단어)를 이름에 포함하면 제외하지 않는다(우선).
    - 그 외에는 extra_words(제외 단어)를 포함하면 제외.
    """
    low = name.lower()
    if any(w in low for w in include_words):
        return False
    return any(w in low for w in extra_words)


def _is_excluded_file(name, extra_words=(), include_words=()):
    """파일을 제외 대상으로 볼지 판단.

    - OS 잔여물(desktop.ini 등)은 항상 제외.
    - include_words(예외/허용 단어)를 포함하면 제외하지 않는다.
    - 그 외에는 extra_words(제외 단어)를 포함하면 제외.
    """
    low = name.lower()
    if low in EXCLUDE_FILE_NAMES or any(
            low.startswith(p) for p in EXCLUDE_FILE_PREFIXES):
        return True
    if any(w in low for w in include_words):
        return False
    return any(w in low for w in extra_words)


def list_relative_files(root, exclude_dirs=(), exclude_files=(),
                        include_dirs=(), include_files=()):
    """root 아래의 모든 파일을 root 기준 상대경로 set 으로 반환(제외 항목 제외)."""
    result = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not _is_excluded_dir(d, exclude_dirs, include_dirs)]
        for name in filenames:
            if _is_excluded_file(name, exclude_files, include_files):
                continue
            full = os.path.join(dirpath, name)
            result.add(os.path.relpath(full, root))
    return result


def list_relative_dirs(root, exclude_dirs=(), exclude_files=(),
                       include_dirs=(), include_files=()):
    """root 아래의 모든 하위 폴더를 root 기준 상대경로 set 으로 반환(제외 폴더 제외)."""
    result = set()
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not _is_excluded_dir(d, exclude_dirs, include_dirs)]
        for name in dirnames:
            full = os.path.join(dirpath, name)
            result.add(os.path.relpath(full, root))
    return result


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


# 같은 이름의 파일이 대상에 있을 때의 처리 방식.
#   skip  : 무조건 건너뛰기(덮어쓰지 않음)
#   diff  : 크기 또는 수정일자가 다르면 덮어쓰기
#   newer : 원본이 대상보다 더 최근(수정시간)일 때만 덮어쓰기
#   size  : 파일 크기가 다를 때만 덮어쓰기
CONFLICT_MODES = ("skip", "diff", "newer", "size")


def should_overwrite(src, dst, mode):
    """같은 이름의 파일에 대해 mode 에 따라 덮어쓸지 결정한다."""
    if mode == "skip":
        return False
    try:
        s = os.stat(src)
        d = os.stat(dst)
    except OSError:
        # 상태를 못 읽으면 안전하게 덮어쓰기(복사) 시도
        return True
    if mode == "size":
        return s.st_size != d.st_size
    if mode == "newer":
        return s.st_mtime - d.st_mtime > MTIME_TOLERANCE
    # 기본 "diff": 크기 또는 수정시간이 다르면 덮어쓰기
    if s.st_size != d.st_size:
        return True
    return abs(s.st_mtime - d.st_mtime) > MTIME_TOLERANCE


class SyncPlan:
    """소스/대상을 비교해서 해야 할 작업 목록을 만든다.

    conflict_mode 는 같은 이름의 파일이 대상에 있을 때의 처리 방식.
    (skip / diff / newer / size — CONFLICT_MODES 참고)
    """

    def __init__(self, src, dst, conflict_mode="diff",
                 exclude_dirs=(), exclude_files=(),
                 include_dirs=(), include_files=()):
        self.src = src
        self.dst = dst
        self.conflict_mode = conflict_mode if conflict_mode in CONFLICT_MODES \
            else "diff"
        # 이름에 포함되면 원본/대상 양쪽 모두 건너뛸 단어 목록
        self.exclude_dirs = tuple(exclude_dirs)
        self.exclude_files = tuple(exclude_files)
        # 제외 단어가 있어도 이 단어를 포함하면 동기화 대상으로 인정(예외/허용)
        self.include_dirs = tuple(include_dirs)
        self.include_files = tuple(include_files)
        self.to_copy = []        # 새로 추가된 파일
        self.to_update = []      # 변경되어 덮어쓸 파일
        self.to_skip = []        # 동일/건너뛸 파일
        self.to_delete = []      # 대상에만 있어 삭제할 파일
        self.dirs_to_delete = []  # 대상에만 있어 삭제할 폴더

    def build(self):
        src_files = list_relative_files(
            self.src, self.exclude_dirs, self.exclude_files,
            self.include_dirs, self.include_files)
        dst_files = list_relative_files(
            self.dst, self.exclude_dirs, self.exclude_files,
            self.include_dirs, self.include_files) \
            if os.path.isdir(self.dst) else set()

        for rel in sorted(src_files):
            s = os.path.join(self.src, rel)
            d = os.path.join(self.dst, rel)
            if rel not in dst_files:
                self.to_copy.append(rel)
            elif should_overwrite(s, d, self.conflict_mode):
                self.to_update.append(rel)
            else:
                self.to_skip.append(rel)

        for rel in sorted(dst_files - src_files):
            self.to_delete.append(rel)

        src_dirs = list_relative_dirs(
            self.src, self.exclude_dirs, self.exclude_files,
            self.include_dirs, self.include_files)
        dst_dirs = list_relative_dirs(
            self.dst, self.exclude_dirs, self.exclude_files,
            self.include_dirs, self.include_files) \
            if os.path.isdir(self.dst) else set()
        self.dirs_to_delete = sorted(dst_dirs - src_dirs, reverse=True)


# ===================== 이름 일괄 변경 =====================
# 지정한 루트 폴더 아래의 폴더 또는 파일 이름을 일괄로 바꾼다.
#   recursive=False : 루트 "바로 아래"의 항목만
#   recursive=True  : 하위 폴더 안의 항목까지 모두

def list_immediate_names(root, want_dir):
    """root 바로 아래의 이름 목록(정렬). want_dir=True면 폴더만, False면 파일만."""
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in sorted(names):
        p = os.path.join(root, name)
        try:
            is_dir = os.path.isdir(p)
        except OSError:
            continue
        if is_dir == want_dir:
            out.append(name)
    return out


def _rename_target(old, want_dir, op, new_name, find, replace):
    """한 항목의 원하는(충돌 해소 전) 새 이름을 계산한다."""
    if op == "set":
        if want_dir:
            return new_name
        # 파일은 확장자를 보존하고 이름 부분만 교체
        _stem, ext = os.path.splitext(old)
        return new_name + ext
    # op == "replace": 이름 안의 특정 단어를 다른 단어로 치환
    if not find:
        return old
    return old.replace(find, replace)


def _resolve_dir_renames(dir_path, want_dir, op, new_name, find, replace):
    """한 폴더(dir_path) 안에서 바뀔 (old_basename, new_basename) 목록을 만든다.

    같은 이름으로 겹치거나 남는 항목과 충돌하면 앞에 "1_", "2_" … 를 붙여 구분한다.
    """
    try:
        all_names = set(os.listdir(dir_path))
    except OSError:
        return []
    names = list_immediate_names(dir_path, want_dir)
    desired = [(old, _rename_target(old, want_dir, op, new_name, find, replace))
               for old in names]

    if op == "replace":
        changing = [(o, n) for o, n in desired if n and n != o]
    else:  # set
        changing = [(o, n) for o, n in desired if n]

    moving_olds = {o for o, _ in changing}
    reserved = set(all_names) - moving_olds   # 이동하지 않고 남는 이름들

    dup_count = {}
    for _o, n in changing:
        dup_count[n] = dup_count.get(n, 0) + 1

    taken = set(reserved)
    counters = {}
    result = []
    for old, new in changing:
        if dup_count[new] > 1 or new in taken:
            k = counters.get(new, 0) + 1
            cand = f"{k}_{new}"
            while cand in taken:
                k += 1
                cand = f"{k}_{new}"
            counters[new] = k
            final = cand
        else:
            final = new
        taken.add(final)
        if final != old:
            result.append((old, final))
    return result


def build_rename_plan(root, want_dir, op, new_name="", find="", replace="",
                      recursive=False):
    """(old_rel, new_rel) 변경 목록을 만든다(경로는 root 기준 상대경로).

    recursive=True 면 하위 폴더 안의 항목까지 포함한다.
    이름 충돌은 각 폴더 안에서 "1_", "2_" … 로 구분한다.
    """
    if op == "set" and not new_name:
        return []
    if op == "replace" and not find:
        return []

    if recursive:
        dirs = [dp for dp, _dn, _fn in os.walk(root)]
    else:
        dirs = [root]

    result = []
    for d in dirs:
        for old, new in _resolve_dir_renames(
                d, want_dir, op, new_name, find, replace):
            old_rel = os.path.relpath(os.path.join(d, old), root)
            new_rel = os.path.relpath(os.path.join(d, new), root)
            result.append((old_rel, new_rel))
    return result


def apply_rename_plan(root, changes):
    """changes: [(old_rel, new_rel), …] 를 실제로 적용한다.

    - 폴더별로 묶어 2단계(임시 이름 경유)로 바꿔 형제 이름 충돌을 피하고,
    - 깊은 폴더부터 처리해 상위 폴더가 먼저 바뀌어 경로가 어긋나는 일을 막는다.
    (done_count, errors) 를 돌려준다. errors: [(old_rel, new_rel, 사유), …]
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for old_rel, new_rel in changes:
        parent = os.path.dirname(old_rel)
        groups[parent].append((os.path.basename(old_rel),
                               os.path.basename(new_rel)))

    def depth(p):
        return len(p.split(os.sep)) if p else 0

    done = 0
    errors = []
    for parent in sorted(groups, key=depth, reverse=True):
        d = os.path.join(root, parent) if parent else root
        temps = []
        for i, (old, new) in enumerate(groups[parent]):
            tmp = f".__bulk_rename_tmp_{i}__"
            try:
                os.rename(os.path.join(d, old), os.path.join(d, tmp))
                temps.append((tmp, new, old))
            except OSError as e:
                errors.append((os.path.join(parent, old),
                               os.path.join(parent, new), str(e)))
        for tmp, new, old in temps:
            try:
                os.rename(os.path.join(d, tmp), os.path.join(d, new))
                done += 1
            except OSError as e:
                errors.append((os.path.join(parent, old),
                               os.path.join(parent, new), str(e)))
                try:   # 실패하면 원래 이름으로 되돌린다
                    os.rename(os.path.join(d, tmp), os.path.join(d, old))
                except OSError:
                    pass
    return done, errors


# ===================== 파일 일괄 잠금 =====================
# 특정 단어가 이름에 든 파일을 비밀번호로 잠근다(암호화).
#   - 잠긴 파일은 원래 이름 뒤에 ".locked" 가 붙고 내용이 암호화되어
#     그대로는 열거나 실행할 수 없다.
#   - 전체 "잠금 ON"  : 관리 대상 파일이 모두 잠긴(암호화) 상태
#   - 전체 "잠금 OFF" : 관리 대상 파일이 모두 풀린(복호화) 상태 → 비번 없이 열기 가능
# 표준 라이브러리(hashlib, os)만 사용한다. 비밀번호 자체는 저장하지 않고,
# 확인용 검증값(verifier)과 소금값(salt)만 설정 파일에 남긴다.

LOCK_EXT = ".locked"
LOCK_MAGIC = b"FLOCK1\0"
LOCK_ITERS = 200000


def lock_derive_key(password, salt):
    """비밀번호 + salt 에서 32바이트 키를 만든다(PBKDF2-HMAC-SHA256)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, LOCK_ITERS)


def lock_make_verifier(password):
    """새 비밀번호에 대한 (salt_hex, verifier_hex, key) 를 만든다."""
    salt = os.urandom(16)
    key = lock_derive_key(password, salt)
    verifier = hashlib.sha256(key + b"verify").hexdigest()
    return salt.hex(), verifier, key


def lock_check_password(password, salt_hex, verifier_hex):
    """비밀번호가 맞으면 key(bytes) 를, 틀리면 None 을 돌려준다."""
    try:
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return None
    key = lock_derive_key(password, salt)
    if hashlib.sha256(key + b"verify").hexdigest() == verifier_hex:
        return key
    return None


def lock_key_matches_verifier(key, verifier_hex):
    """이미 가지고 있는 key 가 현재 검증값과 맞는지 확인한다."""
    return bool(key) and bool(verifier_hex) and \
        hashlib.sha256(key + b"verify").hexdigest() == verifier_hex


def _lock_keystream(key, nonce, length):
    """key + nonce + 카운터 를 SHA-256 으로 이어붙여 keystream 을 만든다."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _lock_xor(data, key, nonce):
    if not data:
        return b""
    ks = _lock_keystream(key, nonce, len(data))
    n = int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")
    return n.to_bytes(len(data), "big")


def lock_encrypt_file(path, key):
    """path 를 암호화하여 path + LOCK_EXT 로 만들고 원본을 지운다. 잠긴 경로 반환."""
    locked = path + LOCK_EXT
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path).encode("utf-8")
    nonce = os.urandom(16)
    cipher = _lock_xor(data, key, nonce)
    tmp = locked + ".tmp"
    with open(tmp, "wb") as f:
        f.write(LOCK_MAGIC)
        f.write(len(name).to_bytes(2, "big"))
        f.write(name)
        f.write(nonce)
        f.write(cipher)
    os.replace(tmp, locked)     # 잠금 파일을 안전하게 만든 뒤에
    os.remove(path)             # 원본을 지운다
    return locked


def _lock_read(locked_path, key):
    """잠금 파일을 읽어 (원래이름, 복호화된 내용) 을 돌려준다."""
    with open(locked_path, "rb") as f:
        blob = f.read()
    if blob[:len(LOCK_MAGIC)] != LOCK_MAGIC:
        raise ValueError("잠금 파일 형식이 아닙니다.")
    i = len(LOCK_MAGIC)
    name_len = int.from_bytes(blob[i:i + 2], "big"); i += 2
    name = blob[i:i + name_len].decode("utf-8"); i += name_len
    nonce = blob[i:i + 16]; i += 16
    data = _lock_xor(blob[i:], key, nonce)
    return name, data


def lock_decrypt_file(locked_path, key):
    """locked_path 를 복호화해 원래 파일로 되돌리고 .locked 를 지운다. 원본 경로 반환."""
    name, data = _lock_read(locked_path, key)
    original = os.path.join(os.path.dirname(locked_path), name)
    tmp = original + ".tmp_unlock"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, original)
    os.remove(locked_path)
    return original


def lock_decrypt_to_temp(locked_path, key):
    """복호화한 내용을 임시 파일에 쓰고 그 경로를 돌려준다(원본 .locked 는 그대로).

    잠금이 켜진(ON) 상태에서 파일을 '실행'만 할 때 쓴다. 원본 잠금은 유지된다.
    """
    name, data = _lock_read(locked_path, key)
    d = tempfile.mkdtemp(prefix="unlock_")
    out = os.path.join(d, name)
    with open(out, "wb") as f:
        f.write(data)
    return out


def find_files_with_word(root, word, recursive=False):
    """root 아래에서 이름에 word 가 든 파일 경로 목록을 만든다(.locked 는 제외)."""
    out = []
    if not word:
        return out
    if recursive:
        for dp, _dn, fns in os.walk(root):
            for fn in sorted(fns):
                if word in fn and not fn.endswith(LOCK_EXT):
                    out.append(os.path.join(dp, fn))
    else:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return out
        for fn in names:
            p = os.path.join(root, fn)
            if word in fn and not fn.endswith(LOCK_EXT) and os.path.isfile(p):
                out.append(p)
    return out


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
        root.title("폴더 동기화")
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
        # 이름에 이 단어를 포함하면 원본/대상 양쪽에서 건너뛴다(콤마로 구분).
        self.exclude_dirs_var = tk.StringVar(value=cfg.get("exclude_dirs", ""))
        self.exclude_files_var = tk.StringVar(value=cfg.get("exclude_files", ""))
        # 제외 단어가 있어도 이 단어를 포함하면 동기화 대상으로 인정(예외/허용).
        self.include_dirs_var = tk.StringVar(value=cfg.get("include_dirs", ""))
        self.include_files_var = tk.StringVar(value=cfg.get("include_files", ""))
        self.src_input = tk.StringVar()
        self.dst_input = tk.StringVar()

        self.sched_enabled = tk.BooleanVar(value=cfg.get("sched_enabled", False))
        self.sched_mode = tk.StringVar(value=cfg.get("sched_mode", "interval"))
        self.sched_time = tk.StringVar(value=cfg.get("sched_time", "03:00"))
        self.sched_interval = tk.StringVar(value=str(cfg.get("sched_interval", 60)))
        self._last_interval_run = time.time()
        self._last_daily_run_day = None

        self.busy = False
        self.compact = False               # 최소화(컴팩트) 모드 여부
        self._full_geometry = None         # 최소화 전 창 크기 기억
        self._cancel = threading.Event()   # 멈추기 요청
        self.log_queue = queue.Queue()
        self._ui_queue = queue.Queue()

        self.status_var = tk.StringVar(value="대기 중")
        self.count_var = tk.StringVar(value="동기화 폴더 0개")
        self.sched_status_var = tk.StringVar(value="")
        self.progress_var = tk.StringVar(value="")   # 진행률 % + 남은 시간

        # 이름 일괄 변경(별도 화면) 상태
        self.rn_root = tk.StringVar(value=cfg.get("rename_root", ""))
        self.rn_target = tk.StringVar(value="dir")     # dir=폴더 / file=파일
        self.rn_op = tk.StringVar(value="set")         # set=같은이름 / replace=단어치환
        self.rn_newname = tk.StringVar()
        self.rn_find = tk.StringVar()
        self.rn_replace = tk.StringVar()
        self.rn_recursive = tk.BooleanVar(value=False)  # 하위 폴더까지 포함

        # 파일 일괄 잠금 상태
        self.lk_word = tk.StringVar()                   # 잠글 파일 이름에 든 단어
        self._lock_key = None                           # 이번 실행 동안 기억하는 키
        self._lk_rows = None                            # 잠금 목록이 들어가는 프레임

        self._setup_fonts()
        self._setup_style()
        self._build_ui()
        self._build_rename_ui()
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
                        foreground=LOG_TEXT, borderwidth=0, relief="flat",
                        rowheight=rowh, font=self.font_n)
        style.map("Sync.Treeview", background=[("selected", TEAL)],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Sync.Vertical.TScrollbar", troughcolor=INSET,
                        background=SHADOW, bordercolor=INSET, arrowcolor=TEAL,
                        relief="flat", borderwidth=0)

    # ---------------- 위젯 헬퍼 (customtkinter, 뉴모피즘) ----------------
    def _card(self, parent, pady=(0, 7)):
        # 배경과 거의 같은 톤 + 옅은 경계선으로 부드럽게 떠 있는 느낌을 근사
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=SHADOW)
        card.pack(fill="x", pady=pady)
        return card

    def _button(self, parent, text, command, tooltip="", primary=False,
                danger=False, width=110):
        if danger:
            # 멈추기: 외곽선만 청록(연한 톤)
            opts = dict(fg_color="transparent", hover_color=STOP_HOVER,
                        text_color=TEAL_SOFT, border_width=2, border_color=TEAL_SOFT,
                        text_color_disabled=MUTED)
        elif primary:
            # 강조: 네이비 채움 + 밝은 크림 글씨 (시안 2a)
            opts = dict(fg_color=PRIMARY_FILL, hover_color=PRIMARY_HOVER,
                        text_color=PRIMARY_TEXT, border_width=0,
                        text_color_disabled=MUTED)
        else:
            # 일반: 옅은 회색 바탕 + 청록 글씨
            opts = dict(fg_color=CARD2, hover_color=BTN_HOVER, text_color=TEAL,
                        border_width=1, border_color=SHADOW, text_color_disabled=MUTED)
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
                            text_color=LOG_TEXT, border_color=SHADOW, border_width=1)

    def _check(self, parent, text, var):
        # 시안 2a: 체크박스 대신 토글 스위치
        return ctk.CTkSwitch(parent, text=text, variable=var,
                             command=self._persist, font=self.font_n,
                             text_color=TEXT, progress_color=TEAL,
                             fg_color=SHADOW, button_color="#FFFFFF",
                             button_hover_color=HILIGHT,
                             switch_width=40, switch_height=20)

    def _radio(self, parent, text, value):
        return ctk.CTkRadioButton(parent, text=text, variable=self.sched_mode,
                                  value=value, command=self._persist, font=self.font_n,
                                  text_color=TEXT, fg_color=TEAL, hover_color=TEAL_DARK,
                                  border_color=SHADOW, radiobutton_width=20,
                                  radiobutton_height=20)

    def _conflict_radio(self, parent, text, value):
        """같은 이름 파일 처리 방식(conflict_mode) 라디오 버튼."""
        return ctk.CTkRadioButton(parent, text=text, variable=self.conflict_mode,
                                  value=value, command=self._persist, font=self.font_n,
                                  text_color=TEXT, fg_color=TEAL, hover_color=TEAL_DARK,
                                  border_color=SHADOW, radiobutton_width=20,
                                  radiobutton_height=20)

    def _pradio(self, parent, text, var, value, command=None):
        """임의의 StringVar 에 연결되는 라디오 버튼."""
        return ctk.CTkRadioButton(parent, text=text, variable=var, value=value,
                                  command=command, font=self.font_n, text_color=TEXT,
                                  fg_color=TEAL, hover_color=TEAL_DARK,
                                  border_color=SHADOW, radiobutton_width=20,
                                  radiobutton_height=20)

    def _label(self, parent, text, font=None, fg=TEXT, bg=None):
        return ctk.CTkLabel(parent, text=text, font=font or self.font_n,
                            text_color=fg, fg_color="transparent")

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        main = self.page_sync = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        main.pack(fill="both", expand=True, padx=16, pady=12)
        self._main = main

        # 헤더: 제목 + 오른쪽 상태 칩 + 최소화 버튼
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        self._header = head
        self.title_box = tbox = ctk.CTkFrame(head, fg_color="transparent")
        tbox.pack(side="left")
        self._label(tbox, "폴더 동기화", font=self.font_title, fg=TEAL).pack(anchor="w")
        # 최소화/펼치기 토글 (누르면 진행률·로그·실행버튼만 남는 컴팩트 모드)
        self.min_btn = self._button(
            head, "▁  최소화", self.toggle_compact,
            "설정을 접고 진행률·로그·실행 버튼만 작은 창으로 표시합니다", width=96)
        self.min_btn.pack(side="right")
        # 이름 일괄 변경 화면으로 넘어가는 버튼
        self.nav_btn = self._button(
            head, "이름변경·잠금  ▸", self.show_rename,
            "폴더·파일 이름을 한 번에 바꾸고, 특정 파일을 잠그는 화면으로 이동합니다",
            width=124)
        self.nav_btn.pack(side="right", padx=(0, 8))
        self.status_chip = ctk.CTkLabel(
            head, textvariable=self.status_var, font=self.font_small,
            text_color=TEXT, fg_color=INSET, corner_radius=13, height=26, padx=12)
        self.status_chip.pack(side="right", padx=(0, 8))

        # 설정 영역(컴팩트 모드에서 통째로 숨김) — 아래 카드들을 담는 컨테이너
        self.full_frame = ctk.CTkFrame(main, fg_color="transparent")
        self.full_frame.pack(fill="x")
        full = self.full_frame

        # 폴더 선택 카드
        c = self._card(full)
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "원본 폴더").grid(row=0, column=0, sticky="w",
                                      padx=(14, 8), pady=(11, 5))
        self._entry(c, self.src_input).grid(row=0, column=1, sticky="ew", pady=(11, 5))
        self._button(c, "찾아보기", self.browse_src,
                     "동기화할 원본 폴더를 선택합니다", width=92).grid(
            row=0, column=2, padx=(8, 14), pady=(11, 5))
        self._label(c, "대상 폴더").grid(row=1, column=0, sticky="w",
                                      padx=(14, 8), pady=5)
        self._entry(c, self.dst_input).grid(row=1, column=1, sticky="ew", pady=5)
        self._button(c, "찾아보기", self.browse_dst,
                     "복사될 대상 폴더를 선택합니다", width=92).grid(
            row=1, column=2, padx=(8, 14), pady=5)
        self._button(c, "＋  목록에 추가", self.add_pair,
                     "위에서 고른 원본·대상 폴더를 동기화 목록에 추가합니다",
                     primary=True).grid(row=2, column=0, columnspan=3, sticky="ew",
                                        padx=14, pady=(5, 11))

        # 폴더 목록 카드
        c = self._card(full)
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
        c = self._card(full)
        self._label(c, "같은 이름의 파일이 대상에 있을 때",
                    font=self.font_b, fg=TEXT).pack(anchor="w", padx=14, pady=(10, 3))
        self._conflict_radio(c, "무조건 건너뛰기 (덮어쓰지 않음)", "skip").pack(
            anchor="w", padx=14, pady=1)
        self._conflict_radio(c, "크기 또는 수정일자가 다르면 덮어쓰기", "diff").pack(
            anchor="w", padx=14, pady=1)
        self._conflict_radio(c, "원본이 더 최근(수정날짜)일 때만 덮어쓰기", "newer").pack(
            anchor="w", padx=14, pady=1)
        self._conflict_radio(c, "파일 크기가 다를 때만 덮어쓰기", "size").pack(
            anchor="w", padx=14, pady=(1, 8))
        ctk.CTkFrame(c, height=1, fg_color=SHADOW).pack(fill="x", padx=14, pady=2)
        self._check(c, "원본에서 삭제된 파일을 대상에서도 삭제",
                    self.delete_var).pack(anchor="w", padx=14, pady=(6, 3))
        self._check(c, "삭제 전 확인 (켜면 삭제 직전에 한 번 물어봅니다)",
                    self.confirm_delete_var).pack(anchor="w", padx=14, pady=(0, 9))

        # 제외 카드 (특정 단어를 포함한 폴더명/파일명 건너뛰기)
        c = self._card(full)
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "동기화에서 제외할 이름 (원본·대상 모두 건너뜀)",
                    font=self.font_b, fg=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 3))
        self._label(c, "폴더 이름").grid(row=1, column=0, sticky="w",
                                     padx=(14, 8), pady=4)
        ex_dir_entry = self._entry(c, self.exclude_dirs_var)
        ex_dir_entry.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=4)
        Tooltip(ex_dir_entry,
                "이 단어를 이름에 포함하는 폴더는 통째로 건너뜁니다.\n"
                "콤마(,)로 여러 개 지정 (예: temp, 캐시, __pycache__)",
                self.font_small)
        self._label(c, "파일 이름").grid(row=2, column=0, sticky="w",
                                     padx=(14, 8), pady=4)
        ex_file_entry = self._entry(c, self.exclude_files_var)
        ex_file_entry.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=4)
        Tooltip(ex_file_entry,
                "이 단어를 이름에 포함하는 파일은 건너뜁니다.\n"
                "콤마(,)로 여러 개 지정 (예: .tmp, 사본, backup)",
                self.font_small)
        self._label(c, "콤마(,)로 여러 단어를 지정할 수 있습니다. 대소문자 구분 없음.",
                    font=self.font_small, fg=MUTED).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 6))
        ex_dir_entry.bind("<FocusOut>", lambda _e: self._persist())
        ex_file_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkFrame(c, height=1, fg_color=SHADOW).grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=2)
        self._label(c, "예외 — 제외 단어가 있어도 이 단어를 포함하면 동기화 대상",
                    font=self.font_b, fg=TEXT).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=14, pady=(6, 3))
        self._label(c, "폴더 이름").grid(row=6, column=0, sticky="w",
                                     padx=(14, 8), pady=4)
        inc_dir_entry = self._entry(c, self.include_dirs_var)
        inc_dir_entry.grid(row=6, column=1, sticky="ew", padx=(0, 14), pady=4)
        Tooltip(inc_dir_entry,
                "제외 단어에 걸리는 폴더라도 이 단어를 포함하면 동기화합니다.\n"
                "콤마(,)로 여러 개 지정 (예: 중요, keep)",
                self.font_small)
        self._label(c, "파일 이름").grid(row=7, column=0, sticky="w",
                                     padx=(14, 8), pady=4)
        inc_file_entry = self._entry(c, self.include_files_var)
        inc_file_entry.grid(row=7, column=1, sticky="ew", padx=(0, 14), pady=(4, 10))
        Tooltip(inc_file_entry,
                "제외 단어에 걸리는 파일이라도 이 단어를 포함하면 동기화합니다.\n"
                "콤마(,)로 여러 개 지정 (예: 최종, keep)",
                self.font_small)
        inc_dir_entry.bind("<FocusOut>", lambda _e: self._persist())
        inc_file_entry.bind("<FocusOut>", lambda _e: self._persist())

        # 예약 카드
        c = self._card(full)
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

        # 진행 카드: 원형 다이얼 + 퍼센트/남은 시간 (항상 표시)
        c = self.card_progress = self._card(main)
        pf = ctk.CTkFrame(c, fg_color="transparent")
        pf.pack(fill="x", padx=14, pady=10)
        self.dial = tk.Canvas(pf, width=64, height=64, bg=CARD,
                              highlightthickness=0, bd=0)
        self.dial.pack(side="left")
        self._set_dial(0.0)
        ctk.CTkLabel(pf, textvariable=self.progress_var, font=self.font_n,
                     text_color=TEXT, anchor="w", justify="left").pack(
            side="left", fill="x", expand=True, padx=(14, 0))

        # 로그 카드
        c = self._card(main, pady=(0, 8))
        holder = ctk.CTkFrame(c, fg_color=INSET, corner_radius=10)
        holder.pack(fill="both", expand=True, padx=10, pady=10)
        self.log = tk.Text(holder, height=3, font=self.font_log, bg=INSET, fg=LOG_TEXT,
                           relief="flat", bd=0, highlightthickness=0, wrap="none",
                           state="disabled")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        sb = ttk.Scrollbar(holder, command=self.log.yview,
                           style="Sync.Vertical.TScrollbar")
        sb.pack(side="right", fill="y", pady=6, padx=(0, 4))
        self.log.configure(yscrollcommand=sb.set)

        # 실행 버튼 (하단 바, 시안 2a) — 상태는 상단 칩에 표시
        af = ctk.CTkFrame(main, fg_color="transparent")
        af.pack(fill="x", pady=(2, 2))
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

    # ---------------- 최소화(컴팩트) 모드 ----------------
    def toggle_compact(self):
        """설정 카드들을 접거나 펼쳐 작은 창/큰 창을 전환한다."""
        self.compact = not self.compact
        if self.compact:
            # 현재(큰) 창 크기를 기억해 두었다가 펼칠 때 복원
            self._full_geometry = self.root.geometry()
            self.full_frame.pack_forget()
            self.title_box.pack_forget()
            self.nav_btn.pack_forget()
            self.min_btn.configure(text="▢  펼치기")
            self._resize_compact()
        else:
            # 설정 영역을 진행 카드 앞에 다시 끼워 넣는다
            self.full_frame.pack(fill="x", before=self.card_progress)
            self.title_box.pack(side="left")
            self.nav_btn.pack(side="right", padx=(0, 8))
            self.min_btn.configure(text="▁  최소화")
            self.root.minsize(520, 460)
            if self._full_geometry:
                self.root.geometry(self._full_geometry)

    def _resize_compact(self):
        """컴팩트 모드에서 내용 높이에 맞춰 창을 작게 줄인다."""
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        w = 460
        self.root.minsize(380, h)
        self.root.geometry(f"{w}x{h}")

    # ---------------- 이름 일괄 변경 화면 ----------------
    def _build_rename_ui(self):
        page = self.page_rename = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        # 처음엔 숨겨 둔다(show_rename 시 표시).

        # 헤더: 뒤로 가기 + 제목
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 8))
        self._button(head, "◂  동기화로", self.show_sync,
                     "폴더 동기화 화면으로 돌아갑니다", width=104).pack(side="left")
        self._label(head, "이름 일괄 변경 및 잠금 설정",
                    font=self.font_title, fg=TEAL).pack(side="left", padx=(12, 0))

        body = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # 대상 루트 폴더 카드
        c = self._card(body)
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "대상 루트 폴더").grid(row=0, column=0, sticky="w",
                                          padx=(14, 8), pady=(11, 4))
        self._entry(c, self.rn_root).grid(row=0, column=1, sticky="ew", pady=(11, 4))
        self._button(c, "찾아보기", self.browse_rename_root,
                     "이름을 바꿀 폴더들이 들어있는 상위 폴더를 선택합니다",
                     width=92).grid(row=0, column=2, padx=(8, 14), pady=(11, 4))
        self._label(c, "기본은 이 폴더 바로 아래의 항목만 바꿉니다. 아래 '재귀'를 켜면 "
                       "하위 폴더 안까지 모두 바꿉니다.", font=self.font_small,
                    fg=MUTED).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 11))

        # 대상 종류 카드
        c = self._card(body)
        self._label(c, "무엇의 이름을 바꿀까요?", font=self.font_b, fg=TEXT).pack(
            anchor="w", padx=14, pady=(10, 3))
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 6))
        self._pradio(row, "폴더 이름", self.rn_target, "dir").pack(side="left")
        self._pradio(row, "파일 이름", self.rn_target, "file").pack(
            side="left", padx=(24, 0))
        ctk.CTkSwitch(c, text="하위 폴더 안의 항목까지 포함 (재귀)",
                      variable=self.rn_recursive, font=self.font_n,
                      text_color=TEXT, progress_color=TEAL, fg_color=SHADOW,
                      button_color="#FFFFFF", button_hover_color=HILIGHT,
                      switch_width=40, switch_height=20).pack(
            anchor="w", padx=14, pady=(0, 10))

        # 변경 방식 카드
        c = self._card(body)
        c.grid_columnconfigure(0, weight=1)
        self._label(c, "변경 방식", font=self.font_b, fg=TEXT).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=14, pady=(10, 3))
        # (1) 같은 이름으로 일괄 설정
        self._pradio(c, "같은 이름으로 일괄 설정", self.rn_op, "set").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(2, 0))
        self._label(c, "새 이름").grid(row=2, column=0, sticky="w",
                                    padx=(38, 8), pady=4)
        self._entry(c, self.rn_newname, width=200).grid(
            row=2, column=1, sticky="w", pady=4)
        self._label(c, "이름이 겹치면 앞에 1_, 2_ … 자동으로 붙습니다 "
                       "(파일은 확장자 유지).", font=self.font_small, fg=MUTED).grid(
            row=3, column=0, columnspan=4, sticky="w", padx=(38, 14), pady=(0, 6))
        ctk.CTkFrame(c, height=1, fg_color=SHADOW).grid(
            row=4, column=0, columnspan=4, sticky="ew", padx=14, pady=4)
        # (2) 특정 단어 바꾸기
        self._pradio(c, "특정 단어를 다른 단어로 바꾸기", self.rn_op, "replace").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=14, pady=(4, 0))
        self._label(c, "바꿀 단어").grid(row=6, column=0, sticky="w",
                                     padx=(38, 8), pady=(4, 11))
        rrow = ctk.CTkFrame(c, fg_color="transparent")
        rrow.grid(row=6, column=1, columnspan=3, sticky="w", pady=(4, 11))
        self._entry(rrow, self.rn_find, width=150).pack(side="left")
        self._label(rrow, "→").pack(side="left", padx=10)
        self._entry(rrow, self.rn_replace, width=150).pack(side="left")

        # 실행 버튼
        af = ctk.CTkFrame(body, fg_color="transparent")
        af.pack(fill="x", pady=(2, 6))
        self._button(af, "미리보기", self.rename_preview,
                     "실제로 바꾸기 전에 어떤 이름이 바뀔지 먼저 확인합니다",
                     width=120).pack(side="left")
        self._button(af, "일괄 변경 실행", self.rename_apply,
                     "위 설정대로 이름을 실제로 바꿉니다", primary=True).pack(
            side="left", padx=(10, 0), fill="x", expand=True)

        # 결과/로그 카드
        c = self._card(body, pady=(0, 0))
        holder = ctk.CTkFrame(c, fg_color=INSET, corner_radius=10)
        holder.pack(fill="both", expand=True, padx=10, pady=10)
        self.rn_log = tk.Text(holder, height=8, font=self.font_log, bg=INSET,
                              fg=LOG_TEXT, relief="flat", bd=0, highlightthickness=0,
                              wrap="none", state="disabled")
        self.rn_log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        sb = ttk.Scrollbar(holder, command=self.rn_log.yview,
                           style="Sync.Vertical.TScrollbar")
        sb.pack(side="right", fill="y", pady=6, padx=(0, 4))
        self.rn_log.configure(yscrollcommand=sb.set)

        # ----- 파일 일괄 잠금 -----
        self._build_lock_ui(body)

    # ---------------- 파일 일괄 잠금 화면 ----------------
    def _build_lock_ui(self, body):
        # 구역 제목
        self._label(body, "특정 단어가 든 파일 일괄 잠금",
                    font=self.font_b, fg=TEAL).pack(anchor="w", pady=(12, 2))
        self._label(body, "위에서 고른 '대상 루트 폴더'와 '재귀' 설정을 그대로 사용합니다. "
                          "잠근 파일은 이름 뒤에 .locked 가 붙고 내용이 암호화됩니다.",
                    font=self.font_small, fg=MUTED).pack(anchor="w", pady=(0, 4))

        # 잠글 단어 + 실행
        c = self._card(body)
        c.grid_columnconfigure(1, weight=1)
        self._label(c, "잠글 단어").grid(row=0, column=0, sticky="w",
                                       padx=(14, 8), pady=11)
        self._entry(c, self.lk_word).grid(row=0, column=1, sticky="ew", pady=11)
        self._button(c, "일괄 잠금", self.lock_bulk,
                     "이름에 이 단어가 든 파일을 비밀번호로 한 번에 잠급니다",
                     primary=True, width=110).grid(row=0, column=2,
                                                   padx=(8, 14), pady=11)

        # 전체 잠금 ON/OFF 상태
        c = self._card(body)
        c.grid_columnconfigure(0, weight=1)
        self._lk_status_lbl = self._label(c, "", font=self.font_b, fg=TEXT)
        self._lk_status_lbl.grid(row=0, column=0, sticky="w", padx=14, pady=(11, 2))
        self.lk_toggle_btn = self._button(
            c, "잠금 OFF로", self.lock_toggle,
            "잠금을 끄면(OFF) 비번 없이 파일을 열 수 있습니다. 끄려면 비밀번호가 필요합니다",
            width=120)
        self.lk_toggle_btn.grid(row=0, column=1, padx=(8, 14), pady=(11, 2))
        self._label(c, "잠금 ON: 파일을 열 때마다 비밀번호가 필요합니다 · "
                       "잠금 OFF: 비밀번호 없이 바로 열 수 있습니다 "
                       "(OFF 로 바꾸려면 비밀번호 필요).",
                    font=self.font_small, fg=MUTED).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 11))

        # 잠금 관리 중인 파일 목록
        c = self._card(body, pady=(0, 0))
        self._label(c, "잠금 관리 중인 파일", font=self.font_b, fg=TEXT).pack(
            anchor="w", padx=14, pady=(11, 4))
        self._lk_rows = ctk.CTkScrollableFrame(
            c, fg_color=INSET, corner_radius=10, height=150)
        self._lk_rows.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._lock_refresh()

    # ---------------- 화면 전환 ----------------
    def show_rename(self):
        if self.compact:      # 컴팩트 상태면 먼저 펼친다
            self.toggle_compact()
        self._sync_geometry = self.root.geometry()
        self.page_sync.pack_forget()
        self.page_rename.pack(fill="both", expand=True)
        self.root.update_idletasks()
        w = min(self.root.winfo_reqwidth(), 820)
        h = self.root.winfo_reqheight()
        self.root.minsize(480, 420)
        self.root.geometry(f"{max(w, 480)}x{h}")

    def show_sync(self):
        self.page_rename.pack_forget()
        self.page_sync.pack(fill="both", expand=True, padx=16, pady=12)
        self.root.minsize(520, 460)
        if getattr(self, "_sync_geometry", None):
            self.root.geometry(self._sync_geometry)

    # ---------------- 이름 일괄 변경 동작 ----------------
    def browse_rename_root(self):
        p = filedialog.askdirectory(
            title="이름을 바꿀 폴더가 들어있는 상위 폴더 선택",
            initialdir=self.rn_root.get() or os.path.expanduser("~"))
        if p:
            self.rn_root.set(p)
            save_config({**load_config(), "rename_root": p})

    def _rn_clear(self):
        self.rn_log.configure(state="normal")
        self.rn_log.delete("1.0", "end")
        self.rn_log.configure(state="disabled")

    def _rn_write(self, msg):
        self.rn_log.configure(state="normal")
        self.rn_log.insert("end", msg + "\n")
        self.rn_log.see("end")
        self.rn_log.configure(state="disabled")

    def _current_rename_plan(self):
        """현재 화면 설정으로 (plan, 오류메시지) 를 만든다."""
        root = self.rn_root.get().strip()
        if not root or not os.path.isdir(root):
            return None, "대상 루트 폴더를 올바르게 지정하세요."
        want_dir = self.rn_target.get() == "dir"
        op = self.rn_op.get()
        recursive = self.rn_recursive.get()
        if op == "set":
            nm = self.rn_newname.get().strip()
            if not nm:
                return None, "새 이름을 입력하세요."
            plan = build_rename_plan(root, want_dir, "set", new_name=nm,
                                     recursive=recursive)
        else:
            find = self.rn_find.get()
            if not find.strip():
                return None, "바꿀 단어를 입력하세요."
            plan = build_rename_plan(root, want_dir, "replace",
                                     find=find, replace=self.rn_replace.get(),
                                     recursive=recursive)
        return plan, None

    def rename_preview(self):
        self._rn_clear()
        plan, err = self._current_rename_plan()
        if err:
            self._rn_write(err)
            return
        kind = "폴더" if self.rn_target.get() == "dir" else "파일"
        if not plan:
            self._rn_write(f"바뀌는 {kind}이(가) 없습니다.")
            return
        self._rn_write(f"[미리보기] 바뀔 {kind} {len(plan)}개")
        for old, new in plan:
            self._rn_write(f"  {old}   →   {new}")

    def rename_apply(self):
        self._rn_clear()
        plan, err = self._current_rename_plan()
        if err:
            self._rn_write(err)
            messagebox.showwarning("확인", err)
            return
        kind = "폴더" if self.rn_target.get() == "dir" else "파일"
        if not plan:
            self._rn_write(f"바뀌는 {kind}이(가) 없습니다.")
            messagebox.showinfo("안내", f"바뀌는 {kind}이(가) 없습니다.")
            return
        sample = "\n".join(f"{o}  →  {n}" for o, n in plan[:10])
        more = "" if len(plan) <= 10 else f"\n... 외 {len(plan) - 10}개"
        if not messagebox.askyesno(
                "일괄 변경 확인",
                f"{kind} {len(plan)}개의 이름을 바꿀까요?\n\n{sample}{more}"):
            return
        root = self.rn_root.get().strip()
        done, errors = apply_rename_plan(root, plan)
        for old, new in plan:
            self._rn_write(f"  {old}   →   {new}")
        self._rn_write("")
        self._rn_write(f"===== 완료: {done}개 변경 / 오류 {len(errors)}개 =====")
        for old, new, e in errors:
            self._rn_write(f"[오류] {old} → {new}: {e}")

    # ---------------- 파일 일괄 잠금 동작 ----------------
    def _lock_state(self):
        """설정 파일에서 잠금 상태를 읽어온다."""
        cfg = load_config()
        return {
            "files": cfg.get("lock_files", []),
            "salt": cfg.get("lock_salt", ""),
            "verifier": cfg.get("lock_verifier", ""),
            "on": bool(cfg.get("lock_on", False)),
        }

    def _lock_save(self, **kw):
        """잠금 관련 항목만 설정 파일에 저장한다(다른 설정은 그대로)."""
        mapping = {"files": "lock_files", "salt": "lock_salt",
                   "verifier": "lock_verifier", "on": "lock_on"}
        cfg = load_config()
        for k, v in kw.items():
            cfg[mapping[k]] = v
        save_config(cfg)

    def _password_modal(self, title, confirm=False, note=""):
        """비밀번호 입력 창을 띄우고 입력값(문자열) 또는 None(취소) 을 돌려준다."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        result = {"pw": None}

        frm = ctk.CTkFrame(win, fg_color=BG, corner_radius=0)
        frm.pack(fill="both", expand=True, padx=18, pady=16)
        self._label(frm, title, font=self.font_b).pack(anchor="w")
        if note:
            self._label(frm, note, font=self.font_small, fg=MUTED).pack(
                anchor="w", pady=(2, 8))

        v1 = tk.StringVar()
        v2 = tk.StringVar()
        e1 = ctk.CTkEntry(frm, textvariable=v1, show="●", width=240, height=30,
                          corner_radius=10, font=self.font_n, fg_color=INSET,
                          text_color=LOG_TEXT, border_color=SHADOW, border_width=1)
        e1.pack(pady=(6, 4))
        e2 = None
        if confirm:
            self._label(frm, "비밀번호 확인", font=self.font_small, fg=MUTED).pack(
                anchor="w", pady=(6, 0))
            e2 = ctk.CTkEntry(frm, textvariable=v2, show="●", width=240, height=30,
                              corner_radius=10, font=self.font_n, fg_color=INSET,
                              text_color=LOG_TEXT, border_color=SHADOW, border_width=1)
            e2.pack(pady=(2, 4))
        msg = self._label(frm, "", font=self.font_small, fg=TEAL_SOFT)
        msg.pack(anchor="w")

        def ok(*_a):
            p1 = v1.get()
            if not p1:
                msg.configure(text="비밀번호를 입력하세요.")
                return
            if confirm and p1 != v2.get():
                msg.configure(text="두 비밀번호가 일치하지 않습니다.")
                return
            result["pw"] = p1
            win.destroy()

        def cancel(*_a):
            win.destroy()

        btns = ctk.CTkFrame(frm, fg_color="transparent")
        btns.pack(fill="x", pady=(10, 0))
        self._button(btns, "취소", cancel, width=90).pack(side="right")
        self._button(btns, "확인", ok, primary=True, width=90).pack(
            side="right", padx=(0, 8))

        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.update_idletasks()
        # 부모 창 가운데에 띄운다
        px = self.root.winfo_rootx() + (self.root.winfo_width()
                                        - win.winfo_reqwidth()) // 2
        py = self.root.winfo_rooty() + 80
        win.geometry(f"+{max(px, 0)}+{max(py, 0)}")
        e1.focus_set()
        win.grab_set()
        self.root.wait_window(win)
        return result["pw"]

    def _lock_key_new_or_verify(self):
        """일괄 잠금용 키를 얻는다. 비번이 없으면 새로 설정하고, 있으면 확인한다."""
        st = self._lock_state()
        if st["verifier"]:
            pw = self._password_modal(
                "잠금 비밀번호 입력",
                note="이미 설정된 잠금 비밀번호를 입력하세요.")
            if pw is None:
                return None
            key = lock_check_password(pw, st["salt"], st["verifier"])
            if key is None:
                messagebox.showwarning("확인", "비밀번호가 올바르지 않습니다.")
                return None
            self._lock_key = key
            return key
        # 처음 잠그는 경우: 새 비밀번호 설정
        pw = self._password_modal(
            "새 잠금 비밀번호 설정", confirm=True,
            note="이 비밀번호로 파일을 잠급니다. 잊어버리면 되돌릴 수 없으니 주의하세요.")
        if pw is None:
            return None
        salt_hex, verifier, key = lock_make_verifier(pw)
        self._lock_save(salt=salt_hex, verifier=verifier)
        self._lock_key = key
        return key

    def _lock_key_verify(self, note="비밀번호를 입력하세요."):
        """잠금 비밀번호를 확인해 key 를 얻는다(설정된 비번이 없으면 None)."""
        st = self._lock_state()
        if not st["verifier"]:
            return None
        pw = self._password_modal("잠금 비밀번호 입력", note=note)
        if pw is None:
            return None
        key = lock_check_password(pw, st["salt"], st["verifier"])
        if key is None:
            messagebox.showwarning("확인", "비밀번호가 올바르지 않습니다.")
            return None
        self._lock_key = key
        return key

    def _lock_encrypt_all(self, key, files):
        """아직 잠기지 않은 관리 파일들을 모두 잠근다."""
        done, errors = 0, []
        for p in files:
            if os.path.exists(p + LOCK_EXT):
                continue                    # 이미 잠김
            if not os.path.exists(p):
                errors.append((p, "파일을 찾을 수 없습니다."))
                continue
            try:
                lock_encrypt_file(p, key)
                done += 1
            except Exception as e:          # noqa: BLE001
                errors.append((p, str(e)))
        return done, errors

    def _lock_decrypt_all(self, key, files):
        """잠겨 있는 관리 파일들을 모두 푼다."""
        done, errors = 0, []
        for p in files:
            locked = p + LOCK_EXT
            if not os.path.exists(locked):
                continue                    # 이미 풀림
            try:
                lock_decrypt_file(locked, key)
                done += 1
            except Exception as e:          # noqa: BLE001
                errors.append((p, str(e)))
        return done, errors

    def lock_bulk(self):
        """이름에 특정 단어가 든 파일을 찾아 비밀번호로 일괄 잠근다."""
        root = self.rn_root.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("확인", "대상 루트 폴더를 올바르게 지정하세요.")
            return
        word = self.lk_word.get().strip()
        if not word:
            messagebox.showwarning("확인", "잠글 파일 이름에 포함된 단어를 입력하세요.")
            return
        st = self._lock_state()
        already = set(st["files"])
        found = [p for p in find_files_with_word(root, word, self.rn_recursive.get())
                 if p not in already]
        if not found:
            messagebox.showinfo(
                "안내", f"'{word}' 이(가) 이름에 든 새 파일을 찾지 못했습니다.")
            return
        sample = "\n".join(os.path.basename(p) for p in found[:10])
        more = "" if len(found) <= 10 else f"\n... 외 {len(found) - 10}개"
        if not messagebox.askyesno(
                "일괄 잠금 확인",
                f"'{word}' 이(가) 든 파일 {len(found)}개를 잠글까요?\n\n{sample}{more}"):
            return
        key = self._lock_key_new_or_verify()
        if key is None:
            return
        files = st["files"] + found
        # 관리 목록 전체가 '잠김(ON)' 상태가 되도록 아직 안 잠긴 것도 모두 잠근다
        done, errors = self._lock_encrypt_all(key, files)
        self._lock_save(files=files, on=True)
        self._lock_refresh()
        if errors:
            detail = "\n".join(f"- {os.path.basename(p)}: {e}"
                               for p, e in errors[:5])
            messagebox.showwarning(
                "일부 오류", f"{done}개를 잠갔습니다. 오류 {len(errors)}개\n{detail}")
        else:
            messagebox.showinfo("완료", f"파일 {done}개를 잠갔습니다.")

    def lock_toggle(self):
        """전체 잠금 ON/OFF 를 전환한다. OFF 로 바꿀 때는 반드시 비밀번호를 확인한다."""
        st = self._lock_state()
        if not st["files"]:
            messagebox.showinfo(
                "안내", "잠금 관리 중인 파일이 없습니다. 먼저 '일괄 잠금'을 하세요.")
            return
        if st["on"]:
            # 잠금 ON -> OFF : 반드시 비밀번호 확인
            key = self._lock_key_verify(
                note="잠금을 끄면(OFF) 비밀번호 없이 파일을 열 수 있습니다.\n"
                     "끄려면 비밀번호를 입력하세요.")
            if key is None:
                return
            done, errors = self._lock_decrypt_all(key, st["files"])
            self._lock_save(on=False)
            summary = f"잠금 OFF: 파일 {done}개를 열었습니다."
        else:
            # 잠금 OFF -> ON : 이번 실행에서 확인한 키가 있으면 그대로 사용
            key = self._lock_key
            if not lock_key_matches_verifier(key, st["verifier"]):
                key = self._lock_key_verify(
                    note="잠금을 켜려면(ON) 비밀번호를 입력하세요.")
                if key is None:
                    return
            done, errors = self._lock_encrypt_all(key, st["files"])
            self._lock_save(on=True)
            summary = f"잠금 ON: 파일 {done}개를 잠갔습니다."
        self._lock_refresh()
        if errors:
            messagebox.showwarning(
                "일부 오류", f"{summary}\n오류 {len(errors)}개")
        else:
            messagebox.showinfo("완료", summary)

    def _os_open(self, path):
        """운영체제 기본 프로그램으로 파일을 연다."""
        try:
            if sys.platform == "win32":
                os.startfile(path)          # noqa: SLF001
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:              # noqa: BLE001
            messagebox.showwarning("오류", f"파일을 열 수 없습니다: {e}")

    def lock_open(self, path):
        """관리 파일을 연다.

        잠금 OFF: 비밀번호 없이 바로 연다.
        잠금 ON : 비밀번호를 확인한 뒤, 임시로 복호화한 사본을 열어 실행한다
                  (원본 잠금은 그대로 유지).
        """
        st = self._lock_state()
        locked = path + LOCK_EXT
        if st["on"]:
            key = self._lock_key_verify(
                note="이 파일을 열려면 비밀번호를 입력하세요.")
            if key is None:
                return
            if not os.path.exists(locked):
                # 이미 풀려 있으면 그냥 연다(상태 불일치 방어)
                if os.path.exists(path):
                    self._os_open(path)
                else:
                    messagebox.showwarning("오류", "파일을 찾을 수 없습니다.")
                return
            try:
                tmp = lock_decrypt_to_temp(locked, key)
            except Exception as e:          # noqa: BLE001
                messagebox.showwarning("오류", f"파일을 여는 중 오류: {e}")
                return
            self._os_open(tmp)
        else:
            # 잠금 OFF: 비밀번호 없이 열기
            if os.path.exists(path):
                self._os_open(path)
            elif os.path.exists(locked):
                messagebox.showinfo(
                    "안내", "이 파일은 아직 잠겨 있습니다. 잠금을 켠 뒤 비밀번호로 여세요.")
            else:
                messagebox.showwarning("오류", "파일을 찾을 수 없습니다.")

    def lock_unmanage(self, path):
        """파일을 잠금 관리에서 뺀다. 잠겨 있으면 풀고 목록에서 제거한다."""
        st = self._lock_state()
        locked = path + LOCK_EXT
        if os.path.exists(locked):
            # 잠긴 상태라면 풀어서 원래 파일로 돌려놓아야 하므로 비밀번호 확인
            key = self._lock_key_verify(
                note="관리에서 빼려면 파일을 먼저 풀어야 합니다. 비밀번호를 입력하세요.")
            if key is None:
                return
            try:
                lock_decrypt_file(locked, key)
            except Exception as e:          # noqa: BLE001
                messagebox.showwarning("오류", f"파일을 푸는 중 오류: {e}")
                return
        files = [p for p in st["files"] if p != path]
        # 남은 관리 파일이 없으면 잠금 설정(비번 포함)을 깨끗이 지운다
        if files:
            self._lock_save(files=files)
        else:
            self._lock_save(files=[], on=False, salt="", verifier="")
            self._lock_key = None
        self._lock_refresh()

    def _lock_refresh(self):
        """잠금 상태 라벨과 파일 목록을 현재 설정에 맞게 다시 그린다."""
        st = self._lock_state()
        on = st["on"]
        # 상태 라벨 / 토글 버튼
        if hasattr(self, "_lk_status_lbl"):
            self._lk_status_lbl.configure(
                text=("전체 잠금 상태:  ON (잠김)" if on
                      else "전체 잠금 상태:  OFF (열림)"),
                text_color=(TEAL if on else TEAL_SOFT))
        if hasattr(self, "lk_toggle_btn"):
            self.lk_toggle_btn.configure(
                text=("잠금 OFF로" if on else "잠금 ON으로"))
        # 파일 목록
        if not self._lk_rows:
            return
        for w in self._lk_rows.winfo_children():
            w.destroy()
        if not st["files"]:
            self._label(self._lk_rows, "잠금 관리 중인 파일이 없습니다.",
                        font=self.font_small, fg=MUTED).pack(
                anchor="w", padx=6, pady=8)
            return
        for path in st["files"]:
            locked = os.path.exists(path + LOCK_EXT)
            row = ctk.CTkFrame(self._lk_rows, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            mark = "🔒" if locked else "🔓"
            self._label(row, f"{mark}  {os.path.basename(path)}",
                        font=self.font_n, fg=TEXT).pack(side="left")
            self._button(row, "관리 해제", lambda p=path: self.lock_unmanage(p),
                         "이 파일을 잠금 관리에서 뺍니다(잠겨 있으면 풀어서 되돌립니다)",
                         width=84).pack(side="right", padx=(6, 4))
            self._button(row, "열기", lambda p=path: self.lock_open(p),
                         "파일을 엽니다(잠금 ON 이면 비밀번호가 필요합니다)",
                         width=64).pack(side="right")

    # ---------------- 폴더 선택 / 쌍 관리 ----------------
    def browse_src(self):
        p = filedialog.askdirectory(
            title="원본 폴더 선택",
            initialdir=self.src_input.get() or os.path.expanduser("~"))
        if p:
            self.src_input.set(p)

    def browse_dst(self):
        p = filedialog.askdirectory(
            title="대상 폴더 선택",
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
            "exclude_dirs": self.exclude_dirs_var.get().strip(),
            "exclude_files": self.exclude_files_var.get().strip(),
            "include_dirs": self.include_dirs_var.get().strip(),
            "include_files": self.include_files_var.get().strip(),
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
        conflict_mode = self.conflict_mode.get()
        exclude_dirs = parse_exclude_words(self.exclude_dirs_var.get())
        exclude_files = parse_exclude_words(self.exclude_files_var.get())
        include_dirs = parse_exclude_words(self.include_dirs_var.get())
        include_files = parse_exclude_words(self.include_files_var.get())
        self.progress_var.set("")
        self.set_status("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(
            target=self._preview_worker,
            args=(pairs, conflict_mode, exclude_dirs, exclude_files,
                  include_dirs, include_files),
            daemon=True).start()

    def _preview_worker(self, pairs, conflict_mode, exclude_dirs, exclude_files,
                        include_dirs, include_files):
        try:
            if exclude_dirs:
                self.log_msg(f"[제외] 폴더 이름 포함: {', '.join(exclude_dirs)}")
            if exclude_files:
                self.log_msg(f"[제외] 파일 이름 포함: {', '.join(exclude_files)}")
            if include_dirs:
                self.log_msg(f"[예외] 폴더 허용: {', '.join(include_dirs)}")
            if include_files:
                self.log_msg(f"[예외] 파일 허용: {', '.join(include_files)}")
            tot_copy = tot_update = tot_skip = tot_delete = 0
            for src, dst in pairs:
                plan = SyncPlan(src, dst, conflict_mode, exclude_dirs, exclude_files,
                                include_dirs, include_files)
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
        conflict_mode = self.conflict_mode.get()
        exclude_dirs = parse_exclude_words(self.exclude_dirs_var.get())
        exclude_files = parse_exclude_words(self.exclude_files_var.get())
        include_dirs = parse_exclude_words(self.include_dirs_var.get())
        include_files = parse_exclude_words(self.include_files_var.get())
        threading.Thread(
            target=self._sync_worker,
            args=(pairs, delete_enabled, confirm_delete, conflict_mode,
                  exclude_dirs, exclude_files, include_dirs, include_files),
            daemon=True).start()

    def _sync_worker(self, pairs, delete_enabled, confirm_delete, conflict_mode,
                     exclude_dirs, exclude_files, include_dirs, include_files):
        try:
            if exclude_dirs:
                self.log_msg(f"[제외] 폴더 이름 포함: {', '.join(exclude_dirs)}")
            if exclude_files:
                self.log_msg(f"[제외] 파일 이름 포함: {', '.join(exclude_files)}")
            if include_dirs:
                self.log_msg(f"[예외] 폴더 허용: {', '.join(include_dirs)}")
            if include_files:
                self.log_msg(f"[예외] 파일 허용: {', '.join(include_files)}")
            plans = []
            for src, dst in pairs:
                os.makedirs(dst, exist_ok=True)
                plan = SyncPlan(src, dst, conflict_mode, exclude_dirs, exclude_files,
                                include_dirs, include_files)
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

    # ---------------- 원형 진행률 다이얼 ----------------
    DIAL_SIZE = 64

    def _render_dial_image(self, frac):
        """PIL 로 4배 슈퍼샘플링해 매끄러운(안티앨리어싱) 링 이미지를 만든다.

        tkinter Canvas 의 arc 는 계단현상이 심해 진행률 원이 깨져 보이므로,
        Pillow(=customtkinter 의존성)로 큰 이미지를 그린 뒤 축소해 또렷하게 만든다.
        실패하면 None 을 돌려주고 호출부가 기존 Canvas 방식으로 대체한다.
        """
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            return None
        size = self.DIAL_SIZE
        scale = 4
        S = size * scale
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        pad = 7 * scale
        ring = 6 * scale
        box = [pad, pad, S - 1 - pad, S - 1 - pad]
        dr.arc(box, 0, 360, fill=SHADOW, width=ring)          # 배경 링
        if frac > 0:
            # 12시 방향(-90°)에서 시계방향으로 진행
            dr.arc(box, -90, -90 + frac * 360, fill=TEAL, width=ring)
        img = img.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _set_dial(self, frac):
        frac = max(0.0, min(1.0, frac))
        size = self.DIAL_SIZE
        d = self.dial
        d.delete("all")
        photo = self._render_dial_image(frac)
        if photo is not None:
            self._dial_photo = photo   # GC 방지용 참조 유지
            d.create_image(size // 2, size // 2, image=photo)
        else:
            # 폴백: Pillow 가 없을 때 기존 Canvas arc 방식
            d.create_oval(7, 7, size - 7, size - 7, outline=SHADOW, width=5)
            if frac > 0:
                extent = -359.9 if frac >= 1.0 else -frac * 360
                d.create_arc(7, 7, size - 7, size - 7, start=90, extent=extent,
                             style="arc", outline=TEAL, width=5)
        d.create_text(size // 2, size // 2, text=f"{int(frac * 100)}%",
                      fill=TEAL, font=(FONT_FAMILY, 11, "bold"))

    def _set_progress_max(self, total):
        self._total = max(total, 1)
        self._start_time = time.time()
        self._last_emit = 0.0
        self._ui(lambda: (self._set_dial(0.0), self.progress_var.set("0%")))

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
        self._ui(lambda: (self._set_dial(frac), self.progress_var.set(text)))


def main():
    enable_dpi_awareness()
    # customtkinter: 밝은 모드 + 고해상도에서 또렷하게 (자체 DPI 스케일링)
    ctk.set_appearance_mode("light")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
