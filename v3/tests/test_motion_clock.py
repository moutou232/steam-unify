from __future__ import annotations

import tkinter as tk
import unittest

from steam_zh.ui_anim import MotionClock


class MotionClockTests(unittest.TestCase):
    def test_callback_can_start_next_animation(self):
        root = tk.Tk()
        root.withdraw()
        clock = MotionClock(root)
        seen: list[str] = []
        try:
            clock.animate(20, lambda t: None, lambda: (
                seen.append("covered"),
                clock.animate(20, lambda t: None, lambda: seen.append("revealed")),
            ))
            root.after(180, root.quit)
            root.mainloop()
        finally:
            clock.stop()
            root.destroy()
        self.assertEqual(seen, ["covered", "revealed"])


if __name__ == "__main__":
    unittest.main()
