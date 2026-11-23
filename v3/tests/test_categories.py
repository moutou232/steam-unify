from __future__ import annotations

import unittest

from steam_zh import categories
from steam_zh.metadata import GameInfo


def game(appid, name, genres=None, tags=None):
    return GameInfo(
        appid=appid,
        name=name,
        type="Game",
        genres=genres or [],
        tags=tags or [],
        tag_names=tags or [],
    )


class CategoryTests(unittest.TestCase):
    def test_visual_novel(self):
        g = game(1, "CLANNAD", [25], ["视觉小说", "动漫"])
        self.assertEqual(categories.category_for(g), "视觉小说")

    def test_simulation(self):
        g = game(413150, "Stardew Valley", [3, 23, 28], ["农场模拟", "生活模拟"])
        self.assertEqual(categories.category_for(g), "模拟经营")

    def test_sandbox_survival(self):
        g = game(105600, "Terraria", [1, 3, 25], ["沙盒", "生存", "开放世界生存制作"])
        self.assertEqual(categories.category_for(g), "沙盒生存")

    def test_noisy_single_horror_tag_does_not_win(self):
        g = game(282800, "100% Orange Juice", [2, 23], ["策略", "卡牌游戏", "生存恐怖"])
        self.assertEqual(categories.category_for(g), "策略")

    def test_horror_requires_multiple_tags(self):
        g = game(414700, "Outlast 2", [1, 25], ["恐怖", "心理恐怖", "生存恐怖"])
        self.assertEqual(categories.category_for(g), "恐怖")

    def test_racing_beats_car_simulation(self):
        g = game(618140, "Barro", [9, 4, 23], ["汽车模拟", "竞速", "驾驶"])
        self.assertEqual(categories.category_for(g), "竞速")

    def test_shooter(self):
        g = game(578080, "PUBG", [1], ["第一人称射击", "射击"])
        self.assertEqual(categories.category_for(g), "射击")

    def test_fallback(self):
        g = game(1, "Mystery", [], [])
        self.assertEqual(categories.category_for(g), "其他")


if __name__ == "__main__":
    unittest.main()
