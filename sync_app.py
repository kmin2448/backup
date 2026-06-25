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
import shutil
import threading
import queue
import json
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 설정 파일 (등록한 폴더 쌍과 옵션을 기억)
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
        root.geometry("820x620")
        root.minsize(720, 540)

        cfg = load_config()

        # 등록된 폴더 쌍 목록: [(src, dst), ...]
        self.pairs = [tuple(p) for p in cfg.get("pairs", []) if len(p) == 2]
        # 과거 단일 쌍 설정과의 호환
        if not self.pairs and cfg.get("src") and cfg.get("dst"):
            self.pairs = [(cfg["src"], cfg["dst"])]

        self.delete_var = tk.BooleanVar(value=cfg.get("delete_enabled", True))
        self.confirm_delete_var = tk.BooleanVar(value=cfg.get("confirm_delete", True))

        # 백그라운드 작업과 통신용 큐
        self.log_queue = queue.Queue()

        self._build_ui()
        self._refresh_tree()
        self.root.after(100, self._drain_log_queue)

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # 폴더 쌍 목록 (Treeview)
        ttk.Label(self.root, text="동기화할 폴더 쌍 목록 (원본 → 대상)").pack(
            anchor="w", padx=8, pady=(8, 0))

        treefrm = ttk.Frame(self.root)
        treefrm.pack(fill="both", expand=False, padx=8, pady=4)

        self.tree = ttk.Treeview(
            treefrm, columns=("src", "dst"), show="headings", height=7)
        self.tree.heading("src", text="원본 폴더 (SSD)")
        self.tree.heading("dst", text="대상 폴더 (D드라이브)")
        self.tree.column("src", width=370, anchor="w")
        self.tree.column("dst", width=370, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        tsb = ttk.Scrollbar(treefrm, command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)

        # 목록 조작 버튼
        listbtns = ttk.Frame(self.root)
        listbtns.pack(fill="x", **pad)
        ttk.Button(listbtns, text="폴더 쌍 추가",
                   command=self.add_pair).pack(side="left", padx=4)
        ttk.Button(listbtns, text="선택한 쌍 수정",
                   command=self.edit_pair).pack(side="left", padx=4)
        ttk.Button(listbtns, text="선택한 쌍 제거",
                   command=self.remove_pair).pack(side="left", padx=4)
        ttk.Button(listbtns, text="전체 비우기",
                   command=self.clear_pairs).pack(side="left", padx=4)

        # 옵션
        opt = ttk.Frame(self.root)
        opt.pack(fill="x", **pad)
        ttk.Checkbutton(
            opt, text="원본에서 삭제된 파일을 대상에서도 삭제",
            variable=self.delete_var).pack(side="left", padx=4)
        ttk.Checkbutton(
            opt, text="삭제 전 확인",
            variable=self.confirm_delete_var).pack(side="left", padx=4)

        # 실행 버튼
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.preview_btn = ttk.Button(
            btns, text="미리보기 (변경사항 확인)", command=self.preview)
        self.preview_btn.pack(side="left", padx=4)
        self.sync_btn = ttk.Button(
            btns, text="전체 동기화 시작", command=self.start_sync)
        self.sync_btn.pack(side="left", padx=4)

        # 진행 표시줄
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=4)

        # 로그 영역
        logfrm = ttk.Frame(self.root)
        logfrm.pack(fill="both", expand=True, padx=8, pady=4)
        self.log = tk.Text(logfrm, wrap="none", height=14, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logfrm, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # 상태 표시줄
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    # ---------------- 폴더 쌍 관리 ----------------
    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for src, dst in self.pairs:
            self.tree.insert("", "end", values=(src, dst))

    def _ask_pair(self, init_src="", init_dst=""):
        """원본/대상 폴더를 차례로 묻는다. 취소하면 None 반환."""
        src = filedialog.askdirectory(
            title="원본 폴더(SSD) 선택",
            initialdir=init_src or os.path.expanduser("~"))
        if not src:
            return None
        dst = filedialog.askdirectory(
            title="대상 폴더(D드라이브) 선택",
            initialdir=init_dst or os.path.expanduser("~"))
        if not dst:
            return None
        err = validate_pair(src, dst)
        if err:
            messagebox.showerror("잘못된 폴더 쌍", err)
            return None
        return (os.path.abspath(src), os.path.abspath(dst))

    def add_pair(self):
        pair = self._ask_pair()
        if not pair:
            return
        if pair in self.pairs:
            messagebox.showinfo("안내", "이미 등록된 폴더 쌍입니다.")
            return
        self.pairs.append(pair)
        self._refresh_tree()
        self._persist()

    def edit_pair(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "수정할 폴더 쌍을 목록에서 선택하세요.")
            return
        idx = self.tree.index(sel[0])
        old_src, old_dst = self.pairs[idx]
        pair = self._ask_pair(init_src=old_src, init_dst=old_dst)
        if not pair:
            return
        self.pairs[idx] = pair
        self._refresh_tree()
        self._persist()

    def remove_pair(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "제거할 폴더 쌍을 목록에서 선택하세요.")
            return
        # 여러 개 선택 가능 — 인덱스 큰 것부터 제거
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
        save_config({
            "pairs": [list(p) for p in self.pairs],
            "delete_enabled": self.delete_var.get(),
            "confirm_delete": self.confirm_delete_var.get(),
        })

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
    def _valid_pairs(self):
        if not self.pairs:
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
    def start_sync(self):
        self.clear_log()
        self._persist()
        pairs = self._valid_pairs()
        if pairs is None:
            return
        if not pairs:
            messagebox.showwarning("경고", "동기화할 유효한 폴더 쌍이 없습니다.")
            return
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
