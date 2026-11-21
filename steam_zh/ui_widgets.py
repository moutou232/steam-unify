"""Custom dark-theme table with multi-select checkboxes and colored tag pills.

Tkinter's Treeview can only use one foreground/background per cell, so the
category column is drawn on a Canvas.  The table holds at most a few hundred
rows, so drawing every row is cheap; the scrollbar is native Canvas scrolling.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Any, Callable

BG = "#171a21"
PANEL = "#1b2838"
PANEL2 = "#22334a"
HEADER = "#101821"
FG = "#c7d5e0"
MUTED = "#8f98a0"
ACCENT = "#66c0f4"
ZEBRA = "#1d2b3a"
HOVER = "#2a475e"
SELECTED = "#2f5c7a"
BORDER = "#2a3b4d"

CATEGORY_COLORS: dict[str, str] = {
    "视觉小说": "#9b6dff",
    "沙盒生存": "#d08b4f",
    "恐怖": "#d94126",
    "音乐节奏": "#e86fb3",
    "模拟经营": "#76b852",
    "肉鸽": "#f0a23a",
    "卡牌": "#c58b3a",
    "解谜": "#35c5c9",
    "平台跳跃": "#ff8fa3",
    "竞速": "#ef5b5b",
    "体育": "#6fcf97",
    "策略": "#5b9bd5",
    "射击": "#e67e22",
    "角色扮演": "#e2c044",
    "动作": "#e05a47",
    "冒险": "#48b3a2",
    "休闲": "#8fbf7f",
    "多人": "#8f9fd8",
    "独立": "#8fa6b8",
    "其他": "#7d8a96",
}

COLUMNS: list[tuple[str, str, int, str]] = [
    ("check", "", 28, "center"),
    ("game", "游戏名 / 原名", 290, "w"),
    ("categories", "分类", 225, "w"),
    ("status", "状态", 64, "center"),
]

COLUMN_PROFILES: dict[str, list[tuple[str, str, int, str]]] = {
    "names": [
        ("check", "", 28, "center"),
        ("game", "游戏名 / 原名", 520, "w"),
        ("status", "状态", 72, "center"),
    ],
    "categories": [
        ("check", "", 28, "center"),
        ("game", "游戏名 / 原名", 300, "w"),
        ("categories", "分类", 300, "w"),
        ("status", "状态", 72, "center"),
    ],
    "both": COLUMNS,
}

ROW_H = 44
HEADER_H = 38
PILL_H = 20


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: float, **kwargs: Any) -> int:
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=10, **kwargs)


@dataclass
class TableRow:
    appid: int
    zh: str
    orig: str
    categories: list[str]
    source: str
    status: str
    installed: bool
    raw: Any = None


class TagTable(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_selection: Callable[[set[int]], None] | None = None,
        on_check: Callable[[set[int]], None] | None = None,
        on_double: Callable[[int, str], None] | None = None,
        on_context: Callable[[tk.Event, int], None] | None = None,
        on_sort: Callable[[str], None] | None = None,
    ):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0)
        self.on_selection = on_selection
        self.on_check = on_check
        self.on_double = on_double
        self.on_context = on_context
        self.on_sort = on_sort
        self.rows: list[TableRow] = []
        self.selected: set[int] = set()
        self.checked: set[int] = set()
        self.hover_appid: int | None = None
        self.sort_key = ""
        self.sort_desc = True
        self._render_after: str | None = None
        self._scroll_render: str | None = None
        self._last_selected: int | None = None
        self.columns = list(COLUMNS)

        self.body_font = tkfont.Font(family="Microsoft YaHei UI", size=10)
        self.small_font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.bold_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.header_font = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")

        total_w = sum(c[2] for c in self.columns)
        self.total_width = total_w
        self.x_offsets: dict[str, tuple[int, int]] = {}
        x = 0
        for key, _title, width, _anchor in self.columns:
            self.x_offsets[key] = (x, x + width)
            x += width

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = tk.Canvas(
            self, height=HEADER_H, bg=HEADER, highlightthickness=0, bd=0
        )
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.configure(scrollregion=(0, 0, total_w, HEADER_H))

        wrap = tk.Frame(self, bg=BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            wrap, bg=PANEL, highlightthickness=0, bd=0,
            yscrollincrement=ROW_H // 2,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb = tk.Scrollbar(wrap, orient="vertical", command=self._yview)
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb = tk.Scrollbar(wrap, orient="horizontal", command=self._xview)
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.configure(xscrollcommand=self._body_xscroll)
        self.header.configure(xscrollcommand=self._header_xscroll)

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Control-Button-1>", self._on_ctrl_click)
        self.canvas.bind("<Shift-Button-1>", self._on_shift_click)
        self.canvas.bind("<Double-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_render())
        self.header.bind("<Button-1>", self._on_header_click)
        self.header.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Control-a>", lambda _e: self.select_all_checked())

    # ---------------------------------------------------------------- data
    def set_profile(self, profile: str) -> None:
        columns = COLUMN_PROFILES.get(profile, COLUMNS)
        if columns is self.columns:
            return
        self.columns = list(columns)
        self.total_width = sum(c[2] for c in self.columns)
        self.x_offsets = {}
        x = 0
        for key, _title, width, _anchor in self.columns:
            self.x_offsets[key] = (x, x + width)
            x += width
        self.header.configure(scrollregion=(0, 0, self.total_width, HEADER_H))
        self.canvas.configure(
            scrollregion=(0, 0, self.total_width, max(ROW_H, ROW_H * len(self.rows)))
        )
        self.render()

    def set_rows(self, rows: list[TableRow]) -> None:
        self.rows = rows
        visible = {r.appid for r in rows}
        self.checked &= visible
        self.selected &= visible
        self.hover_appid = None
        self.canvas.configure(scrollregion=(0, 0, self.total_width, max(ROW_H, ROW_H * len(rows))))
        self.render()
        self._notify()

    def set_checked(self, ids: set[int]) -> None:
        self.checked = set(ids) & {r.appid for r in self.rows}
        self.render()
        self._notify()

    def selected_ids(self) -> set[int]:
        return set(self.selected)

    def checked_ids(self) -> set[int]:
        return set(self.checked)

    def active_ids(self) -> set[int]:
        return set(self.checked) | set(self.selected)

    def select_all_checked(self) -> None:
        visible = {r.appid for r in self.rows}
        if visible and visible <= self.checked:
            self.checked -= visible
        else:
            self.checked |= visible
        self.render()
        self._notify()

    def clear_checked(self) -> None:
        self.checked.clear()
        self.render()
        self._notify()

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    # -------------------------------------------------------------- drawing
    def _fit(self, text: str, width: float, font: tkfont.Font) -> str:
        text = str(text or "")
        if font.measure(text) <= width:
            return text
        ell = "…"
        while text and font.measure(text + ell) > width:
            text = text[:-1]
        return text + ell if text else ""

    def _pill(self, x: float, y: float, category: str, max_right: float) -> float:
        color = CATEGORY_COLORS.get(category, "#7d8a96")
        text = self._fit(category, max(30, max_right - x - 18), self.small_font)
        width = self.small_font.measure(text) + 16
        if x + width > max_right:
            return x
        _round_rect(self.canvas, x, y, x + width, y + PILL_H, 8, fill=color, outline="")
        self.canvas.create_text(
            x + width / 2, y + PILL_H / 2, text=text, fill="#10161d",
            font=self.small_font, anchor="center",
        )
        return x + width + 5

    def render(self) -> None:
        self._render_after = None
        c = self.canvas
        c.delete("all")
        self._draw_header()
        width = self.total_width
        if not self.rows:
            c.create_text(
                14, 24, text="没有符合条件的游戏", fill=MUTED,
                font=self.body_font, anchor="nw",
            )
            return
        height = max(1, self.canvas.winfo_height())
        top = self.canvas.canvasy(0)
        bottom = self.canvas.canvasy(height)
        start = max(0, int(top // ROW_H) - 5)
        end = min(len(self.rows), int(bottom // ROW_H) + 6)
        for idx in range(start, end):
            row = self.rows[idx]
            y = idx * ROW_H
            bg = ZEBRA if idx % 2 else PANEL
            if row.appid in self.selected:
                bg = SELECTED
            elif row.appid == self.hover_appid:
                bg = HOVER
            c.create_rectangle(0, y, width, y + ROW_H, fill=bg, outline="")
            if row.appid in self.selected:
                c.create_rectangle(0, y, 3, y + ROW_H, fill=ACCENT, outline="")

            # checkbox
            cx, cy = 12, y + 12
            c.create_rectangle(cx, cy, cx + 17, cy + 17, outline="#5f7a90", fill="")
            if row.appid in self.checked:
                c.create_line(cx + 4, cy + 9, cx + 8, cy + 13, cx + 14, cy + 4,
                              fill=ACCENT, width=2)
            if row.installed:
                c.create_oval(cx + 20, cy + 6, cx + 26, cy + 12,
                              fill="#8fbf7f", outline="")

            # game name (Chinese on top, original underneath)
            x1, x2 = self.x_offsets["game"]
            game_width = x2 - x1 - 16
            c.create_text(
                x1 + 8, y + 13,
                text=self._fit(row.zh or row.orig or "（空）", game_width, self.body_font),
                fill=FG if row.zh else MUTED, font=self.body_font, anchor="w",
            )
            if row.orig and row.zh:
                c.create_text(
                    x1 + 8, y + 30,
                    text=self._fit(row.orig, game_width, self.small_font),
                    fill=MUTED, font=self.small_font, anchor="w",
                )
            # category pills
            if "categories" in self.x_offsets:
                x1, x2 = self.x_offsets["categories"]
                px = x1 + 8
                max_right = x2 - 10
                cats = row.categories[:3]
                if not cats:
                    self._pill(px, y + 12, "未分类", max_right)
                else:
                    for cat in cats:
                        nx = self._pill(px, y + 12, cat, max_right)
                        if nx == px:
                            break
                        px = nx
            x1, x2 = self.x_offsets["status"]
            c.create_text(
                (x1 + x2) / 2, y + ROW_H / 2,
                text=self._fit(row.status, x2 - x1 - 8, self.small_font),
                fill=self._status_color(row.status), font=self.small_font, anchor="center",
            )
            c.create_line(0, y + ROW_H, self.total_width, y + ROW_H, fill="#151e28")

    def _draw_header(self) -> None:
        h = self.header
        h.delete("all")
        h.create_rectangle(0, 0, self.total_width, HEADER_H, fill=HEADER, outline="")
        # select all checkbox
        h.create_rectangle(12, 11, 29, 28, outline="#5f7a90", fill="")
        if self.rows and {r.appid for r in self.rows} <= self.checked:
            h.create_line(16, 20, 20, 24, 26, 15, fill=ACCENT, width=2)
        for key, title, width, _anchor in self.columns:
            x1, x2 = self.x_offsets[key]
            if key == "check":
                continue
            arrow = ""
            if key == self.sort_key:
                arrow = " ▼" if self.sort_desc else " ▲"
            h.create_text(
                x1 + 8, HEADER_H / 2, text=title + arrow,
                fill="#c7d5e0", font=self.header_font, anchor="w",
            )
        h.create_line(0, HEADER_H - 1, self.total_width, HEADER_H - 1, fill="#2a3b4d")

    @staticmethod
    def _status_color(status: str) -> str:
        if status in ("已应用", "已复原"):
            return "#8fbf7f"
        if status in ("可应用",):
            return ACCENT
        if status in ("待翻译",):
            return "#e5a00d"
        return MUTED

    # -------------------------------------------------------------- events
    def _content_pos(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _row_at(self, y: float) -> int | None:
        if y < 0:
            return None
        idx = int(y // ROW_H)
        return idx if 0 <= idx < len(self.rows) else None

    def _col_at(self, x: float) -> str:
        for key, (x1, x2) in self.x_offsets.items():
            if x1 <= x < x2:
                return key
        return ""

    def _notify(self) -> None:
        if self.on_selection:
            self.on_selection(set(self.selected))
        if self.on_check:
            self.on_check(set(self.checked))

    def _toggle_checked(self, idx: int) -> None:
        appid = self.rows[idx].appid
        if appid in self.checked:
            self.checked.remove(appid)
        else:
            self.checked.add(appid)
        self._notify()
        self.render()

    def _on_click(self, event: tk.Event) -> None:
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            self.selected.clear()
            self._notify()
            self.render()
            return
        row = self.rows[idx]
        if self._col_at(x) == "check":
            self._toggle_checked(idx)
            return
        self.selected = {row.appid}
        self._last_selected = row.appid
        self._notify()
        self.render()

    def _on_ctrl_click(self, event: tk.Event) -> None:
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        row = self.rows[idx]
        if self._col_at(x) == "check":
            self._toggle_checked(idx)
            return
        if row.appid in self.selected:
            self.selected.remove(row.appid)
        else:
            self.selected.add(row.appid)
        self._last_selected = row.appid
        self._notify()
        self.render()

    def _on_shift_click(self, event: tk.Event) -> None:
        if self._last_selected is None:
            self._on_click(event)
            return
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        ids = [r.appid for r in self.rows]
        try:
            a = ids.index(self._last_selected)
        except ValueError:
            a = idx
        lo, hi = sorted((a, idx))
        self.selected.update(ids[lo:hi + 1])
        self._notify()
        self.render()

    def _on_double(self, event: tk.Event) -> None:
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        row = self.rows[idx]
        col = self._col_at(x)
        if col in ("game", "categories"):
            if self.on_double:
                self.on_double(row.appid, col)

    def _on_motion(self, event: tk.Event) -> None:
        _x, y = self._content_pos(event)
        idx = self._row_at(y)
        appid = self.rows[idx].appid if idx is not None else None
        if appid == self.hover_appid:
            return
        self.hover_appid = appid
        if self._render_after is None:
            self._render_after = self.after(20, self.render)

    def _on_leave(self, _event: tk.Event) -> None:
        if self.hover_appid is not None:
            self.hover_appid = None
            self.render()

    def _on_right(self, event: tk.Event) -> None:
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None or not self.on_context:
            return
        row = self.rows[idx]
        if row.appid not in self.selected:
            self.selected = {row.appid}
            self.render()
        self.on_context(event, row.appid)

    def _on_header_click(self, event: tk.Event) -> None:
        x = self.header.canvasx(event.x)
        if x < 38:
            self.select_all_checked()
            return
        col = self._col_at(x)
        if col and col != "check" and self.on_sort:
            if self.sort_key == col:
                self.sort_desc = not self.sort_desc
            else:
                self.sort_key = col
                self.sort_desc = True
            self.on_sort(col)

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_yscroll(self, first: str, last: str) -> None:
        self.vsb.set(first, last)
        self._schedule_render()

    def _schedule_render(self) -> None:
        if self._scroll_render is None:
            self._scroll_render = self.after_idle(self._do_scroll_render)

    def _do_scroll_render(self) -> None:
        self._scroll_render = None
        self.render()

    def _yview(self, *args: Any) -> None:
        self.canvas.yview(*args)

    def _xview(self, *args: Any) -> None:
        self.canvas.xview(*args)

    def _body_xscroll(self, first: str, last: str) -> None:
        self.hsb.set(first, last)
        self.header.xview_moveto(first)

    def _header_xscroll(self, first: str, last: str) -> None:
        self.hsb.set(first, last)
