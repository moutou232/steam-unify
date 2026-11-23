"""Lightweight premium UI components built on Tkinter."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import ui_theme as T


def card(parent, *, padding: int = T.SPACE_4, bg: str = T.SURFACE):
    """A flat card with a hairline border and internal padding."""
    outer = tk.Frame(parent, bg=bg, highlightthickness=1,
                     highlightbackground=T.BORDER, bd=0)
    inner = tk.Frame(outer, bg=bg)
    inner.pack(fill="both", expand=True, padx=padding, pady=padding)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


class Toast(tk.Toplevel):
    """Bottom-right transient notification with a soft slide/fade."""

    def __init__(self, app, text: str, kind: str = "success", duration: int = 2200):
        super().__init__(app.root)
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        color = {"success": T.SUCCESS, "error": T.DANGER,
                 "warning": T.WARNING, "info": T.ACCENT}.get(kind, T.ACCENT)
        mark = {"success": "✓", "error": "!", "warning": "!", "info": "i"}.get(kind, "•")
        frame = tk.Frame(self, bg=T.SURFACE_2, highlightthickness=1,
                         highlightbackground=T.BORDER_2)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=mark, bg=T.SURFACE_2, fg=color,
                 font=(T.FONT, 12, "bold")).pack(side="left", padx=(12, 6), pady=10)
        tk.Label(frame, text=text, bg=T.SURFACE_2, fg=T.TEXT,
                 font=(T.FONT, 9), wraplength=360, justify="left").pack(
            side="left", padx=(0, 14), pady=10)
        self.update_idletasks()
        width = max(240, self.winfo_reqwidth())
        height = max(40, self.winfo_reqheight())
        root = app.root
        x = root.winfo_rootx() + root.winfo_width() - width - 24
        y = root.winfo_rooty() + root.winfo_height() - height - 56
        self.geometry(f"{width}x{height}+{x}+{y + 18}")
        self.after(10, lambda: self._animate(x, y, 0.0))
        self.after(duration, self.destroy)

    def _animate(self, x: int, y: int, alpha: float) -> None:
        if not self.winfo_exists():
            return
        alpha = min(0.96, alpha + 0.16)
        try:
            self.attributes("-alpha", alpha)
            current_y = self.winfo_y()
        except Exception:
            return
        self.geometry(f"+{x}+{max(y, current_y - 3)}")
        if alpha < 0.96:
            self.after(16, lambda: self._animate(x, y, alpha))


class MotionButton(tk.Canvas):
    """Rounded button with smooth hover/press colours and disabled state."""

    KINDS = {
        "primary": (T.PRIMARY, T.PRIMARY_HOVER, T.PRIMARY_PRESSED, "#ffffff"),
        "secondary": (T.SURFACE_2, T.SURFACE_3, "#263646", T.TEXT),
        "ghost": (T.BG, T.SURFACE_2, T.SURFACE_3, T.MUTED),
        "danger": ("#40232a", "#5a2b34", "#351c22", T.DANGER),
    }

    def __init__(self, parent, text: str, command=None, kind: str = "secondary",
                 width: int | None = None, height: int = 34):
        self._font = tkfont.Font(family=T.FONT, size=9)
        if width is None:
            width = self._font.measure(text) + 28
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self._focus = False
        self._text = text
        self._command = command
        self._kind = kind if kind in self.KINDS else "secondary"
        self._state = "normal"
        self._hover = 0.0
        self._pressed = False
        self._active = 0.0
        self._anim = None
        self._active_anim = None
        self._width = width
        self._height = height
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<Return>", lambda _e: self._invoke())
        self.bind("<space>", lambda _e: self._invoke())
        self._draw()

    def set_active(self, active: bool, duration: int = 180) -> None:
        """Animate the selected-tab highlight instead of swapping styles."""
        target = 1.0 if active else 0.0
        if abs(self._active - target) < 0.01 and self._active_anim is None:
            return
        if self._active_anim:
            try:
                self.after_cancel(self._active_anim)
            except Exception:
                pass
        start = self._active
        steps = max(1, int(duration / 16))

        def step(i=0):
            raw = min(1.0, i / steps)
            eased = 1 - (1 - raw) ** 3
            self._active = start + (target - start) * eased
            self._draw()
            if i < steps:
                self._active_anim = self.after(16, step, i + 1)
            else:
                self._active = target
                self._active_anim = None
                self._draw()
        step()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        state = kwargs.pop("state", None)
        style = kwargs.pop("style", None)
        if text is not None:
            self._text = str(text)
            self._width = self._font.measure(self._text) + 28
            self.configure(width=self._width)
        if state is not None:
            self._state = str(state)
        if style is not None:
            self._kind = "primary" if "Accent" in style or "Primary" in style else (
                "ghost" if "Small" in style else "secondary")
        if kwargs:
            return super().configure(**kwargs)
        self._draw()
        return None

    config = configure

    def _target(self):
        return 0.0 if self._state == "disabled" else 1.0 if self._hover else 0.0

    def _enter(self, _e):
        if self._state == "disabled":
            return
        self._animate_hover(1.0)

    def _leave(self, _e):
        self._pressed = False
        self._animate_hover(0.0)

    def _press(self, _e):
        if self._state == "disabled":
            return
        self._pressed = True
        self._draw()

    def _release(self, _e):
        if self._state == "disabled" or not self._pressed:
            return
        self._pressed = False
        self._draw()
        if self._command:
            try:
                self._command()
            except Exception:
                pass

    def _animate_hover(self, target: float):
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
        start = self._hover
        steps = 8

        def step(i=0):
            raw = min(1.0, i / steps)
            eased = 1 - (1 - raw) ** 3
            self._hover = start + (target - start) * eased
            self._draw()
            if i < steps:
                self._anim = self.after(16, step, i + 1)
            else:
                self._anim = None
        step()

    def _set_focus(self, value: bool):
        self._focus = value
        self._draw()

    def _invoke(self):
        if self._state != "disabled" and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        bg, hover, press, fg = self.KINDS[self._kind]
        color = _lerp(bg, press if self._pressed else hover, self._hover)
        edge = T.BORDER if self._kind == "secondary" else color
        if self._active > 0 and self._kind != "primary":
            t = max(0.0, min(1.0, self._active))
            color = _lerp(color, T.PRIMARY, t)
            edge = _lerp(edge, T.ACCENT, t)
            fg = _lerp(fg, "#ffffff", t)
        if self._state == "disabled":
            color = "#151d27"
            edge = color
            fg = T.DIM
        self._round(1, 2 if self._pressed else 1, self._width - 1,
                    self._height - 2 if self._pressed else self._height - 1,
                    8, color, edge)
        if self._active > 0:
            line = _lerp(color, "#a8ddff", max(0.0, min(1.0, self._active)))
            self.create_line(10, self._height - 3, self._width - 10,
                             self._height - 3, fill=line, width=2)
        self.create_text(self._width / 2, self._height / 2 + (1 if self._pressed else 0),
                         text=self._text, fill=fg,
                         font=(T.FONT, 9, "bold" if self._kind == "primary" or self._active > 0.5 else "normal"))
        if self._focus:
            self._round(0, 0, self._width, self._height, 9, "", T.ACCENT)

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)


class RoundedSurface(tk.Frame):
    """A rounded card that hosts a normal Frame as its content."""

    def __init__(self, parent, *, radius: int = 14, padding: int = 12,
                 bg: str = T.SURFACE, border: str = T.BORDER,
                 width: int | None = None, height: int | None = None):
        outer_bg = T.BG
        super().__init__(parent, bg=outer_bg, width=width or 1, height=height or 1,
                         highlightthickness=0, bd=0)
        self._radius = radius
        self._padding = padding
        self._bg = bg
        self._border = border
        self.canvas = tk.Canvas(self, bg=outer_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(padding, padding, anchor="nw",
                                                 window=self.inner)
        self.canvas.bind("<Configure>", self._redraw)
        self.inner.bind("<Configure>", lambda _e: self._redraw())

    def fit_content(self, min_width: int = 520, min_height: int = 360) -> tuple[int, int]:
        """Size a standalone dialog card from its embedded content."""
        self.inner.update_idletasks()
        w = max(min_width, self.inner.winfo_reqwidth() + 2 * self._padding + 4)
        h = max(min_height, self.inner.winfo_reqheight() + 2 * self._padding + 4)
        self.configure(width=w, height=h)
        self.pack_propagate(False)
        self.update_idletasks()
        self.canvas.event_generate("<Configure>")
        return w, h

    def _redraw(self, _event=None):
        self.canvas.delete("card")
        w = max(2, self.canvas.winfo_width())
        h = max(2, self.canvas.winfo_height())
        r = self._radius
        self._round(0, 2, w - 1, h - 1, r, "#090e14", "", "card")
        self._round(1, 1, w - 2, h - 2, r, self._bg, self._border, "card")
        p = self._padding
        self.canvas.coords(self._window, p, p)
        self.canvas.itemconfigure(self._window, width=max(1, w - 2 * p),
                                  height=max(1, h - 2 * p))

    def _round(self, x1, y1, x2, y2, r, fill, outline, tag):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.canvas.create_polygon(points, smooth=True, fill=fill,
                                   outline=outline, width=1, tags=tag)


class RoundedField(RoundedSurface):
    """Fixed-size rounded shell for Entry/Combobox-like widgets."""

    def __init__(self, parent, *, width=180, height=32, radius=8, padding=2,
                 bg=T.SURFACE_2):
        super().__init__(parent, width=width, height=height, radius=radius,
                         padding=padding, bg=bg)
        self.pack_propagate(False)
        self.inner.configure(height=max(1, height - 2 * padding))
        self.inner.pack_propagate(False)


class TabBar(tk.Canvas):
    """Three workspace tabs with one independent sliding capsule."""

    def __init__(self, parent, items, command, height: int = 38):
        super().__init__(parent, height=height, bg=T.BG, highlightthickness=0, bd=0,
                         takefocus=True)
        self._focus = False
        self.items = list(items)
        self.command = command
        self._font = tkfont.Font(family=T.FONT, size=10, weight="bold")
        self._tab_width = 0
        self._active = self.items[0][0] if self.items else ""
        self._x = 0.0
        self._tab_w = 0.0
        self._target_x = 0
        self._target_w = 0
        self._old_active = self._active
        self._text_mix = 1.0
        self._anim = None
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", self._layout)
        self.bind("<Left>", lambda _e: self._key_move(-1))
        self.bind("<Right>", lambda _e: self._key_move(1))
        self.bind("<Return>", lambda _e: self.command(self._active))
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.after_idle(self._layout)

    def _positions(self):
        out = []
        x = 0
        for key, label in self.items:
            w = self._font.measure(label) + 34
            out.append((key, label, x, w))
            x += w + 6
        return out

    def _layout(self, _event=None):
        self._tab_width = max(1, self.winfo_width())
        pos = self._positions()
        for key, label, x, w in pos:
            if key == self._active:
                self._target_x, self._target_w = x, w
                if self._anim is None:
                    self._x, self._tab_w = float(x), float(w)
        self.delete("all")
        self._draw()

    def _set_focus(self, value):
        self._focus = value
        self._draw()

    def _key_move(self, delta):
        keys = [k for k, _ in self.items]
        if self._active not in keys:
            return
        idx = (keys.index(self._active) + delta) % len(keys)
        self.command(keys[idx])

    def _click(self, event):
        for key, label, x, w in self._positions():
            if x <= event.x <= x + w:
                self.command(key)
                return

    def set_active(self, key: str, duration: int = 260):
        if key == self._active and self._anim is None:
            return
        self._old_active = self._active
        self._active = key
        self._text_mix = 0.0
        pos = self._positions()
        for k, label, x, w in pos:
            if k == key:
                self._target_x, self._target_w = x, w
                break
        if self._x == 0 and self._tab_w == 0:
            self._x, self._tab_w = float(self._target_x), float(self._target_w)
            self._draw()
            return
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
        sx, sw = self._x, self._tab_w
        steps = max(1, int(duration / 16))

        def step(i=0):
            raw = min(1.0, i / steps)
            eased = 1 - (1 - raw) ** 3
            self._x = sx + (self._target_x - sx) * eased
            self._tab_w = sw + (self._target_w - sw) * eased
            self._text_mix = eased
            self._draw()
            if i < steps:
                self._anim = self.after(16, step, i + 1)
            else:
                self._anim = None
                self._x, self._tab_w = float(self._target_x), float(self._target_w)
                self._text_mix = 1.0
                self._draw()
        step()

    def _draw(self):
        self.delete("all")
        self.create_rectangle(0, 0, self._tab_width, self.winfo_height(), fill=T.BG, outline="")
        # capsule first, labels above it
        if self._tab_w > 0:
            self._round(self._x, 4, self._x + self._tab_w, 34, 9,
                        T.PRIMARY, T.ACCENT)
        if self._focus:
            self._round(0, 0, sum(self._font.measure(l) + 40 for _, l in self.items),
                        36, 9, "", T.ACCENT)
        for key, label, x, w in self._positions():
            if key == self._active:
                color = _lerp(T.MUTED, "#ffffff", self._text_mix)
            elif key == self._old_active:
                color = _lerp("#ffffff", T.MUTED, self._text_mix)
            else:
                color = T.MUTED
            active = key == self._active and self._text_mix > 0.5
            self.create_text(x + w / 2, 19, text=label, fill=color,
                             font=(T.FONT, 10, "bold" if active else "normal"))

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)


class AnimatedPopup(tk.Toplevel):
    """Rounded, animated popup with exact row geometry and scrolling."""

    def __init__(self, app, anchor, items, *, on_pick=None, width: int = 200):
        root = getattr(app, "root", app)
        old = getattr(root, "_dsh_active_popup", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        super().__init__(root)
        self.app = app if hasattr(app, "root") else type("AppRef", (), {"root": root})()
        self.anchor = anchor
        self.items = list(items)
        self.on_pick = on_pick
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.configure(bg="#010203")
        try:
            self.attributes("-transparentcolor", "#010203")
        except Exception:
            pass
        self.width = width
        self.row_h = 31
        self.sep_h = 9
        self.rows: list[dict] = []
        self._scroll = 0
        self._active_row = -1
        self._content_h = 8
        for item in self.items:
            self._content_h += self.sep_h if item is None else self.row_h
        self.full_h = min(330, self._content_h)
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if x + width > sw - 8:
            x = max(8, sw - width - 8)
        if y + self.full_h > sh - 8:
            y = max(8, anchor.winfo_rooty() - self.full_h - 4)
        self._x, self._y = x, y
        self.geometry(f"{width}x1+{x}+{y}")
        self.canvas = tk.Canvas(self, bg="#010203", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._close_id = None
        self._closed = False
        self._bindings: list[tuple[str, str]] = []
        root._dsh_active_popup = self
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Up>", lambda _e: self._key_move(-1))
        self.bind("<Down>", lambda _e: self._key_move(1))
        self.bind("<Return>", lambda _e: self._pick_active())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._pick)
        self.canvas.bind("<Motion>", self._motion)
        self.bind("<MouseWheel>", self._on_wheel)
        try:
            self.bind("<FocusOut>", lambda _e: self.after(60, self._check_focus))
        except Exception:
            pass
        self._render()
        self._animate_open()
        self._bind_outside()

    def _max_scroll(self) -> int:
        return max(0, self._content_h - self.full_h)

    def _render(self):
        self.canvas.delete("all")
        self._round(self.canvas, 1, 1, self.width - 1, self.full_h - 1, 12,
                    T.SURFACE_2, T.BORDER_2, "panel")
        self.rows = []
        y = 4
        for item in self.items:
            if item is None:
                self.canvas.create_line(8, y - self._scroll + 4,
                                        self.width - 8, y - self._scroll + 4,
                                        fill=T.BORDER)
                self.rows.append({"y": y, "h": self.sep_h, "item": None})
                y += self.sep_h
                continue
            label, value, command = item
            disabled = bool(item[3]) if len(item) > 3 else False
            color = T.DIM if disabled else T.TEXT
            self.canvas.create_text(14, y - self._scroll + self.row_h / 2,
                                    text=label, fill=color, anchor="w",
                                    font=(T.FONT, 9), tags="rowtext")
            self.rows.append({"y": y, "h": self.row_h, "item": item,
                              "disabled": disabled})
            y += self.row_h
        self._draw_scrollbar()

    def _draw_scrollbar(self):
        if self._content_h <= self.full_h:
            return
        span = self.full_h / self._content_h
        thumb_h = max(24, int(self.full_h * span))
        top = 4 + int((self.full_h - 8 - thumb_h) * (self._scroll / max(1, self._max_scroll())))
        self.canvas.create_rectangle(self.width - 7, 4, self.width - 4,
                                     self.full_h - 4, fill="#111923", outline="")
        self._round(self.canvas, self.width - 8, top, self.width - 3,
                    top + thumb_h, 2, "#465b70", "", "scrollthumb")

    def _row_at(self, y):
        for row in self.rows:
            if row["y"] <= y < row["y"] + row["h"]:
                return row
        return None

    def _on_wheel(self, event):
        if self._content_h <= self.full_h:
            return "break"
        direction = -1 if event.delta > 0 else 1
        self._scroll = max(0, min(self._max_scroll(),
                                  self._scroll + direction * self.row_h))
        self._render()
        return "break"

    def _key_move(self, delta):
        candidates = [i for i, r in enumerate(self.rows) if r.get("item") and not r.get("disabled")]
        if not candidates:
            return
        if self._active_row not in candidates:
            self._active_row = candidates[0]
        else:
            idx = candidates.index(self._active_row)
            self._active_row = candidates[(idx + delta) % len(candidates)]
        row = self.rows[self._active_row]
        if row["y"] < self._scroll + 4:
            self._scroll = max(0, row["y"] - 4)
        elif row["y"] + row["h"] > self._scroll + self.full_h - 4:
            self._scroll = min(self._max_scroll(),
                               row["y"] + row["h"] - self.full_h + 4)
        self._render()
        self.canvas.delete("keyfocus")
        self._round(self.canvas, 6, row["y"] - self._scroll + 1,
                    self.width - 10, row["y"] - self._scroll + row["h"] - 1,
                    7, "", T.ACCENT, "keyfocus")

    def _pick_active(self):
        if 0 <= self._active_row < len(self.rows):
            self._pick_row(self.rows[self._active_row])

    def _pick_row(self, row):
        if not row or not row.get("item") or row.get("disabled"):
            return
        label, value, command = row["item"][:3]
        self.close()
        if command:
            command()
        elif self.on_pick:
            self.on_pick(value)

    def _motion(self, event):
        self.canvas.delete("hover")
        row = self._row_at(event.y + self._scroll)
        if not row or not row.get("item") or row.get("disabled"):
            return
        y, h = row["y"] - self._scroll, row["h"]
        self._round(self.canvas, 6, y + 1, self.width - 6, y + h - 1,
                    7, T.SURFACE_3, "", "hover")
        self.canvas.tag_raise("rowtext", "hover")
        self.canvas.tag_raise("scrollthumb", "hover")

    def _pick(self, event):
        self._pick_row(self._row_at(event.y + self._scroll))

    def _animate_open(self):
        start_h = 1
        steps = 10

        def step(i=0):
            t = min(1.0, i / steps)
            h = int(start_h + (self.full_h - start_h) * (1 - (1 - t) ** 3))
            try:
                self.geometry(f"{self.width}x{max(1, h)}+{self._x}+{self._y}")
                self.attributes("-alpha", min(0.98, t * 0.98))
            except Exception:
                pass
            if i < steps:
                self.after(16, step, i + 1)
        step()

    def _bind_outside(self):
        root = self.app.root

        def outside(event):
            if self._closed:
                return
            try:
                if event.widget is self or str(event.widget).startswith(str(self)):
                    return
                if event.widget is self.anchor:
                    return
            except Exception:
                pass
            self.close()

        def deactivate(_event=None):
            self.after(60, self._check_focus)

        for seq, func in (("<Button-1>", outside), ("<FocusOut>", deactivate),
                          ("<Unmap>", deactivate)):
            try:
                fid = root.bind(seq, func, add="+")
                if fid:
                    self._bindings.append((seq, fid))
            except Exception:
                pass

    def _check_focus(self):
        if self._closed or not self.winfo_exists():
            return
        try:
            focus = self.app.root.focus_get()
        except Exception:
            focus = None
        if focus is not None and str(focus).startswith(str(self)):
            return
        self.close()

    def close(self):
        if self._closed or not self.winfo_exists():
            return
        self._closed = True
        root = self.app.root
        if getattr(root, "_dsh_active_popup", None) is self:
            root._dsh_active_popup = None
        for seq, fid in self._bindings:
            try:
                root.unbind(seq, fid)
            except Exception:
                pass
        self._bindings.clear()
        start_h = self.winfo_height()
        steps = 7

        def step(i=0):
            t = min(1.0, i / steps)
            h = int(start_h * (1 - t))
            try:
                self.geometry(f"{self.width}x{max(1, h)}+{self._x}+{self._y}")
                self.attributes("-alpha", 0.98 * (1 - t))
            except Exception:
                pass
            if i < steps:
                self.after(16, step, i + 1)
            else:
                try:
                    self.destroy()
                except Exception:
                    pass
        step()

    @staticmethod
    def _round(canvas, x1, y1, x2, y2, r, fill, outline, tag):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        canvas.create_polygon(points, smooth=True, fill=fill, outline=outline,
                              width=1, tags=tag)


class AnimatedSelect(tk.Canvas):
    """A rounded combobox replacement with an animated popup."""

    def __init__(self, parent, variable: tk.StringVar, values, command=None,
                 width: int = 150):
        super().__init__(parent, width=width, height=32, bg=T.BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.variable = variable
        self.values = list(values)
        self.command = command
        self._app = parent.winfo_toplevel()
        self._tab_w = width
        self._hover = False
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        try:
            self.variable.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        values = kwargs.pop("values", None)
        if values is not None:
            self.values = list(values)
            self._draw()
        if kwargs:
            return super().configure(**kwargs)
        return None

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _draw(self):
        self.delete("all")
        fill = T.SURFACE_3 if self._hover else T.SURFACE_2
        self._round(1, 1, self._tab_w - 1, 31, 8, fill, T.BORDER_2)
        self.create_text(10, 16, text=str(self.variable.get()), anchor="w",
                         fill=T.TEXT, font=(T.FONT, 9))
        self.create_text(self._tab_w - 13, 16, text="⌄", fill=T.MUTED,
                         font=(T.FONT, 11))

    def _open(self, _event=None):
        app = self._app
        items = [(v, v, None) for v in self.values]
        popup = AnimatedPopup(app, self, items, on_pick=self._pick,
                              width=max(140, self._tab_w))
        self._popup = popup

    def _pick(self, value):
        self.variable.set(value)
        if self.command:
            self.command()

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)


class AnimatedMenuButton(tk.Canvas):
    """Rounded menu button with animated popup."""

    def __init__(self, parent, text: str, items, width: int = 116):
        super().__init__(parent, width=width, height=34, bg=T.BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text = text
        self.items = items
        self._app = parent.winfo_toplevel()
        self._tab_w = width
        self._hover = False
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _draw(self):
        self.delete("all")
        fill = T.SURFACE_3 if self._hover else T.SURFACE_2
        self._round(1, 1, self._tab_w - 1, 33, 8, fill, T.BORDER_2)
        self.create_text(10, 17, text=self.text, anchor="w", fill=T.TEXT,
                         font=(T.FONT, 9))
        self.create_text(self._tab_w - 13, 17, text="⌄", fill=T.MUTED,
                         font=(T.FONT, 11))

    def _open(self, _event=None):
        popup = AnimatedPopup(self._app, self, self.items,
                              width=max(190, self._tab_w))
        self._popup = popup

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)


class ToggleSwitch(tk.Canvas):
    """Small animated on/off switch bound to a BooleanVar."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None,
                 width: int = 40, height: int = 22):
        super().__init__(parent, width=width, height=height, bg=T.BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.variable = variable
        self.command = command
        self._sw = width
        self._sh = height
        self._t = 1.0 if variable.get() else 0.0
        self._anim = None
        self.bind("<Button-1>", self._toggle)
        try:
            self.variable.trace_add("write", lambda *_: self._sync())
        except Exception:
            pass
        self._draw()

    def _toggle(self, _event=None):
        self.variable.set(not bool(self.variable.get()))
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def _sync(self):
        target = 1.0 if self.variable.get() else 0.0
        start = self._t
        self._animate(start, target)

    def _animate(self, start: float, target: float):
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
        steps = 10

        def step(i=0):
            raw = min(1.0, i / steps)
            eased = 1 - (1 - raw) ** 3
            self._t = start + (target - start) * eased
            self._draw()
            if i < steps:
                self._anim = self.after(16, step, i + 1)
            else:
                self._anim = None
        step()

    def _draw(self):
        self.delete("all")
        w, h = self._sw, self._sh
        r = h / 2
        t = max(0.0, min(1.0, self._t))
        color = _lerp("#2a3746", T.PRIMARY, t)
        self._round(1, 1, w - 1, h - 1, r, color, "")
        kx = 3 + (w - h) * t
        self._round(kx, 3, kx + h - 6, h - 3, (h - 6) / 2, "#e8f2fa", "")

    def _round(self, x1, y1, x2, y2, r, fill, outline):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.create_polygon(points, smooth=True, fill=fill, outline=outline, width=1)


def _lerp(a: str, b: str, t: float) -> str:
    def rgb(value: str):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    ar, ag, ab = rgb(a)
    br, bg, bb = rgb(b)
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % (round(ar + (br - ar) * t),
                              round(ag + (bg - ag) * t),
                              round(ab + (bb - ab) * t))


def toast(app, text: str, kind: str = "success") -> None:
    try:
        Toast(app, text, kind)
    except Exception:
        pass
