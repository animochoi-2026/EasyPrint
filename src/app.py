"""메인 애플리케이션 창.

Phase 1: 좌(스크롤 리스트) / 우(기능 패널) 레이아웃 뼈대.
Phase 2: 드래그앤드롭 파일 추가, 순서 변경(진행 중 항목만 고정), 개별 삭제
(Delete/우클릭, 진행 중 제외), 전체 비우기(진행 중엔 비활성), 상태별 개수 갱신.
Phase 3: 워커 스레드(인덱스 미저장, 매번 리스트 재조회 + Lock)로 PDF를
SumatraPDF에 순서대로 전달.
Phase 4: 오른쪽 패널의 프린터 선택이 실제 인쇄에 반영되고, 행별 매수/페이지범위가
SumatraPDF -print-settings로 전달된다. 프린터 옵션 버튼은 Windows 속성창을 그대로 호출.
Phase 5: HWP/HWPX는 한글 인쇄 관리자(HwpPrnMng.exe /p)로 인쇄한다 — win32com 자동화는
이 PC에서 보안 확인창을 못 막고 한/글이 죽는 문제가 있어 포기했다 (hwp_printing.py 참고).
프린터 드롭다운에서 고른 프린터는 Windows 기본 프린터 자체를 바꾼다 (HwpPrnMng.exe는
명령줄로 프린터를 지정할 방법이 없어 항상 그 순간의 Windows 기본 프린터로 인쇄하기 때문).
Phase 6: 예약 시작 — 목표 시각까지 남은 초를 threading.Timer로 1회만 기다린다(매분 폴링 없음).
절전모드로 늦게 깨어나도 타이머가 깨어난 즉시 그대로 시작하므로 별도 처리가 필요 없다.
Phase 9: 정지(Graceful Stop) — 현재 항목은 끝까지 두고 다음 항목부터 안 집는다.
강제 취소(Abort) — abort_event로 진행 중인 서브프로세스도 즉시 죽이고 스풀러 잡도 삭제 시도.
두 경우 다 남은 대기 항목은 ⏸로 바뀌고, "인쇄 시작"을 다시 누르면 이어서 진행된다.
Phase 10: 대기 항목이 자연스럽게 0이 되는 순간(정지/장애로 멈춘 게 아니라 진짜 다 끝났을 때만)
체크된 완료 후 동작(프로그램 종료/Windows 종료/절전)을 실행한다. print.log에 인쇄 시작/성공/
실패/정지/취소/장애 이벤트를 발생 시점마다 한 줄씩 append한다.
Phase 11: 진행 중일 때 리스트가 바뀔 때마다 queue_state.json에 스냅샷을 남기고
정상 종료 시 지운다. 다음 실행 시 파일이 남아있으면 비정상 종료로 보고 복구 여부를
물어본다. settings.json에는 창 크기만 저장(프린터는 이제 Windows 기본 프린터 자체를
바꾸는 방식이라 따로 저장할 필요가 없음).
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from .constants import APP_TITLE
from .control_panel import ControlPanel
from .hwp_printing import print_hwp
from .document_printing import WordUnavailableError, check_word_available, print_document
from .models import IMAGE_EXTENSIONS, PENDING_STATUSES, PrintItem, PrintStatus, supported_extension
from .pdf_utils import get_pdf_page_count
from .print_list_view import PrintListView
from .print_log import log_event
from .printer_status import cancel_print_jobs
from .printing import check_file_ready, print_pdf
from .queue_state import clear_queue_snapshot, load_queue_snapshot, save_queue_snapshot
from .settings import load_settings, save_settings
from .theme import apply_theme
from .version import APP_VERSION
from .update_ui import UpdateUI

try:
    import win32print
except ImportError:  # 개발 환경에 pywin32 미설치 시 UI만이라도 뜨도록
    win32print = None


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self._settings = load_settings()
        self.geometry(self._settings.get("window_geometry", "1140x780"))
        self.minsize(1040, 740)
        self.style = apply_theme(self)

        self.items: list[PrintItem] = []
        self._lock = threading.Lock()
        self._in_progress = False
        self._schedule_timer: threading.Timer | None = None
        self._stop_requested = False
        self._abort_event = threading.Event()
        self._run_ids = None
        self._run_printer = None
        self._metadata_jobs = queue.Queue()
        self._metadata_results = queue.Queue()
        self._closing = False
        self._updating = False

        self._build_layout()
        self._register_drop_targets()
        self._wire_control_panel()
        self.bind_all("<Delete>", self._on_delete_key)
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        self._load_printers()
        self._check_crash_recovery()
        self._rerender()
        threading.Thread(target=self._metadata_worker, daemon=True).start()
        for item in self.items:
            if item.extension == ".pdf":
                self._metadata_jobs.put((item.id, item.file_path))
        self._metadata_poll_id = self.after(100, self._poll_metadata)
        self._updater = UpdateUI(self)

    def _on_close_request(self) -> None:
        """정상 종료: 창 크기는 저장하고, 인쇄 리스트 스냅샷은 지운다
        (계획서 4장 — 리스트 자체는 항상 초기화, 복구용 파일만 정상 종료 시 삭제)."""
        if self._updating:
            messagebox.showinfo("업데이트 진행 중", "다운로드가 끝날 때까지 기다리거나 상단 업데이트 버튼에서 취소해주세요.")
            return
        if self._in_progress:
            messagebox.showinfo("인쇄 진행 중", "현재 파일 후 정지 또는 강제 취소한 뒤 창을 닫아주세요.")
            return
        self._closing = True
        self._updater.close()
        self.after_cancel(self._metadata_poll_id)
        self._cancel_schedule_timer()
        self._metadata_jobs.put(None)
        self._settings["window_geometry"] = self.geometry()
        save_settings(self._settings)
        clear_queue_snapshot()
        self.destroy()

    def _check_crash_recovery(self) -> None:
        """계획서 4장 "프로그램 종료/재시작": queue_state.json이 남아있으면
        비정상 종료로 보고 복구 여부를 물어본다."""
        recovered = load_queue_snapshot()
        update_resume = self._settings.pop("update_resume", False)
        if recovered:
            if update_resume or messagebox.askyesno(
                "이전 인쇄 작업 복구",
                f"이전에 비정상 종료된 인쇄 작업이 있습니다 ({len(recovered)}개 항목).\n복구하시겠습니까?",
            ):
                self.items = recovered
        clear_queue_snapshot()
        if update_resume:
            save_settings(self._settings)

    def _maybe_save_queue_snapshot(self) -> None:
        """진행 중일 때만 스냅샷을 남긴다 (계획서 4장) — 그냥 편집 중일 때마다
        디스크에 쓰면 저사양 PC에서 불필요한 병목이 될 수 있어서(계획서 7장)."""
        if self._in_progress:
            save_queue_snapshot(self.items)

    # ------------------------------------------------------------------ 레이아웃
    def _build_layout(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=0)
        container.rowconfigure(1, weight=1)
        header = ttk.Frame(container, padding=(18, 16, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        title_row = ttk.Frame(header)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="EasyPrint", style="Title.TLabel").pack(side="left")
        self.update_btn = ttk.Button(title_row, text=f"v{APP_VERSION} · 업데이트")
        self.update_btn.pack(side="right")
        ttk.Label(header, text="파일을 놓고, 프린터를 고르고, 순서대로 인쇄하세요.", style="Secondary.TLabel").pack(anchor="w", pady=(3, 10))
        toolbar = ttk.Frame(header)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="＋ 파일 추가", command=self._choose_files).pack(side="left", padx=(0, 6))
        self.reprint_one_btn = ttk.Button(toolbar, text="선택 파일 재인쇄", command=lambda: self.list_view.request_reprint(False))
        self.reprint_one_btn.pack(side="left", padx=(0, 6))
        self.reprint_from_btn = ttk.Button(toolbar, text="여기부터 재인쇄", command=lambda: self.list_view.request_reprint(True))
        self.reprint_from_btn.pack(side="left")

        self.list_view = PrintListView(
            container,
            on_copies_change=self._on_item_copies_change,
            on_range_change=self._on_item_range_change,
            on_delete_request=self._on_delete_request,
            on_reorder_commit=self._on_reorder_commit,
            on_retry_request=self._on_retry_request,
            on_reprint_request=self._on_reprint_request,
        )
        self.list_view.grid(row=1, column=0, sticky="nsew", padx=(4, 0))
        ttk.Label(container, text="PDF · HWP/HWPX · PNG/JPG/JPEG/BMP · DOCX   |   범위 예: 1-3,5 / 빈칸은 전체", style="Secondary.TLabel", padding=(18, 10)).grid(row=2, column=0, sticky="ew")

        separator = ttk.Separator(container, orient="vertical")
        separator.grid(row=0, column=1, rowspan=3, sticky="ns")

        self.control_panel = ControlPanel(container, width=300)
        self.control_panel.grid(row=0, column=2, rowspan=3, sticky="ns")
        container.columnconfigure(2, weight=0, minsize=300)

    def _wire_control_panel(self) -> None:
        self.control_panel.clear_all_btn.configure(command=self._on_clear_all_clicked)
        self.control_panel.start_btn.configure(command=self._on_start_clicked)
        self.control_panel.printer_options_btn.configure(command=self._on_printer_options_clicked)
        self.control_panel.retry_all_btn.configure(command=self._on_retry_all_clicked)
        self.control_panel.cleanup_btn.configure(command=self._on_cleanup_clicked)
        self.control_panel.schedule_entry.bind("<Return>", self._on_schedule_entry_submit)
        self.control_panel.schedule_cancel_btn.configure(command=self._on_schedule_cancel)
        self.control_panel.stop_btn.configure(command=self._on_stop_clicked)
        self.control_panel.abort_btn.configure(command=self._on_abort_clicked)
        self.control_panel.schedule_set_btn.configure(command=self._on_schedule_entry_submit)
        self.control_panel.shell_install_btn.configure(command=lambda: self._set_shell_menu(True))
        self.control_panel.shell_remove_btn.configure(command=lambda: self._set_shell_menu(False))

    def _set_shell_menu(self, enabled):
        from .shell_integration import register_menu, unregister_menu
        try:
            (register_menu if enabled else unregister_menu)()
            messagebox.showinfo("탐색기 우클릭", "등록했습니다. 파일 우클릭 → EasyPrint로 인쇄…를 선택하세요.\nWindows 11에서는 ‘더 많은 옵션 표시’에 나타날 수 있습니다.\n프로그램 폴더를 옮기면 다시 등록해주세요." if enabled else "EasyPrint 우클릭 메뉴를 해제했습니다.")
        except OSError as exc:
            messagebox.showerror("우클릭 메뉴 설정 실패", str(exc))

    def _choose_files(self):
        paths = filedialog.askopenfilenames(title="인쇄할 파일 추가", filetypes=[("지원 파일", "*.pdf *.hwp *.hwpx *.png *.jpg *.jpeg *.bmp *.docx"), ("모든 파일", "*.*")])
        self.add_files(list(paths))

    def _register_drop_targets(self) -> None:
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_files_dropped)

    # ------------------------------------------------------------------ 프린터
    def _load_printers(self) -> None:
        """콤보박스 초기값은 현재 Windows 기본 프린터로 맞춘다. 이 앱에서 프린터를
        선택하는 것 자체가 Windows 기본 프린터를 바꾸는 동작이라(_on_printer_selected),
        재시작해도 Windows가 이미 그 값을 기억하고 있어 별도 설정 파일이 필요 없다."""
        printers: list[str] = []
        current_default: str | None = None
        if win32print is not None:
            try:
                flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
                printers = [p[2] for p in win32print.EnumPrinters(flags)]
            except Exception:
                printers = []
            try:
                current_default = win32print.GetDefaultPrinter()
            except Exception:
                current_default = None
        self.control_panel.set_printers(printers, selected=current_default)
        self.control_panel.printer_combo.bind("<<ComboboxSelected>>", self._on_printer_selected)

    def _on_printer_selected(self, _event=None) -> None:
        """오른쪽 패널에서 프린터를 고르면 Windows 기본 프린터 자체를 바꾼다.
        HwpPrnMng.exe /p가 명령줄로 프린터를 지정할 방법이 없어, 인쇄 시점에
        Windows 기본 프린터가 곧 우리가 쓸 프린터가 되도록 맞춰두는 것."""
        selected = self.control_panel.printer_var.get().strip()
        if not selected or win32print is None:
            return
        try:
            win32print.SetDefaultPrinter(selected)
        except Exception as exc:
            messagebox.showwarning("프린터 변경 실패", f"Windows 기본 프린터를 바꾸지 못했습니다: {exc}")

    # ------------------------------------------------------------------ 인쇄 시작 / 워커 스레드
    def _on_start_clicked(self, item_ids=None) -> None:
        if self._in_progress or getattr(self, "_updating", False):
            return
        with self._lock:
            run_ids = {i.id for i in self.items if i.status in PENDING_STATUSES and (item_ids is None or i.id in item_ids)}
            has_pending = bool(run_ids)
        if not has_pending:
            messagebox.showinfo("인쇄 시작", "인쇄할 항목이 없습니다.")
            return
        if not self.list_view.commit_pending_options(run_ids):
            return
        self._run_ids = run_ids
        self._run_printer = self._current_printer()
        self._cancel_schedule_timer()
        self.control_panel.set_schedule_active(False)
        self._stop_requested = False
        self._abort_event.clear()
        self._in_progress = True
        self.list_view.lock_items(run_ids)
        self.control_panel.set_printing_state(True)
        self.reprint_one_btn.state(["disabled"])
        self.reprint_from_btn.state(["disabled"])
        log_event("인쇄 시작")
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _on_stop_clicked(self) -> None:
        """계획서 4장 "정지(Graceful Stop)": 현재 인쇄 중인 파일은 끝까지 두고,
        이후 새 항목을 집지 않는다."""
        if not self._in_progress:
            return
        self._stop_requested = True
        log_event("정지 요청")

    def _on_abort_clicked(self) -> None:
        """계획서 4장 "강제 취소(Abort)": 진행 중인 인쇄도 즉시 중단을 시도한다.
        이미 프린터로 넘어간 페이지는 취소가 안 될 수 있어 결과와 무관하게 항상
        "시도했다"는 사실을 안내하고, 취소 실패로 프로그램이 멈추지 않도록 보장한다."""
        if not self._in_progress:
            return
        self._stop_requested = True
        self._abort_event.set()
        cancel_print_jobs(self._run_printer)
        log_event("강제 취소 요청")
        messagebox.showinfo(
            "강제 취소",
            "인쇄 취소를 시도했습니다. 이미 프린터로 넘어간 페이지는 Windows 특성상 "
            "취소되지 않을 수 있습니다.",
        )

    # ------------------------------------------------------------------ 예약 시작
    def _on_schedule_entry_submit(self, _event=None) -> None:
        """계획서 4장: 시각을 지정하면 그 시각에 자동으로 진행 중 상태로 전환.
        이미 지난 시각이면 다음날로 예약하고 안내 팝업을 띄운다."""
        if self._in_progress or self._updating:
            return
        raw = self.control_panel.schedule_var.get().strip()
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw)
        if not match:
            messagebox.showerror("예약 시작", "24시간 형식(HH:MM)으로 입력하세요. 예: 18:30")
            return

        hour, minute = int(match.group(1)), int(match.group(2))
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
            messagebox.showinfo(
                "예약 시작", f"지정한 시각이 이미 지나서 내일 {hour:02d}:{minute:02d}로 예약됩니다."
            )

        self._cancel_schedule_timer()
        delay_sec = max(0.0, (target - datetime.now()).total_seconds())
        self._schedule_timer = threading.Timer(delay_sec, self._on_schedule_fire)
        self._schedule_timer.daemon = True
        self._schedule_timer.start()

        same_day = target.date() == now.date()
        label = target.strftime("%H:%M") if same_day else target.strftime("%m/%d %H:%M")
        self.control_panel.set_schedule_active(True, f"예약됨: {label}")

    def _on_schedule_fire(self) -> None:
        """threading.Timer는 절전모드로 PC가 늦게 깨어나도 깨어난 즉시 콜백을 실행하므로,
        "이미 지났으면 즉시 시작"이라는 요구사항이 별도 처리 없이 자연스럽게 만족된다."""
        self.after(0, self._start_scheduled_print)

    def _start_scheduled_print(self) -> None:
        self._schedule_timer = None
        self.control_panel.set_schedule_active(False)
        self._on_start_clicked()

    def _on_schedule_cancel(self) -> None:
        self._cancel_schedule_timer()
        self.control_panel.set_schedule_active(False)

    def _cancel_schedule_timer(self) -> None:
        if self._schedule_timer is not None:
            self._schedule_timer.cancel()
            self._schedule_timer = None

    def _worker_loop(self) -> None:
        """워커는 인덱스를 저장하지 않고, 다음 항목을 고를 때마다 항상 현재 리스트를
        다시 조회한다 (설계 계획서 1장). 리스트 조회+상태변경만 짧게 락으로 보호한다.
        프린터 자체 장애가 감지되면(개별 파일 문제가 아니라) 루프를 멈추고 남은 대기
        항목을 ⏸로 돌린 뒤 안내 팝업을 띄운다. 정지/강제 취소 요청도 같은 방식으로
        루프를 멈추고 남은 항목을 ⏸로 돌린다. exhausted=True(정지/장애 없이 자연스럽게
        더 처리할 항목이 없어진 경우)일 때만 "완료 후 자동 처리"를 검토한다."""
        fault_printer: str | None = None
        word_error: str | None = None
        exhausted = False
        with self._lock:
            needs_word = any(i.id in self._run_ids and i.status in PENDING_STATUSES and i.extension == ".docx" for i in self.items)
        if needs_word and not self._stop_requested:
            try:
                check_word_available(self._abort_event)
            except Exception as exc:
                if not self._abort_event.is_set():
                    word_error = str(exc)
                self._stop_requested = True
        while True:
            if self._stop_requested:
                self._pause_remaining_items()
                self.after(0, self._rerender)
                break
            with self._lock:
                item = next((i for i in self.items if i.status in PENDING_STATUSES and i.id in self._run_ids), None)
                if item is None:
                    exhausted = True
                    break
                item.status = PrintStatus.PRINTING
                item.fail_reason = None
            self.after(0, self._refresh_item_and_counts, item.id)
            try:
                fault_printer = self._print_one(item)
            except WordUnavailableError as exc:
                # Word can disappear/fail after the initial batch preflight.
                word_error = str(exc)
                with self._lock:
                    item.status = PrintStatus.PAUSED
                    item.fail_reason = None
                self._stop_requested = True
            if fault_printer:
                self._pause_remaining_items()
                self.after(0, self._rerender)
                break
            if self._stop_requested:
                self._pause_remaining_items()
                self.after(0, self._rerender)
                break
        self._stop_requested = False
        self._abort_event.clear()
        self.after(0, self._on_worker_finished)
        if word_error:
            log_event(f"Word 실행 불가로 인쇄 중지: {word_error}")
            self.after(0, self._show_word_unavailable_dialog, word_error)
        elif fault_printer:
            log_event(f"프린터 장애 감지: {fault_printer}")
            self.after(0, self._show_printer_fault_dialog, fault_printer)
        elif exhausted:
            self.after(0, self._maybe_run_auto_finish_action)

    def _print_one(self, item: PrintItem) -> str | None:
        """반환값: 프린터 장애가 감지되면 그 프린터 이름, 아니면 None."""
        ok, reason = check_file_ready(item.file_path)
        is_fault = False
        printer: str | None = None
        if ok:
            printer = self._run_printer
            ext = supported_extension(item.file_path)
            if ext == ".pdf":
                ok, reason, is_fault = print_pdf(
                    item.file_path, printer, item.copies, item.page_range,
                    abort_event=self._abort_event,
                    on_stage=lambda text, item=item: self._set_item_stage_note(item, text),
                )
            elif ext in (".hwp", ".hwpx"):
                ok, reason, is_fault = print_hwp(
                    item.file_path, printer, item.copies, item.page_range, abort_event=self._abort_event
                )
            else:
                ok, reason, is_fault = print_document(
                    item.file_path, printer, item.copies, item.page_range,
                    abort_event=self._abort_event,
                    on_stage=lambda text, item=item: self._set_item_stage_note(item, text),
                )

        aborted = self._abort_event.is_set()
        with self._lock:
            if is_fault:
                item.status = PrintStatus.WAITING  # 이 파일 문제가 아니므로 재시도 대상으로 유지
                item.fail_reason = None
            elif aborted:
                # 취소를 시도했지만 실제로 출력됐는지 불확실하므로 성공/실패로 단정하지 않고
                # 정지된 것으로 취급해 재시도 대상으로 남긴다 (계획서 4장 "강제 취소").
                item.status = PrintStatus.PAUSED
                item.fail_reason = None
            else:
                item.status = PrintStatus.DONE if ok else PrintStatus.FAILED
                item.fail_reason = None if ok else reason

        if is_fault:
            pass  # 큐 전체 장애 로그는 _worker_loop에서 한 번만 남김
        elif aborted:
            log_event(f"취소됨 - {item.file_name}")
        elif ok:
            log_event(f"성공 - {item.file_name}")
        else:
            log_event(f"실패 - {item.file_name} (사유: {reason})")

        self.after(0, self._refresh_item_and_counts, item.id)
        return printer if is_fault else None

    def _pause_remaining_items(self) -> None:
        """계획서 4장: 프린터 장애 감지 시 진행을 자동 정지 — 남은 대기 항목을 ⏸로 전환."""
        with self._lock:
            for i in self.items:
                if i.status == PrintStatus.WAITING and i.id in self._run_ids:
                    i.status = PrintStatus.PAUSED

    def _show_printer_fault_dialog(self, printer: str) -> None:
        messagebox.showwarning(
            "프린터 오류 감지",
            f"'{printer}' 프린터에서 오류가 감지되어 인쇄를 멈췄습니다 (용지 걸림/오프라인 등).\n\n"
            "프린터를 확인한 뒤, 오른쪽에서 같은 프린터를 그대로 쓰거나 다른 프린터로 바꾼 다음 "
            "\"인쇄 시작\"을 다시 눌러 이어서 진행하세요.",
        )

    def _show_word_unavailable_dialog(self, reason: str) -> None:
        messagebox.showwarning("Microsoft Word 확인 필요 — 인쇄 중지", reason, parent=self)

    def _maybe_run_auto_finish_action(self) -> None:
        """계획서 4장 "완료 후 자동 처리": 더 이상 처리할 대기 항목이 없어지는 순간
        체크된 동작을 실행한다. 실패 항목이 남아있어도(재시도하지 않는 이상) 트리거된다."""
        action = self.control_panel.on_finish_var.get()
        if any(i.status in PENDING_STATUSES for i in self.items):
            return
        if not action:
            return
        if action == "exit_app":
            log_event("완료 후 자동 처리: 프로그램 종료")
            self._on_close_request()
        elif action == "shutdown":
            log_event("완료 후 자동 처리: Windows 종료")
            os.system("shutdown /s /t 0")
        elif action == "sleep":
            log_event("완료 후 자동 처리: 절전 모드")
            try:
                ctypes.windll.powrprof.SetSuspendState(False, True, False)
            except Exception:
                pass

    def _current_printer(self) -> str | None:
        """Phase 4: 오른쪽 패널에서 선택한 프린터를 실제로 사용. 선택이 없으면
        시스템 기본 프린터로 대체."""
        selected = self.control_panel.printer_var.get().strip()
        if selected:
            return selected
        if win32print is None:
            return None
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None

    def _on_printer_options_clicked(self) -> None:
        """자체 UI를 만들지 않고 Windows 프린터 속성창을 그대로 호출한다 (계획서 5장)."""
        printer = self.control_panel.printer_var.get().strip()
        if not printer:
            messagebox.showinfo("프린터 옵션", "먼저 프린터를 선택하세요.")
            return
        try:
            subprocess.Popen(["rundll32", "printui.dll,PrintUIEntry", "/p", "/n", printer])
        except OSError as exc:
            messagebox.showerror("프린터 옵션", f"프린터 속성창을 열 수 없습니다: {exc}")

    def _on_worker_finished(self) -> None:
        self._in_progress = False
        self._run_ids = None
        self._run_printer = None
        self.reprint_one_btn.state(["!disabled"])
        self.reprint_from_btn.state(["!disabled"])
        self.control_panel.set_printing_state(False)
        self.list_view.lock_items(set())
        self._refresh_status_counts()
        self._refresh_clear_all_state()

    def _refresh_item_and_counts(self, item_id: str) -> None:
        self.list_view.refresh_item(item_id)
        self._refresh_status_counts()
        self._refresh_clear_all_state()
        self._maybe_save_queue_snapshot()

    def _set_item_stage_note(self, item: PrintItem, text: str | None) -> None:
        """워커 스레드에서 호출됨(평탄화 등 시간이 걸리는 전처리 구간 안내).
        화면에 아무 표시가 없으면 사용자가 "여전히 느리게 인쇄 중"으로 오해해
        도중에 취소해버리는 문제가 있어서 추가함."""
        item.stage_note = text
        self.after(0, self._refresh_item_and_counts, item.id)

    # ------------------------------------------------------------------ 파일 추가 (드래그앤드롭)
    def _on_files_dropped(self, event: tk.Event) -> None:
        paths = list(self.tk.splitlist(event.data))
        self.add_files(paths)
        return "copy"

    def add_files(self, paths: list[str]) -> None:
        """진행 상태와 무관하게 항상 리스트 맨 아래에 추가. 중복 파일도 허용."""
        added = 0
        skipped: list[str] = []
        for path in paths:
            path = os.path.normpath(path)
            if supported_extension(path) is None or not os.path.isfile(path):
                skipped.append(path)
                continue
            item = PrintItem(file_path=path)
            if item.extension in IMAGE_EXTENSIONS:
                item.page_count = 1
            self.items.append(item)
            if item.extension == ".pdf":
                self._metadata_jobs.put((item.id, path))
            added += 1

        if added:
            self._rerender()
        if skipped:
            names = "\n".join(f"- {p}" for p in skipped[:10])
            more = f"\n...외 {len(skipped) - 10}개" if len(skipped) > 10 else ""
            messagebox.showwarning(
                "지원하지 않는 파일",
                f"PDF, HWP/HWPX, PNG/JPG/JPEG/BMP, DOCX 파일을 지원합니다.\n파일이 존재하는지도 확인해주세요.\n\n{names}{more}",
            )

    def _metadata_worker(self):
        while True:
            job = self._metadata_jobs.get()
            if job is None:
                return
            item_id, path = job
            self._metadata_results.put((item_id, get_pdf_page_count(path)))

    def _poll_metadata(self):
        if self._closing:
            return
        while True:
            try:
                item_id, count = self._metadata_results.get_nowait()
            except queue.Empty:
                break
            item = next((i for i in self.items if i.id == item_id), None)
            if item is not None:
                item.page_count = count
                item.page_count_checked = True
                self.list_view.refresh_item(item_id)
        self._metadata_poll_id = self.after(100, self._poll_metadata)

    def _on_reprint_request(self, item_id, from_here=False):
        if self._in_progress:
            messagebox.showinfo("재인쇄", "현재 작업을 정지한 뒤 프린터를 선택하고 재인쇄해주세요.")
            return
        index = next((n for n, i in enumerate(self.items) if i.id == item_id), None)
        if index is None:
            return
        targets = self.items[index:] if from_here else [self.items[index]]
        ids = {i.id for i in targets}
        if not self.list_view.commit_pending_options(ids):
            return
        printer = self._current_printer() or "Windows 기본 프린터"
        if not messagebox.askyesno("재인쇄 확인", f"프린터: {printer}\n시작 파일: {targets[0].file_name}\n{len(targets)}개 파일을 재인쇄할까요?\n\n프린터를 바꾸려면 취소 후 오른쪽에서 선택해주세요."):
            return
        for item in targets:
            item.status = PrintStatus.WAITING
            item.fail_reason = None
            item.stage_note = None
        self._rerender()
        self._on_start_clicked(ids)

    # ------------------------------------------------------------------ 항목별 옵션 변경
    def _on_item_copies_change(self, item: PrintItem, value: int) -> None:
        item.copies = max(1, value)
        self.list_view.refresh_item(item.id)
        self._maybe_save_queue_snapshot()

    def _on_item_range_change(self, item: PrintItem, value: str) -> None:
        item.page_range = value.strip()
        self._maybe_save_queue_snapshot()

    # ------------------------------------------------------------------ 삭제
    def _on_delete_key(self, event: tk.Event) -> None:
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, ttk.Spinbox, tk.Spinbox)):
            return  # 텍스트 입력 중이면 일반 삭제 키 동작을 방해하지 않음
        self.list_view.delete_selected()

    def _on_delete_request(self, item_id: str) -> None:
        item = next((i for i in self.items if i.id == item_id), None)
        if item is None:
            return
        if item.status == PrintStatus.PRINTING:
            messagebox.showinfo("삭제 불가", "현재 인쇄 중인 항목은 삭제할 수 없습니다.")
            return
        self.items = [i for i in self.items if i.id != item_id]
        self._rerender()

    # ------------------------------------------------------------------ 재시도
    def _on_retry_request(self, item_id: str) -> None:
        """❌ 클릭 후 [다시 시도] 선택 시: 대기 상태로 되돌린다.
        진행 중이면 워커가 다음 순번에 자동으로 다시 집어가고, 아니면 "인쇄 시작"을 눌러야 한다."""
        item = next((i for i in self.items if i.id == item_id), None)
        if item is None or item.status != PrintStatus.FAILED:
            return
        with self._lock:
            item.status = PrintStatus.WAITING
            item.fail_reason = None
        self._refresh_item_and_counts(item_id)

    def _on_retry_all_clicked(self) -> None:
        with self._lock:
            failed = [i for i in self.items if i.status == PrintStatus.FAILED]
            for i in failed:
                i.status = PrintStatus.WAITING
                i.fail_reason = None
        if failed:
            self._rerender()

    # ------------------------------------------------------------------ 순서 변경
    def _on_reorder_commit(self, new_order_ids: list[str]) -> None:
        by_id = {item.id: item for item in self.items}
        self.items = [by_id[iid] for iid in new_order_ids if iid in by_id]
        # 화면은 드래그 중 이미 최신 순서로 갱신돼 있으므로 다시 그릴 필요 없음

    # ------------------------------------------------------------------ 전체 비우기
    def _on_clear_all_clicked(self) -> None:
        if any(i.status == PrintStatus.PRINTING for i in self.items):
            return  # 진행 중일 때는 버튼이 비활성화되어 있어야 하지만 안전차 재확인
        if not self.items:
            return
        if messagebox.askyesno("전체 비우기", f"리스트의 항목 {len(self.items)}개를 모두 삭제할까요?"):
            self.items = []
            self._rerender()

    # ------------------------------------------------------------------ 리스트 정리
    def _on_cleanup_clicked(self) -> None:
        """완료/실패 기록은 사용자가 정리할 때까지 재인쇄할 수 있게 유지한다."""
        before = len(self.items)
        if not messagebox.askyesno("목록 정리", "완료·실패 항목을 목록에서 제거할까요? 원본 파일은 유지됩니다."):
            return
        self.items = [i for i in self.items if i.status not in (PrintStatus.FAILED, PrintStatus.DONE)]
        if len(self.items) != before:
            self._rerender()

    # ------------------------------------------------------------------ 공통 갱신
    def _rerender(self) -> None:
        self.list_view.set_items(self.items)
        self._refresh_status_counts()
        self._refresh_clear_all_state()
        self._maybe_save_queue_snapshot()

    def _refresh_status_counts(self) -> None:
        waiting = sum(1 for i in self.items if i.status in (PrintStatus.WAITING, PrintStatus.PAUSED))
        printing = sum(1 for i in self.items if i.status == PrintStatus.PRINTING)
        done = sum(1 for i in self.items if i.status == PrintStatus.DONE)
        failed = sum(1 for i in self.items if i.status == PrintStatus.FAILED)
        self.control_panel.set_status_counts(waiting, printing, done, failed)

    def _refresh_clear_all_state(self) -> None:
        printing = any(i.status == PrintStatus.PRINTING for i in self.items)
        state = "disabled" if printing else "!disabled"
        self.control_panel.clear_all_btn.state([state])
