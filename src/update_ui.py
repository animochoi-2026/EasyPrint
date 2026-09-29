"""Tk coordinator: workers never call Tk, and installation requires consent."""
import os
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import webbrowser

from .constants import get_base_dir, get_bundled_resource_dir, is_frozen
from .version import APP_VERSION, RELEASES_URL
from .updates import download_update, list_releases, version_choices, version_tuple, discard_download
from .update_installer import launch_installer, stop_installer
from .update_cleanup import prune_backups, prune_finished_downloads
from .print_log import log_event


def persist_update_state(app):
    """Fail visibly before exit if either state file cannot be committed."""
    target = Path(get_base_dir())
    settings = dict(app._settings, window_geometry=app.geometry(), update_resume=True)
    items = [{"file_path": item.file_path, "copies": item.copies,
              "page_range": item.page_range, "status": item.status.value,
              "fail_reason": item.fail_reason} for item in app.items]
    for name, data in (("queue_state.json", items), ("settings.json", settings)):
        pending = target / (name + ".update-tmp")
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, target / name)


class UpdateUI:
    def __init__(self, app):
        self.app = app
        self.events = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self.closed = False
        self.helper = None
        self.work = None
        self.releases = []
        self.dialog = None
        self.app.update_btn.configure(command=lambda: self.check(True))
        self.poll_id = app.after(150, self.poll)
        self.auto_id = app.after(5000, lambda: self.check(False)) if is_frozen() else None

    def check(self, manual):
        if self.closed or self.busy:
            return
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        if self.app._in_progress or self.app._schedule_timer is not None:
            if manual:
                messagebox.showinfo("업데이트", "인쇄 및 예약을 정지한 뒤 업데이트를 확인해주세요.", parent=self.app)
            return
        self.busy = True
        self.app.update_btn.configure(text="버전 확인 중…", state="disabled")
        def worker():
            try:
                releases = list_releases()
                choices = version_choices(releases)
                if is_frozen() and choices.latest is not None:
                    try:
                        removed = prune_backups(get_base_dir(), choices.retained_versions, APP_VERSION)
                        prune_finished_downloads(get_base_dir())
                        if removed:
                            log_event(f"이전 프로그램 백업 자동 삭제: {', '.join(removed)} (GitHub에서 다시 다운로드 가능)")
                    except Exception as exc:
                        log_event(f"업데이트 백업 자동 정리 보류: {exc}")
                self.events.put(("release", (manual, releases)))
            except Exception as exc:
                self.events.put(("check_error", (manual, str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def idle(self):
        if self.work is not None and (self.helper is None or self.helper.poll() is not None):
            discard_download(self.work)
            self.work = None
            self.helper = None
        self.busy = False
        self.app._updating = False
        self.app.update_btn.configure(text=f"v{APP_VERSION} · 업데이트", state="normal", command=lambda: self.check(True))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "release":
                    self.idle()
                    manual, self.releases = value
                    choices = version_choices(self.releases)
                    latest = choices.latest
                    newer = latest is not None and version_tuple(latest.version) > version_tuple(APP_VERSION)
                    if self.app._in_progress or self.app._schedule_timer is not None:
                        if newer:
                            self.app.update_btn.configure(text=f"새 버전 v{latest.version}")
                    elif manual or newer:
                        self.show_versions(choices)
                elif kind == "check_error":
                    self.idle()
                    manual, reason = value
                    if manual:
                        messagebox.showwarning("업데이트 확인 실패", f"{reason}\n\n기존 인쇄 기능은 계속 사용할 수 있습니다.", parent=self.app)
                elif kind == "progress":
                    done, total = value
                    self.app.update_btn.configure(text=f"{done * 100 // total}% · 취소")
                elif kind == "downloaded":
                    self.work, release = value
                    self.prepare_restart(release)
                elif kind == "download_error":
                    self.idle()
                    messagebox.showerror("업데이트 실패", value, parent=self.app)
        except queue.Empty:
            pass
        self.poll_id = self.app.after(150, self.poll)

    def show_versions(self, choices):
        dialog = self.dialog = tk.Toplevel(self.app)
        dialog.title('EasyPrint 버전 선택')
        dialog.configure(background='white')
        dialog.resizable(False, False)
        dialog.transient(self.app)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=22)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='사용할 버전을 선택하세요', font=('', 13, 'bold')).pack(anchor='w', pady=(0, 14))
        latest, previous = choices.latest, choices.previous
        self.version_selection = tk.StringVar(dialog, value='current')
        self.version_options = {}
        rows = [('latest', f'최신 버전  ·  v{latest.version}' if latest else '최신 버전  ·  없음', latest),
                ('current', f'현재 버전  ·  v{APP_VERSION} 유지', None),
                ('previous', f'이전 버전 (롤백)  ·  v{previous.version}' if previous else '이전 버전 (롤백)  ·  없음', previous)]
        for key, label, release in rows:
            button = ttk.Radiobutton(body, text=label, variable=self.version_selection, value=key)
            button.pack(anchor='w', pady=7)
            if key != 'current' and release is None:
                button.configure(state='disabled')
            self.version_options[key] = release
        ttk.Label(body, text='이전 버전은 현재 버전 바로 앞의 정식 배포본입니다.\n'
                  '이 세 버전 외의 검증된 PC 백업은 자동 삭제합니다.\n'
                  '문서·설정·인쇄 목록 및 GitHub 릴리즈는 삭제하지 않습니다.',
                  wraplength=470, justify='left').pack(anchor='w', pady=(15, 18))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='취소', command=self.dismiss_versions).pack(side='right')
        ttk.Button(buttons, text='선택 적용', command=self.apply_selection).pack(side='right', padx=8)
        dialog.protocol('WM_DELETE_WINDOW', self.dismiss_versions)

    def dismiss_versions(self):
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None

    def apply_selection(self):
        selected = self.version_selection.get()
        release = self.version_options.get(selected)
        self.dismiss_versions()
        if selected == 'current' or release is None or version_tuple(release.version) == version_tuple(APP_VERSION):
            return  # Current means keep, never reinstall or restart.
        self.offer(release)

    def offer(self, release):
        if self.closed or self.busy or self.app._in_progress or self.app._schedule_timer is not None:
            return
        rollback = version_tuple(release.version) < version_tuple(APP_VERSION)
        if not is_frozen():
            if messagebox.askyesno("새 버전", "개발 모드에서는 자동 설치하지 않습니다. 배포페이지를 열까요?", parent=self.app):
                webbrowser.open(release.page_url)
            return
        warning = ('이전 버전으로 롤백합니다. 그 버전의 기능과 제한으로 돌아갑니다.\n'
                   '1.0.1 이하로 돌아가면 이 버전 선택 화면도 없어집니다.\n'
                   '다시 최신 버전으로 올릴 때는 기존 업데이트 버튼을 사용하세요.\n\n') if rollback else ''
        if not messagebox.askyesno("EasyPrint 롤백" if rollback else "EasyPrint 업데이트", (
            f"v{APP_VERSION} → v{release.version}\n\n{release.notes[:1000]}\n\n"
            + warning + "선택한 버전을 내려받아 검증한 뒤 프로그램을 다시 시작할까요?\n"
            "인쇄 목록과 설정은 보존합니다. 선택 이후의 최신·현재·이전 버전 외 백업은 정리합니다."
        ), parent=self.app):
            return
        if self.app._in_progress or self.app._schedule_timer is not None:
            return
        self.busy = True
        self.app._updating = True
        self.app.update_btn.configure(text="다운로드 · 취소", state="normal", command=self.cancel.set)
        self.cancel.clear()
        def worker():
            try:
                work = download_update(release, lambda n, size: self.events.put(("progress", (n, size))), self.cancel)
                if self.closed or self.cancel.is_set():
                    discard_download(work)
                    self.events.put(("download_error", "업데이트가 취소되었습니다."))
                else:
                    self.events.put(("downloaded", (work, release)))
            except Exception as exc:
                self.events.put(("download_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def prepare_restart(self, release):
        if self.cancel.is_set():
            self.idle()
            return
        try:
            if self.app._in_progress or self.app._schedule_timer is not None:
                raise RuntimeError("인쇄 또는 예약이 진행 중이어서 업데이트를 중지했습니다.")
            if not self.app.list_view.commit_pending_options({item.id for item in self.app.items}):
                self.idle()
                return
            self.helper = launch_installer(self.work, get_base_dir(),
                os.path.join(get_bundled_resource_dir(), "EasyPrintUpdater.exe"), release.version,
                rollback=version_tuple(release.version) < version_tuple(APP_VERSION), expected_current=APP_VERSION,
                retained_versions=version_choices(self.releases, release.version).retained_versions)
            self.ready_deadline = time.monotonic() + 30
            self.app.update_btn.configure(text="재시작 준비 중…", state="disabled")
            self.app.after(100, self.wait_ready)
        except Exception as exc:
            self.idle()
            messagebox.showerror("설치 준비 실패", f"{exc}\n\n기존 프로그램은 변경하지 않았습니다.\n{RELEASES_URL}", parent=self.app)

    def wait_ready(self):
        if self.closed:
            return
        if (self.work / "ready").is_file():
            # Preserve the entire list without classifying this as a crash.
            try:
                persist_update_state(self.app)
            except Exception as exc:
                stop_installer(self.helper, self.work)
                self.idle()
                messagebox.showerror("목록 저장 실패", f"업데이트를 중지했습니다.\n{exc}", parent=self.app)
                return
            log_event("업데이트 설치를 위해 종료 (목록 보존)")
            self.app._closing = True
            self.app.after_cancel(self.app._metadata_poll_id)
            self.app._metadata_jobs.put(None)
            self.close()
            self.app.destroy()
        elif self.helper.poll() is not None or time.monotonic() > self.ready_deadline:
            # Terminate only the helper we just launched, before replacing files.
            if self.helper.poll() is None:
                stop_installer(self.helper, self.work)
            self.idle()
            messagebox.showerror("업데이트 실패", "업데이트 도우미를 시작하지 못했습니다. 기존 프로그램은 그대로 사용할 수 있습니다.", parent=self.app)
        else:
            self.app.after(100, self.wait_ready)

    def close(self):
        self.closed = True
        self.cancel.set()
        self.dismiss_versions()
        self.app.after_cancel(self.poll_id)
        if self.auto_id is not None:
            self.app.after_cancel(self.auto_id)
