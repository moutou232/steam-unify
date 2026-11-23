from __future__ import annotations

import unittest

from steam_zh import ui_anim


class MotionMathTests(unittest.TestCase):
    def test_ease_endpoints(self):
        self.assertEqual(ui_anim.ease_out_cubic(0), 0.0)
        self.assertEqual(ui_anim.ease_out_cubic(1), 1.0)

    def test_color_interpolation(self):
        self.assertEqual(ui_anim.lerp_color("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(ui_anim.lerp_color("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(ui_anim.lerp_color("#ff0000", "#00ff00", 0.5), "#808000")


if __name__ == "__main__":
    unittest.main()
