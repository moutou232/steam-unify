"""Canvas-based rounded sidebar list."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import ui_theme as T


class SidebarList(tk.Canvas):
    def __init__(self, parent, command=None):
        super().__init__(parent, bg=T.SURFACE, highlightthickness=0, bd=0,
                         width=190)
        self._rows: list[dict] = []
        self._selected: str | None = None
        self._command = command
        self._hover: str | None = None
        self._font = tkfont.Font(family=T.FONT, size=9)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Configure>", lambda _e: self._draw())

    def insert(self, parent, index, iid=None, text="", tags=()):
        self._rows.append({"iid": str(iid), "text": str(text), "tags": tuple(tags)})
        self._draw()

    def delete(self, *iids):
        drop = {str(i) for i in iids}
        self._rows = [r for r in self._rows if r["iid"] not in drop]
        if self._selected in drop:
            self._selected = None
        self._draw()

    def get_children(self):
        return tuple(r["iid"] for r in self._rows)

    def exists(self, iid):
        return any(r["iid"] == str(iid) for r in self._rows)

    def selection(self):
        return (self._selected,) if self._selected else ()

    def selection_set(self, iid):
        self._selected = str(iid)
        self._draw()

    def selection_clear(self):
        self._selected = None
        self._draw()

    def tag_configure(self, *args, **kwargs):
        return None

    def _click(self, event):
        row = self._row_at(event.y)
        if row is None or "section" in row["tags"]:
            return
        self._selected = row["iid"]
        self._draw()
        if self._command:
            self._command(row["iid"])

    def _motion(self, event):
        row = self._row_at(event.y)
        key = row["iid"] if row and "section" not in row["tags"] else None
        self._set_hover(key)

    def _set_hover(self, key):
        if key == self._hover:
            return
        self._hover = key
        self._draw()

    def _wheel(self, event):
        # Sidebar is short in practice; keep wheel from changing table rows.
        self.yview_scroll(int(-event.delta / 120), "units")

    def _row_at(self, y):
        idx = int(y // 31)
        return self._rows[idx] if 0 <= idx < len(self._rows) else None

    def _draw(self):
        tk.Canvas.delete(self, "all")
        w = max(20, self.winfo_width())
        for idx, row in enumerate(self._rows):
            y = idx * 31
            if "section" in row["tags"]:
                self.create_text(12, y + 15, text=row["text"], anchor="w",
                                 fill=T.DIM, font=(T.FONT, 8, "bold"))
                continue
            selected = row["iid"] == self._selected
            hover = row["iid"] == self._hover
            if selected or hover:
                fill = T.SELECTION if selected else T.SURFACE_3
                self._round(3, y + 3, w - 4, y + 29, 8, fill, "")
                if selected:
                    self.create_rectangle(4, y + 9, 6, y + 23, fill=T.ACCENT, outline="")
            self.create_text(13, y + 16, text=row["text"], anchor="w",
                             fill="#ffffff" if selected else T.TEXT,
                             font=(T.FONT, 9))

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)
