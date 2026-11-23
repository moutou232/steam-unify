"""Single-clock animation primitives for Tkinter."""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable


MOTION_MODE = "full"


def set_motion_mode(mode: str) -> None:
    global MOTION_MODE
    MOTION_MODE = mode if mode in ("full", "reduced", "off") else "full"


def duration(ms: int) -> int:
    if MOTION_MODE == "off":
        return 0
    if MOTION_MODE == "reduced":
        return min(90, max(0, int(ms)))
    return max(0, int(ms))


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_out_quint(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 5


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * clamp(t)


def lerp_color(a: str, b: str, t: float) -> str:
    def rgb(value: str):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    ar, ag, ab = rgb(a)
    br, bg, bb = rgb(b)
    t = clamp(t)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


@dataclass
class _Tween:
    duration: float
    delay: float
    update: Callable[[float], None]
    done: Callable[[], None] | None
    started: float
    cancelled: bool = False


class MotionClock:
    """One 60fps clock for every animation in a window."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self._tweens: list[_Tween] = []
        self._tick_id: str | None = None
        self._last = time.perf_counter()

    def animate(self, ms: int, update: Callable[[float], None],
                done: Callable[[], None] | None = None,
                delay: int = 0) -> _Tween:
        tween = _Tween(
            duration=max(1, duration(ms)) / 1000.0,
            delay=max(0, delay) / 1000.0,
            update=update,
            done=done,
            started=time.perf_counter(),
        )
        # A newer animation on the same visual target replaces older work.
        self._tweens = [t for t in self._tweens if not t.cancelled]
        self._tweens.append(tween)
        self._ensure_tick()
        return tween

    def cancel(self, tween: _Tween | None) -> None:
        if tween:
            tween.cancelled = True

    def stop(self) -> None:
        self._tweens.clear()
        if self._tick_id:
            try:
                self.root.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _ensure_tick(self) -> None:
        if self._tick_id is None:
            self._last = time.perf_counter()
            self._tick_id = self.root.after(16, self._tick)

    def _tick(self) -> None:
        self._tick_id = None
        now = time.perf_counter()
        # Swap the list first.  Callbacks are allowed to start the next tween
        # (for example: cover -> switch workspace -> reveal) and those new
        # tweens must survive this frame.
        running, self._tweens = self._tweens, []
        for tween in running:
            if tween.cancelled:
                continue
            elapsed = now - tween.started
            if elapsed < tween.delay:
                self._tweens.append(tween)
                continue
            raw = (elapsed - tween.delay) / tween.duration
            if raw >= 1.0:
                try:
                    tween.update(1.0)
                except Exception:
                    pass
                if tween.done:
                    try:
                        tween.done()
                    except Exception:
                        pass
            else:
                try:
                    tween.update(ease_out_quint(raw))
                except Exception:
                    pass
                self._tweens.append(tween)
        if self._tweens:
            self._tick_id = self.root.after(16, self._tick)


class Animator:
    """Compatibility wrapper around MotionClock."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.clock = MotionClock(root)
        self._current: _Tween | None = None

    def cancel(self) -> None:
        self.clock.cancel(self._current)
        self._current = None

    def animate(self, duration: int, update: Callable[[float], None],
                done: Callable[[], None] | None = None) -> None:
        self.clock.cancel(self._current)
        self._current = self.clock.animate(duration, update, done)
