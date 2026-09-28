"""좌측 인쇄 리스트 위젯.

설계 계획서 3장: 대기/완료/실패를 나누지 않는 단일 스크롤 리스트.
Phase 2: 드래그앤드롭 추가, 드래그로 순서 변경(진행 중 항목만 고정),
선택/삭제(Delete·우클릭)를 지원한다.
Phase 4: 페이지 범위 입력을 포커스아웃 시점에 검증(형식 오류·페이지 수 초과)해
빨간 테두리 + 툴팁으로 안내한다. PDF 페이지 수는 필요한 순간에만 지연 조회한다.
화이트/애플 스타일 테마는 src/theme.py에서 정의하고 여기서 적용한다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .models import IMAGE_EXTENSIONS, PENDING_STATUSES, PrintItem, PrintStatus
from .pdf_utils import get_pdf_page_count
from .theme import (
    BG_MAIN,
    BORDER,
    BORDER_STRONG,
    FONT_BASE,
    FONT_ICON,
    FONT_SMALL,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    row_style_names,
)
from .tooltip import Tooltip
from .validation import parse_page_range

ICON_FONT = FONT_ICON
NAME_FONT = FONT_BASE
DANGER = "#ff3b30"
ACCENT = "#007aff"


class ScrollableFrame(ttk.Frame):
    """마우스 휠 스크롤을 지원하는 세로 스크롤 컨테이너."""

    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(master, **kwargs)

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, background=BG_MAIN)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._inner_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._scroll_sync_id = None
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
        self.canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())

    def _on_inner_configure(self, _event=None) -> None:
        self._sync_scroll_region()

    def destroy(self):
        if self._scroll_sync_id is not None:
            self.after_cancel(self._scroll_sync_id)
        super().destroy()

    def _sync_scroll_region(self) -> None:
        height = self._content_height()
        viewport = self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), max(height, viewport)))
        if height <= viewport:
            self.canvas.yview_moveto(0)
        else:
            top, bottom = self.canvas.yview()
            if bottom > 1:
                self.canvas.yview_moveto(max(0, 1 - viewport / height))

    def _content_height(self) -> int:
        return self.inner.winfo_reqheight()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfig(self._inner_window, width=event.width)
        self._sync_scroll_region()

    def _bind_mousewheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self._content_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def scroll_to_y_fraction(self, y_root: int) -> None:
        """드래그 중 캔버스 경계 근처에서 자동 스크롤하기 위한 헬퍼."""
        top = self.canvas.winfo_rooty()
        bottom = top + self.canvas.winfo_height()
        margin = 24
        if y_root < top + margin:
            self.canvas.yview_scroll(-1, "units")
        elif y_root > bottom - margin:
            self.canvas.yview_scroll(1, "units")


class PrintRow(ttk.Frame):
    """리스트 한 줄: 상태 아이콘 + 파일명 + 매수 + 페이지범위."""

    def __init__(
        self,
        master: tk.Misc,
        item: PrintItem,
        on_copies_change: Callable[[PrintItem, int], None] | None = None,
        on_range_change: Callable[[PrintItem, str], None] | None = None,
        on_delete_request: Callable[[str], None] | None = None,
        on_retry_request: Callable[[str], None] | None = None,
    ):
        super().__init__(master, padding=(14, 5))
        self.item = item
        self._on_copies_change = on_copies_change
        self._on_range_change = on_range_change
        self._on_delete_request = on_delete_request
        self._on_retry_request = on_retry_request
        self.selected = False
        self.hovering = False
        self.options_locked = False

        self.icon_var = tk.StringVar(value=item.icon)
        self.icon_label = ttk.Label(self, textvariable=self.icon_var, font=ICON_FONT, width=2, anchor="center")
        self.icon_label.grid(row=0, column=0, padx=(0, 8))
        self.icon_label.bind("<Button-1>", self._on_icon_click, add="+")

        self.name_label = ttk.Label(self, text=item.file_name, font=NAME_FONT, anchor="w")
        self.name_label.grid(row=0, column=1, sticky="ew")

        self.copies_caption = ttk.Label(self, text="매수", style="Secondary.TLabel")
        self.copies_caption.grid(row=0, column=2, padx=(14, 4))
        self.copies_var = tk.StringVar(value=str(item.copies))
        self._displayed_copies = str(item.copies)
        self.copies_spin = ttk.Spinbox(
            self, from_=1, to=999, width=4, textvariable=self.copies_var,
            command=self._emit_copies_change,
        )
        self.copies_spin.grid(row=0, column=3)
        self.copies_spin.bind("<FocusOut>", lambda e: self._emit_copies_change())
        self.copies_spin.bind("<Return>", lambda e: self._emit_copies_change())

        self.range_caption = ttk.Label(self, text="범위", style="Secondary.TLabel")
        self.range_caption.grid(row=0, column=4, padx=(14, 4))
        self.range_var = tk.StringVar(value=item.page_range)
        self._displayed_range = item.page_range
        # 빨간 테두리 표시를 위해 highlightthickness를 지원하는 일반 tk.Entry 사용
        self.range_entry = tk.Entry(
            self, width=10, textvariable=self.range_var,
            relief="flat", highlightthickness=1,
            background=BG_MAIN, foreground=TEXT_PRIMARY, font=FONT_BASE,
            highlightbackground=BORDER_STRONG, highlightcolor=ACCENT,
            insertbackground=TEXT_PRIMARY,
        )
        self.range_entry.grid(row=0, column=5, ipady=3)
        self._range_tooltip = Tooltip(self.range_entry)
        self.range_entry.bind("<FocusIn>", lambda e: self._ensure_page_count_loaded(), add="+")
        self.range_entry.bind("<FocusOut>", lambda e: self._emit_range_change())
        self.range_entry.bind("<Return>", lambda e: self._emit_range_change())
        if item.extension in (".hwp", ".hwpx"):
            self.range_entry.configure(state="disabled")

        self.close_btn = ttk.Label(self, text="✕", cursor="hand2", anchor="center", width=2)
        self.close_btn.grid(row=0, column=6, padx=(10, 0))
        self.close_btn.bind("<Button-1>", lambda e: self._emit_delete())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.state(["active"]), add="+")
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.state(["!active"]), add="+")

        self.columnconfigure(1, weight=1)

        self.detail_label = ttk.Label(self, style="Secondary.TLabel")
        self.detail_label.grid(row=1, column=1, sticky="w", pady=(3, 2))
        self.divider = tk.Frame(self, height=1, background=BORDER)
        self.divider.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(5, 0))

        # 선택/드래그는 아이콘·파일명·빈 배경 영역에서만 반응 (스핀박스/입력창 조작과 충돌 방지)
        self._drag_targets = [self, self.icon_label, self.name_label, self.detail_label]
        for widget in self._drag_targets:
            widget.bind("<Enter>", self._on_hover_enter, add="+")
            widget.bind("<Leave>", self._on_hover_leave, add="+")

        self.refresh()

    def _emit_delete(self) -> None:
        if self._on_delete_request:
            self._on_delete_request(self.item.id)

    def _on_icon_click(self, _event=None) -> None:
        """계획서 4장: ❌ 클릭 시 실패 사유 팝업 + [다시 시도]."""
        if self.item.status != PrintStatus.FAILED:
            return
        reason = self.item.fail_reason or "알 수 없는 오류"
        if messagebox.askretrycancel("인쇄 실패", f"{self.item.file_name}\n\n{reason}"):
            if self._on_retry_request:
                self._on_retry_request(self.item.id)

    def _emit_copies_change(self) -> None:
        if self.options_locked:
            return
        try:
            value = int(self.copies_var.get())
        except ValueError:
            value = self.item.copies
            self.copies_var.set(str(value))
        if self._on_copies_change:
            self._on_copies_change(self.item, value)

    def _ensure_page_count_loaded(self) -> None:
        """페이지 수는 앱의 메타데이터 워커가 읽는다; 포커스 이동은 조회하지 않는다."""
        self._update_page_count_caption()

    def _update_page_count_caption(self) -> None:
        kind = self.item.extension.lstrip(".").upper()
        if self.item.page_count is not None:
            detail = f"{kind}  ·  총 {self.item.page_count}쪽"
        elif self.item.extension == ".pdf":
            detail = f"PDF  ·  {'페이지 수 확인 불가' if self.item.page_count_checked else '페이지 수 확인 중…'}"
        elif self.item.extension in (".hwp", ".hwpx"):
            detail = f"{kind}  ·  전체 페이지 인쇄"
        elif self.item.extension == ".docx":
            detail = "DOCX  ·  Word 직접 인쇄 · 페이지 지정 가능"
        else:
            detail = f"{kind}  ·  A4 한 장에 맞춤"
        self.detail_label.configure(text=detail)
        self.range_caption.configure(text="전체" if self.item.extension in (".hwp", ".hwpx") else "범위")

    def _emit_range_change(self) -> tuple[bool, str | None]:
        if self.options_locked:
            return True, None
        raw = self.range_var.get()

        page_count = self.item.page_count
        if self.item.extension == ".pdf" and page_count is None:
            page_count = get_pdf_page_count(self.item.file_path)
            self.item.page_count = page_count
            self._update_page_count_caption()

        ok, reason = parse_page_range(raw, page_count)
        if raw.strip() and self.item.extension in (".hwp", ".hwpx"):
            ok, reason = False, "한글 문서는 전체 페이지 인쇄만 지원합니다. 범위를 비워주세요."
        self._set_range_invalid(not ok, reason)
        if ok and self._on_range_change:
            self._on_range_change(self.item, raw.strip())
        return ok, reason

    def _set_range_invalid(self, invalid: bool, reason: str | None) -> None:
        if invalid:
            self.range_entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
        else:
            self.range_entry.configure(highlightbackground=BORDER_STRONG, highlightcolor=ACCENT)
        self._range_tooltip.set_text(reason or "")

    def refresh(self) -> None:
        self.icon_var.set(self.item.icon)
        if self.item.stage_note:
            self.name_label.configure(text=f"{self.item.file_name}  —  {self.item.stage_note}")
        else:
            self.name_label.configure(text=self.item.file_name)
        if self.copies_var.get() == self._displayed_copies:
            self.copies_var.set(str(self.item.copies))
        self._displayed_copies = str(self.item.copies)
        # Preserve an uncommitted edit (including invalid text) during refresh.
        if self.range_var.get() == self._displayed_range:
            self.range_var.set(self.item.page_range)
        self._displayed_range = self.item.page_range
        self._update_page_count_caption()
        locked = self.options_locked or self.item.status == PrintStatus.PRINTING
        self.copies_spin.state(["disabled" if locked else "!disabled"])
        self.range_entry.configure(state="disabled" if locked or self.item.extension in (".hwp", ".hwpx") else "normal")
        self._apply_bg()

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self._apply_bg()

    def _on_hover_enter(self, _event=None) -> None:
        self.hovering = True
        self._apply_bg()

    def _on_hover_leave(self, _event=None) -> None:
        self.hovering = False
        self._apply_bg()

    def _row_kind(self) -> str:
        if self.item.status == PrintStatus.PRINTING:
            return "printing"
        if self.selected:
            return "selected"
        if self.hovering:
            return "hover"
        return "plain"

    def _apply_bg(self) -> None:
        frame_style, label_style, secondary_style, close_style = row_style_names(self._row_kind())
        self.configure(style=frame_style)
        self.divider.configure(background=BORDER)
        for w in (self.icon_label, self.name_label):
            w.configure(style=label_style)
        for w in (self.copies_caption, self.range_caption, self.detail_label):
            w.configure(style=secondary_style)
        self.close_btn.configure(style=close_style)


class PrintListView(ScrollableFrame):
    """인쇄 리스트 전체를 렌더링하는 컨테이너.

    add/remove/reorder는 콜백을 통해 상위(App)의 데이터를 갱신하고,
    이 위젯은 항상 전달받은 items 순서를 그대로 그린다.
    """

    def __init__(
        self,
        master: tk.Misc,
        on_copies_change: Callable[[PrintItem, int], None] | None = None,
        on_range_change: Callable[[PrintItem, str], None] | None = None,
        on_delete_request: Callable[[str], None] | None = None,
        on_reorder_commit: Callable[[list[str]], None] | None = None,
        on_retry_request: Callable[[str], None] | None = None,
        on_reprint_request=None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self._rows: dict[str, PrintRow] = {}
        self._order: list[str] = []
        self._items_by_id: dict[str, PrintItem] = {}
        self._selected_id: str | None = None
        self._locked_ids = set()
        self._scroll_to_bottom_pending = False

        self._on_copies_change = on_copies_change
        self._on_range_change = on_range_change
        self._on_retry_request = on_retry_request
        self._on_delete_request = on_delete_request
        self._on_reorder_commit = on_reorder_commit
        self._on_reprint_request = on_reprint_request

        self._drag_id: str | None = None
        self._drag_active = False

        self._empty_label = ttk.Label(
            self.inner, text="파일을 여기에 놓으세요\n\nPDF · 한글 · 사진 · DOCX",
            style="Secondary.TLabel", padding=32, anchor="center",
        )

        self._context_menu = tk.Menu(
            self, tearoff=0, background=BG_MAIN, foreground=TEXT_PRIMARY,
            activebackground="#e8f0fe", activeforeground=ACCENT, borderwidth=1,
            relief="solid",
        )
        self._context_menu.add_command(label="삭제", command=self._delete_from_context_menu)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="선택 파일만 재인쇄", command=lambda: self.request_reprint(False))
        self._context_menu.add_command(label="여기부터 끝까지 재인쇄", command=lambda: self.request_reprint(True))
        self._context_target_id: str | None = None

    # ------------------------------------------------------------------ 렌더링
    def lock_items(self, ids):
        self._locked_ids = set(ids)
        for item_id, row in self._rows.items():
            row.options_locked = item_id in self._locked_ids
            row.refresh()

    def commit_pending_options(self, item_ids=None) -> bool:
        """인쇄 시작 전에 대기 항목의 입력값을 검증하고 확정한다."""
        for item_id in self._order:
            row = self._rows[item_id]
            if (item_ids is None and row.item.status not in PENDING_STATUSES) or (item_ids is not None and item_id not in item_ids):
                continue
            ok, reason = row._emit_range_change()
            if not ok:
                messagebox.showwarning(
                    "페이지 범위 확인",
                    f"{row.item.file_name}\n\n{reason}\n\n범위를 수정한 뒤 인쇄해주세요.",
                )
                row.range_entry.focus_set()
                return False
            # This callback refreshes the row, so commit the range first.
            row._emit_copies_change()
        return True

    def set_items(self, items: list[PrintItem]) -> None:
        # Keep existing widgets and edits; destroying the whole grid on every
        # drop causes geometry/scroll churn and loses in-progress input.
        ids = {item.id for item in items}
        added = bool(ids.difference(self._rows))
        self._scroll_to_bottom_pending = bool(items) and (self._scroll_to_bottom_pending or added)
        for item_id in list(self._rows):
            if item_id not in ids:
                self._rows.pop(item_id).destroy()
        self._empty_label.grid_forget()

        self._order = [item.id for item in items]
        self._items_by_id = {item.id: item for item in items}

        if not items:
            self._empty_label.grid(row=0, column=0, sticky="ew")

        for index, item in enumerate(items):
            if item.id not in self._rows:
                self._rows[item.id] = self._make_row(item, index)
            else:
                self._rows[item.id].grid_configure(row=index)
                self._rows[item.id].refresh()

        if self._selected_id not in self._items_by_id:
            self._selected_id = None
        self._drag_id = None
        self._drag_active = False
        if self._scroll_sync_id is not None:
            self.after_cancel(self._scroll_sync_id)
        self._scroll_sync_id = self.after_idle(self._settle_items_layout)

    def _content_height(self) -> int:
        # Canvas-only padding: exactly one row below the final file, never a
        # fake item or an accumulating grid row. It also scales with the font.
        height = super()._content_height()
        if self._order:
            height += self._rows[self._order[-1]].winfo_reqheight()
        return height

    def _settle_items_layout(self) -> None:
        # Tk can queue parent geometry propagation behind our first idle job.
        self._scroll_sync_id = self.after_idle(self._finish_items_layout)

    def _finish_items_layout(self) -> None:
        self._scroll_sync_id = None
        self._sync_scroll_region()
        if self._scroll_to_bottom_pending:
            self.canvas.yview_moveto(1.0)
            self._scroll_to_bottom_pending = False

    def request_reprint(self, from_here=False) -> None:
        if self._selected_id and self._on_reprint_request:
            self._on_reprint_request(self._selected_id, from_here)
        elif not self._selected_id:
            messagebox.showinfo("재인쇄", "목록에서 파일을 먼저 선택하세요.")

    def _make_row(self, item: PrintItem, index: int) -> PrintRow:
        row = PrintRow(
            self.inner, item,
            on_copies_change=self._on_copies_change,
            on_range_change=self._on_range_change,
            on_delete_request=self._on_delete_request,
            on_retry_request=self._on_retry_request,
        )
        row.grid(row=index, column=0, sticky="ew")
        row.options_locked = item.id in self._locked_ids
        row.refresh()
        self.inner.columnconfigure(0, weight=1)
        row.set_selected(item.id == self._selected_id)

        for widget in row._drag_targets:
            widget.bind("<Button-1>", lambda e, iid=item.id: self._on_row_press(e, iid), add="+")
            widget.bind("<B1-Motion>", self._on_row_motion, add="+")
            widget.bind("<ButtonRelease-1>", self._on_row_release, add="+")
            widget.bind("<Button-3>", lambda e, iid=item.id: self._on_row_right_click(e, iid), add="+")
        return row

    def refresh_item(self, item_id: str) -> None:
        row = self._rows.get(item_id)
        if row:
            row.refresh()

    # ------------------------------------------------------------------ 선택
    def _select(self, item_id: str | None) -> None:
        if self._selected_id == item_id:
            return
        prev = self._rows.get(self._selected_id) if self._selected_id else None
        if prev:
            prev.set_selected(False)
        self._selected_id = item_id
        cur = self._rows.get(item_id) if item_id else None
        if cur:
            cur.set_selected(True)

    def get_selected_id(self) -> str | None:
        return self._selected_id

    # ------------------------------------------------------------------ 삭제
    def _on_row_right_click(self, event: tk.Event, item_id: str) -> None:
        self._select(item_id)
        self._context_target_id = item_id
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _delete_from_context_menu(self) -> None:
        if self._context_target_id and self._on_delete_request:
            self._on_delete_request(self._context_target_id)

    def delete_selected(self) -> None:
        if self._selected_id and self._on_delete_request:
            self._on_delete_request(self._selected_id)

    # ------------------------------------------------------------------ 드래그 순서변경
    def _on_row_press(self, event: tk.Event, item_id: str) -> None:
        self._select(item_id)
        item = self._items_by_id.get(item_id)
        if item and item.status == PrintStatus.PRINTING:
            self._drag_id = None  # 인쇄 중인 항목은 드래그로 옮길 수 없음
            return
        self._drag_id = item_id
        self._drag_active = False

    def _on_row_motion(self, event: tk.Event) -> None:
        if not self._drag_id:
            return
        self._drag_active = True
        self.scroll_to_y_fraction(event.y_root)
        y_in_inner = event.y_root - self.inner.winfo_rooty()
        target_index = self._row_index_at_y(y_in_inner)
        current_index = self._order.index(self._drag_id)
        if target_index != current_index:
            self._order.insert(target_index, self._order.pop(current_index))
            for idx, iid in enumerate(self._order):
                self._rows[iid].grid_configure(row=idx)

    def _on_row_release(self, event: tk.Event) -> None:
        if self._drag_id and self._drag_active and self._on_reorder_commit:
            self._on_reorder_commit(list(self._order))
        self._drag_id = None
        self._drag_active = False

    def _row_index_at_y(self, y_in_inner: float) -> int:
        if not self._order:
            return 0
        cum = 0.0
        for idx, iid in enumerate(self._order):
            h = self._rows[iid].winfo_height() or 1
            cum += h
            if y_in_inner < cum:
                return idx
        return len(self._order) - 1
