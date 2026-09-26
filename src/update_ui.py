"""Tk coordinator: workers never call Tk, and installation requires consent."""
import os
import json
from pathlib import Path
import queue
import threading
import time
from tkinter import messagebox
import webbrowser

from .constants import get_base_dir, get_bundled_resource_dir, is_frozen
from .version import APP_VERSION, RELEASES_URL
from .updates import check_for_update, download_update
from .update_installer import launch_installer, stop_installer
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
        self.app.update_btn.configure(command=lambda: self.check(True))
        self.poll_id = app.after(150, self.poll)
        self.auto_id = app.after(5000, lambda: self.check(False)) if is_frozen() else None

    def check(self, manual):
        if self.closed or self.busy:
            return
        if self.app._in_progress or self.app._schedule_timer is not None:
            if manual:
                messagebox.showinfo("업데이트", "인쇄 및 예약을 정지한 뒤 업데이트를 확인해주세요.", parent=self.app)
            return
        self.busy = True
        self.app.update_btn.configure(text="버전 확인 중…", state="disabled")
        def worker():
            try:
                self.events.put(("release", (manual, check_for_update())))
            except Exception as exc:
                self.events.put(("check_error", (manual, str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def idle(self):
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
                    manual, release = value
                    if release is None:
                        if manual:
                            messagebox.showinfo("업데이트", f"최신 버전입니다. (v{APP_VERSION})", parent=self.app)
                    elif self.app._in_progress or self.app._schedule_timer is not None:
                        self.app.update_btn.configure(text=f"새 버전 v{release.version}")
                    else:
                        self.offer(release)
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

    def offer(self, release):
        if not is_frozen():
            if messagebox.askyesno("새 버전", "개발 모드에서는 자동 설치하지 않습니다. 배포페이지를 열까요?", parent=self.app):
                webbrowser.open(release.page_url)
            return
        if not messagebox.askyesno("EasyPrint 업데이트", (
            f"v{APP_VERSION} → v{release.version}\n\n{release.notes[:1000]}\n\n"
            "새 버전을 내려받아 검증한 뒤 프로그램을 다시 시작할까요?\n"
            "인쇄 목록과 설정은 보존하고, 기존 프로그램 파일은 백업합니다."
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
                self.events.put(("downloaded", (work, release)))
            except Exception as exc:
                self.events.put(("download_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def prepare_restart(self, release):
        if self.cancel.is_set():
            self.idle()
            return
        try:
            if self.app._in_progress:
                raise RuntimeError("인쇄가 진행 중이어서 업데이트를 중지했습니다.")
            if not self.app.list_view.commit_pending_options({item.id for item in self.app.items}):
                self.idle()
                return
            self.helper = launch_installer(self.work, get_base_dir(),
                os.path.join(get_bundled_resource_dir(), "EasyPrintUpdater.exe"), release.version)
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
        self.app.after_cancel(self.poll_id)
        if self.auto_id is not None:
            self.app.after_cancel(self.auto_id)
