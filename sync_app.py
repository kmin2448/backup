#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
폴더 동기화 프로그램 (SSD -> D드라이브)

기능:
  - 원본(소스) 폴더의 파일을 대상 폴더로 버튼 하나로 동기화
  - 동일한 파일(크기 + 수정시간 일치)은 건너뜀
  - 변경되었거나 새로 추가된 파일은 복사/덮어쓰기
  - 원본에서 삭제된 파일은 대상에서도 삭제 (삭제 전 사용자에게 확인)

표준 라이브러리(tkinter)만 사용하므로 별도 설치 없이 동작합니다.
"""

import os
import shutil
import threading
import queue
import json
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 설정 파일 (마지막에 선택한 폴더 경로를 기억)
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".folder_sync_config.json")

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
        dst_files = list_relative_files(self.dst)

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

        # 대상에만 존재하는 빈 폴더(원본에 없는 폴더) 정리 대상
        src_dirs = list_relative_dirs(self.src)
        dst_dirs = list_relative_dirs(self.dst)
        # 긴 경로(깊은 폴더)부터 지워야 하므로 역순 정렬
        self.dirs_to_delete = sorted(dst_dirs - src_dirs, reverse=True)


class App:
    def __init__(self, root):
        self.root = root
        root.title("폴더 동기화 (SSD → D드라이브)")
        root.geometry("760x560")
        root.minsize(680, 480)

        cfg = load_config()

        self.src_var = tk.StringVar(value=cfg.get("src", ""))
        self.dst_var = tk.StringVar(value=cfg.get("dst", ""))
        self.delete_var = tk.BooleanVar(value=cfg.get("delete_enabled", True))
        self.confirm_delete_var = tk.BooleanVar(value=cfg.get("confirm_delete", True))

        # 백그라운드 작업과 통신용 큐
        self.log_queue = queue.Queue()
        self.worker = None

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        frm = ttk.Frame(self.root)
        frm.pack(fill="x", **pad)

        # 소스 폴더
        ttk.Label(frm, text="원본 폴더 (SSD):").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.src_var, width=70).grid(
            row=0, column=1, sticky="we", padx=4)
        ttk.Button(frm, text="찾아보기", command=self.pick_src).grid(row=0, column=2)

        # 대상 폴더
        ttk.Label(frm, text="대상 폴더 (D드라이브):").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.dst_var, width=70).grid(
            row=1, column=1, sticky="we", padx=4)
        ttk.Button(frm, text="찾아보기", command=self.pick_dst).grid(row=1, column=2)

        frm.columnconfigure(1, weight=1)

        # 옵션
        opt = ttk.Frame(self.root)
        opt.pack(fill="x", **pad)
        ttk.Checkbutton(
            opt, text="원본에서 삭제된 파일을 대상에서도 삭제",
            variable=self.delete_var).pack(side="left", padx=4)
        ttk.Checkbutton(
            opt, text="삭제 전 확인",
            variable=self.confirm_delete_var).pack(side="left", padx=4)

        # 버튼
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.preview_btn = ttk.Button(
            btns, text="미리보기 (변경사항 확인)", command=self.preview)
        self.preview_btn.pack(side="left", padx=4)
        self.sync_btn = ttk.Button(
            btns, text="동기화 시작", command=self.start_sync)
        self.sync_btn.pack(side="left", padx=4)

        # 진행 표시줄
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=4)

        # 로그 영역
        logfrm = ttk.Frame(self.root)
        logfrm.pack(fill="both", expand=True, padx=8, pady=4)
        self.log = tk.Text(logfrm, wrap="none", height=18, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logfrm, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # 상태 표시줄
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    # ---------------- 폴더 선택 ----------------
    def pick_src(self):
        path = filedialog.askdirectory(title="원본 폴더 선택")
        if path:
            self.src_var.set(path)

    def pick_dst(self):
        path = filedialog.askdirectory(title="대상 폴더 선택")
        if path:
            self.dst_var.set(path)

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

    # ---------------- 입력 검증 ----------------
    def _validate(self):
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        if not src or not dst:
            messagebox.showwarning("경고", "원본과 대상 폴더를 모두 선택하세요.")
            return None
        if not os.path.isdir(src):
            messagebox.showerror("오류", f"원본 폴더가 존재하지 않습니다:\n{src}")
            return None
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst)
        if src_abs == dst_abs:
            messagebox.showerror("오류", "원본과 대상이 같은 폴더입니다.")
            return None
        # 대상이 원본의 하위 폴더이면 무한 복사가 되므로 막는다.
        if dst_abs.startswith(src_abs + os.sep):
            messagebox.showerror("오류", "대상 폴더가 원본 폴더 안에 있을 수 없습니다.")
            return None
        return src_abs, dst_abs

    # ---------------- 미리보기 ----------------
    def preview(self):
        validated = self._validate()
        if not validated:
            return
        src, dst = validated
        if not os.path.isdir(dst):
            self.clear_log()
            self.log_msg(f"대상 폴더가 아직 없습니다(동기화 시 생성됨): {dst}")
        self.clear_log()
        self.status_var.set("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(target=self._preview_worker, args=(src, dst),
                         daemon=True).start()

    def _preview_worker(self, src, dst):
        plan = SyncPlan(src, dst)
        if os.path.isdir(dst):
            plan.build()
        else:
            # 대상이 없으면 모든 파일이 새로 복사 대상
            plan.to_copy = sorted(list_relative_files(src))
        self.log_msg("===== 미리보기 =====")
        self.log_msg(f"추가될 파일   : {len(plan.to_copy)} 개")
        self.log_msg(f"변경될 파일   : {len(plan.to_update)} 개")
        self.log_msg(f"건너뛸 파일   : {len(plan.to_skip)} 개")
        self.log_msg(f"삭제될 파일   : {len(plan.to_delete)} 개")
        self.log_msg("")
        for rel in plan.to_copy:
            self.log_msg(f"  [추가] {rel}")
        for rel in plan.to_update:
            self.log_msg(f"  [변경] {rel}")
        for rel in plan.to_delete:
            self.log_msg(f"  [삭제] {rel}")
        self.log_msg("===================")
        self.status_var.set("미리보기 완료")
        self._set_buttons(True)

    # ---------------- 동기화 ----------------
    def start_sync(self):
        validated = self._validate()
        if not validated:
            return
        src, dst = validated
        self.clear_log()
        self.status_var.set("변경사항 분석 중...")
        self._set_buttons(False)
        threading.Thread(target=self._sync_worker, args=(src, dst),
                         daemon=True).start()

    def _sync_worker(self, src, dst):
        try:
            os.makedirs(dst, exist_ok=True)

            plan = SyncPlan(src, dst)
            plan.build()

            self.log_msg(f"분석 완료 - 추가 {len(plan.to_copy)} / "
                         f"변경 {len(plan.to_update)} / "
                         f"건너뜀 {len(plan.to_skip)} / "
                         f"삭제대상 {len(plan.to_delete)}")

            # 삭제 확인
            do_delete = False
            if self.delete_var.get() and plan.to_delete:
                if self.confirm_delete_var.get():
                    do_delete = self._ask_delete(plan.to_delete)
                else:
                    do_delete = True

            total = len(plan.to_copy) + len(plan.to_update)
            if do_delete:
                total += len(plan.to_delete)
            self._set_progress_max(total)

            done = 0
            copied = updated = deleted = skipped = errors = 0

            # 추가 + 변경 복사
            for rel in plan.to_copy + plan.to_update:
                s = os.path.join(src, rel)
                d = os.path.join(dst, rel)
                tag = "추가" if rel in plan.to_copy else "변경"
                try:
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(s, d)  # 메타데이터(수정시간) 보존
                    self.log_msg(f"[{tag}] {rel}")
                    if tag == "추가":
                        copied += 1
                    else:
                        updated += 1
                except Exception as e:
                    self.log_msg(f"[오류] 복사 실패 {rel}: {e}")
                    errors += 1
                done += 1
                self._set_progress(done)

            skipped = len(plan.to_skip)

            # 삭제
            if do_delete:
                for rel in plan.to_delete:
                    d = os.path.join(dst, rel)
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
                    d = os.path.join(dst, rel)
                    try:
                        if os.path.isdir(d) and not os.listdir(d):
                            os.rmdir(d)
                            self.log_msg(f"[폴더삭제] {rel}")
                    except Exception:
                        pass
            elif plan.to_delete:
                self.log_msg(f"삭제 건너뜀 - {len(plan.to_delete)} 개 파일은 "
                             "대상에 그대로 둡니다.")

            self.log_msg("")
            self.log_msg(f"===== 완료 ({datetime.now():%Y-%m-%d %H:%M:%S}) =====")
            self.log_msg(f"추가 {copied} / 변경 {updated} / 건너뜀 {skipped} / "
                         f"삭제 {deleted} / 오류 {errors}")
            self.status_var.set(
                f"완료: 추가 {copied}, 변경 {updated}, 삭제 {deleted}, 오류 {errors}")

            # 설정 저장
            save_config({
                "src": self.src_var.get().strip(),
                "dst": self.dst_var.get().strip(),
                "delete_enabled": self.delete_var.get(),
                "confirm_delete": self.confirm_delete_var.get(),
            })
        except Exception as e:
            self.log_msg(f"[치명적 오류] {e}")
            self.status_var.set("오류로 중단됨")
        finally:
            self._set_buttons(True)

    def _ask_delete(self, to_delete):
        """삭제 확인 대화상자. 메인 스레드에서 띄우기 위해 이벤트로 동기화."""
        result = {"ok": False}
        event = threading.Event()

        def ask():
            preview = "\n".join(to_delete[:20])
            more = "" if len(to_delete) <= 20 else f"\n... 외 {len(to_delete) - 20} 개"
            msg = (f"원본에서 삭제된 파일 {len(to_delete)} 개를 "
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
