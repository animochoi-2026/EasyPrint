"""가벼운 호버 툴팁 (페이지 범위 입력 오류 사유 표시용)."""
from __future__ import annotations

import tkinter as tk


class Tooltip:
    def __init__(self, widget: tk.Widget):
        self.widget = widget
        self.text = ""
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text
        if not text:
            self._hide()

    def _show(self, _event=None) -> None:
        if not self.text or self._tip is not None:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self.text, background="#ffffe0",
            relief="solid", borderwidth=1, padx=4, pady=2, font=("맑은 고딕", 9),
        ).pack()

    def _hide(self, _event=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
