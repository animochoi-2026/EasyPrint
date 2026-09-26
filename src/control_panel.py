"""All print controls on one white panel without tabs."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk


class ControlPanel(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, padding=(18, 16), style="Sidebar.TFrame", **kwargs)
        self.columnconfigure(0, weight=1)
        self._printing = False
        self._schedule_active = False
        ttk.Label(self, text="인쇄 설정", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 12))
        ttk.Label(self, text="프린터", style="SidebarSection.TLabel").grid(row=1, column=0, sticky="w")
        self.printer_var = tk.StringVar()
        self.printer_combo = ttk.Combobox(self, textvariable=self.printer_var, state="readonly", width=27)
        self.printer_combo.grid(row=2, column=0, sticky="ew", pady=(5, 6))
        self.printer_options_btn = ttk.Button(self, text="프린터 속성 / 용지 설정")
        self.printer_options_btn.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.start_btn = ttk.Button(self, text="대기 파일 인쇄", style="Primary.TButton")
        self.start_btn.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, sticky="ew")
        actions.columnconfigure((0, 1), weight=1)
        self.stop_btn = ttk.Button(actions, text="현재 파일 후 정지", state="disabled")
        self.abort_btn = ttk.Button(actions, text="강제 취소", style="Danger.TButton", state="disabled")
        self.stop_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.abort_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Separator(self).grid(row=6, column=0, sticky="ew", pady=12)
        ttk.Label(self, text="예약 인쇄", style="SidebarSection.TLabel").grid(row=7, column=0, sticky="w")
        schedule = ttk.Frame(self)
        schedule.grid(row=8, column=0, sticky="ew", pady=(5, 0))
        self.schedule_var = tk.StringVar()
        self.schedule_entry = ttk.Entry(schedule, textvariable=self.schedule_var, width=7)
        self.schedule_entry.pack(side="left")
        ttk.Label(schedule, text=" HH:MM ", style="Secondary.TLabel").pack(side="left")
        self.schedule_set_btn = ttk.Button(schedule, text="예약")
        self.schedule_set_btn.pack(side="left", padx=(0, 4))
        self.schedule_cancel_btn = ttk.Button(schedule, text="해제", state="disabled")
        self.schedule_cancel_btn.pack(side="left")
        self.schedule_status_var = tk.StringVar(value="예약 없음")
        self.schedule_status_label = ttk.Label(self, textvariable=self.schedule_status_var, style="Secondary.TLabel")
        self.schedule_status_label.grid(row=9, column=0, sticky="w", pady=(4, 0))
        ttk.Separator(self).grid(row=10, column=0, sticky="ew", pady=12)
        ttk.Label(self, text="목록 관리", style="SidebarSection.TLabel").grid(row=11, column=0, sticky="w")
        self.retry_all_btn = ttk.Button(self, text="실패 파일 다시 대기")
        self.retry_all_btn.grid(row=12, column=0, sticky="ew", pady=(5, 5))
        management = ttk.Frame(self)
        management.grid(row=13, column=0, sticky="ew")
        management.columnconfigure((0, 1), weight=1)
        self.cleanup_btn = ttk.Button(management, text="완료·실패 정리")
        self.clear_all_btn = ttk.Button(management, text="전체 비우기", style="Danger.TButton")
        self.cleanup_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.clear_all_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Separator(self).grid(row=14, column=0, sticky="ew", pady=12)
        ttk.Label(self, text="모두 끝나면", style="SidebarSection.TLabel").grid(row=15, column=0, sticky="w")
        self.on_finish_var = tk.StringVar(value="")
        self._on_finish_checks = {}
        for row, (value, label) in enumerate((("exit_app", "프로그램 종료"), ("shutdown", "Windows 종료"), ("sleep", "절전 모드")), 16):
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(self, text=label, variable=var, style="Sidebar.TCheckbutton",
                                  command=lambda v=value, b=var: self._on_finish_toggle(v, b))
            chk.grid(row=row, column=0, sticky="w", pady=1)
            self._on_finish_checks[value] = (chk, var)
        ttk.Separator(self).grid(row=19, column=0, sticky="ew", pady=12)
        shell = ttk.Frame(self)
        shell.grid(row=20, column=0, sticky="ew")
        shell.columnconfigure((0, 1), weight=1)
        self.shell_install_btn = ttk.Button(shell, text="우클릭 메뉴 등록")
        self.shell_remove_btn = ttk.Button(shell, text="등록 해제")
        self.shell_install_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.shell_remove_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(self, text="DOCX: Microsoft Word 필요", style="Secondary.TLabel").grid(row=21, column=0, sticky="w", pady=(6, 0))
        self.status_count_var = tk.StringVar(value="대기 0  ·  출력중 0")
        ttk.Label(self, textvariable=self.status_count_var, style="Secondary.TLabel").grid(row=22, column=0, sticky="w", pady=(12, 0))
        self.status_count_label2 = ttk.Label(self, text="완료 0  ·  실패 0", style="Secondary.TLabel")
        self.status_count_label2.grid(row=23, column=0, sticky="w")

    def _on_finish_toggle(self, selected_value, selected_var):
        self.on_finish_var.set(selected_value if selected_var.get() else "")
        for value, (_, var) in self._on_finish_checks.items():
            if value != selected_value:
                var.set(False)

    def set_status_counts(self, waiting, printing, done, failed):
        self.status_count_var.set(f"대기 {waiting}  ·  출력중 {printing}")
        self.status_count_label2.configure(text=f"완료 {done}  ·  실패 {failed}")

    def set_printers(self, printers, selected=None):
        self.printer_combo["values"] = printers
        if selected and selected in printers:
            self.printer_var.set(selected)
        elif printers:
            self.printer_var.set(printers[0])

    def set_printing_state(self, printing):
        self._printing = printing
        self.start_btn.configure(text="인쇄 진행 중…" if printing else "대기 파일 인쇄")
        self.start_btn.state(["disabled" if printing else "!disabled"])
        self.stop_btn.state(["!disabled" if printing else "disabled"])
        self.abort_btn.state(["!disabled" if printing else "disabled"])
        self.printer_combo.configure(state="disabled" if printing else "readonly")
        self.printer_options_btn.state(["disabled" if printing else "!disabled"])
        self._refresh_schedule_lock()

    def set_schedule_active(self, active, label_text=""):
        self._schedule_active = active
        self.schedule_status_var.set(label_text if active else "예약 없음")
        self.schedule_cancel_btn.state(["!disabled" if active else "disabled"])
        if not active:
            self.schedule_var.set("")
        self._refresh_schedule_lock()

    def _refresh_schedule_lock(self):
        locked = self._printing or self._schedule_active
        for widget in (self.schedule_entry, self.schedule_set_btn):
            widget.state(["disabled" if locked else "!disabled"])
