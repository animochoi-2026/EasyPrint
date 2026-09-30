"""화이트 배경의 애플 스타일 테마 (팔레트 + ttk 스타일 정의).

Windows 기본 ttk 테마("vista")는 배경색·테두리색 커스터마이징을 대부분 무시하므로
"clam" 테마를 기반으로 색상을 전면 재정의한다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG_MAIN = "#ffffff"
BG_SIDEBAR = "#ffffff"
BG_ROW_HOVER = "#f5f5f7"
BG_ROW_SELECTED = "#e8f0fe"
BG_ROW_PRINTING = "#eaf3ff"
BG_ROW_DONE = "#ededf0"
BG_ROW_DONE_HOVER = "#e3e3e8"
BG_ROW_DONE_SELECTED = "#dce5ee"
BG_DONE_FIELD = "#f5f5f7"
TEXT_DONE = "#515158"
TEXT_DONE_SECONDARY = "#62626b"
BORDER_DONE = "#c6c6cd"

BORDER = "#e5e5ea"
BORDER_STRONG = "#d2d2d7"

TEXT_PRIMARY = "#1d1d1f"
TEXT_SECONDARY = "#6e6e73"
TEXT_ON_ACCENT = "#ffffff"

ACCENT = "#007aff"
ACCENT_HOVER = "#0066d6"
ACCENT_DISABLED = "#c7c7cc"

DANGER = "#ff3b30"
DANGER_BORDER = "#ffd0cd"
DANGER_HOVER_BG = "#fff1f0"

FONT_FAMILY = "맑은 고딕"
FONT_BASE = (FONT_FAMILY, 10)
FONT_BASE_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_SECTION = (FONT_FAMILY, 9, "bold")
FONT_ICON = ("Segoe UI Emoji", 12)


def apply_theme(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")

    root.configure(background=BG_MAIN)

    style.configure(".", background=BG_MAIN, foreground=TEXT_PRIMARY, font=FONT_BASE)

    style.configure("TFrame", background=BG_MAIN)
    style.configure("Sidebar.TFrame", background=BG_SIDEBAR)

    style.configure("TLabel", background=BG_MAIN, foreground=TEXT_PRIMARY, font=FONT_BASE)
    style.configure("Title.TLabel", background=BG_MAIN, foreground=TEXT_PRIMARY, font=(FONT_FAMILY, 15, "bold"))
    style.configure("Secondary.TLabel", background=BG_MAIN, foreground=TEXT_SECONDARY, font=FONT_SMALL)
    style.configure("Sidebar.TLabel", background=BG_SIDEBAR, foreground=TEXT_PRIMARY, font=FONT_BASE)
    style.configure("SidebarSecondary.TLabel", background=BG_SIDEBAR, foreground=TEXT_SECONDARY, font=FONT_SMALL)
    style.configure("SidebarSection.TLabel", background=BG_SIDEBAR, foreground=TEXT_SECONDARY, font=FONT_SECTION)

    style.configure("TSeparator", background=BORDER)

    # 기본(보조) 버튼 - 얇은 테두리의 플랫 버튼
    style.configure(
        "TButton",
        background=BG_SIDEBAR,
        foreground=TEXT_PRIMARY,
        bordercolor=BORDER_STRONG,
        lightcolor=BG_SIDEBAR,
        darkcolor=BG_SIDEBAR,
        borderwidth=1,
        focusthickness=0,
        focuscolor=BORDER_STRONG,
        padding=(8, 5),
        font=FONT_BASE,
    )
    style.map(
        "TButton",
        background=[("active", "#ececee"), ("disabled", BG_SIDEBAR)],
        foreground=[("disabled", TEXT_SECONDARY)],
    )

    # 인쇄 시작 - 강조(Primary) 버튼
    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground=TEXT_ON_ACCENT,
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        borderwidth=0,
        focusthickness=0,
        focuscolor=ACCENT,
        padding=(10, 8),
        font=FONT_BASE_BOLD,
    )
    style.map(
        "Primary.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", ACCENT_DISABLED)],
        foreground=[("disabled", TEXT_ON_ACCENT)],
    )

    # 위험 동작(전체비우기/강제취소) - 빨간 텍스트의 아웃라인 버튼
    style.configure(
        "Danger.TButton",
        background=BG_SIDEBAR,
        foreground=DANGER,
        bordercolor=DANGER_BORDER,
        lightcolor=BG_SIDEBAR,
        darkcolor=BG_SIDEBAR,
        borderwidth=1,
        focusthickness=0,
        focuscolor=DANGER_BORDER,
        padding=(8, 5),
        font=FONT_BASE,
    )
    style.map(
        "Danger.TButton",
        background=[("active", DANGER_HOVER_BG), ("disabled", BG_SIDEBAR)],
        foreground=[("disabled", TEXT_SECONDARY)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=BG_MAIN,
        background=BG_MAIN,
        bordercolor=BORDER_STRONG,
        arrowcolor=TEXT_SECONDARY,
        padding=6,
        font=FONT_BASE,
    )
    style.map("TCombobox", fieldbackground=[("readonly", BG_MAIN)])

    style.configure("Sidebar.TCheckbutton", background=BG_SIDEBAR, foreground=TEXT_PRIMARY, font=FONT_BASE)
    style.map("Sidebar.TCheckbutton", background=[("active", BG_SIDEBAR)])

    style.configure("TSpinbox", fieldbackground=BG_MAIN, bordercolor=BORDER_STRONG, padding=4, font=FONT_BASE)
    style.configure("TEntry", fieldbackground=BG_MAIN, bordercolor=BORDER_STRONG, padding=4, font=FONT_BASE)

    style.configure(
        "Vertical.TScrollbar",
        background=BG_SIDEBAR,
        troughcolor=BG_MAIN,
        bordercolor=BG_MAIN,
        arrowcolor=TEXT_SECONDARY,
        gripcount=0,
        width=10,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER_STRONG)])

    configure_row_styles(style)
    return style


def row_style_names(kind: str) -> tuple[str, str, str, str]:
    """상태별 (frame, label, secondary_label, close_button 스타일명)."""
    prefix = {"plain": "RowPlain", "hover": "RowHover", "selected": "RowSelected", "printing": "RowPrinting",
              "done": "RowDone", "done_hover": "RowDoneHover", "done_selected": "RowDoneSelected"}[kind]
    return f"{prefix}.TFrame", f"{prefix}.TLabel", f"{prefix}Secondary.TLabel", f"{prefix}Close.TLabel"


def configure_row_styles(style: ttk.Style) -> None:
    variants = {
        "plain": BG_MAIN,
        "hover": BG_ROW_HOVER,
        "selected": BG_ROW_SELECTED,
        "printing": BG_ROW_PRINTING,
        "done": BG_ROW_DONE,
        "done_hover": BG_ROW_DONE_HOVER,
        "done_selected": BG_ROW_DONE_SELECTED,
    }
    for kind, bg in variants.items():
        frame_style, label_style, secondary_style, close_style = row_style_names(kind)
        done = kind.startswith("done")
        primary = TEXT_DONE if done else TEXT_PRIMARY
        secondary = TEXT_DONE_SECONDARY if done else TEXT_SECONDARY
        style.configure(frame_style, background=bg)
        style.configure(label_style, background=bg, foreground=primary, font=FONT_BASE)
        style.configure(secondary_style, background=bg, foreground=secondary, font=FONT_SMALL)
        style.configure(close_style, background=bg, foreground=secondary, font=(FONT_FAMILY, 11))
        style.map(close_style, foreground=[("active", DANGER)])

    style.configure("Done.TSpinbox", fieldbackground=BG_DONE_FIELD, background=BG_DONE_FIELD,
                    foreground=TEXT_DONE, arrowcolor=TEXT_DONE, insertcolor=TEXT_DONE,
                    bordercolor=BORDER_DONE, lightcolor=BG_DONE_FIELD, darkcolor=BG_DONE_FIELD)
    style.map("Done.TSpinbox", fieldbackground=[("disabled", BG_DONE_FIELD), ("readonly", BG_DONE_FIELD)],
              background=[("active", BG_ROW_DONE_HOVER), ("disabled", BG_DONE_FIELD)],
              foreground=[("disabled", TEXT_DONE_SECONDARY)],
              arrowcolor=[("disabled", TEXT_DONE_SECONDARY)],
              bordercolor=[("focus", ACCENT)])
