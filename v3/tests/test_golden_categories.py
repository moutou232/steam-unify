from __future__ import annotations

import unittest

from steam_zh import scoring
from steam_zh.metadata import GameInfo


def g(appid, name, genres, tags, desc=""):
    return GameInfo(
        appid=appid, name=name, type="Game", genres=genres,
        tags=tags, tag_names=tags, description=desc,
    )


class GoldenCategoryTests(unittest.TestCase):
    CASES = [
        (413150, "Stardew Valley", [3, 23, 28], ["农场模拟", "生活模拟", "角色扮演"], "模拟经营"),
        (646570, "Slay the Spire", [2, 23], ["卡牌游戏", "类 Rogue", "牌组构建"], "卡牌"),
        (578080, "PUBG", [1], ["第一人称射击", "射击", "动作"], "射击"),
        (414700, "Outlast 2", [1, 25], ["恐怖", "心理恐怖", "生存恐怖"], "恐怖"),
        (613100, "House Flipper", [1, 28], ["设计与插画", "管理", "经济"], "模拟经营"),
        (105600, "Terraria", [1, 3, 25], ["沙盒", "生存", "开放世界生存制作"], "沙盒生存"),
        (282800, "100% Orange Juice", [2, 23], ["策略", "卡牌游戏"], "策略"),
        (2358720, "Black Myth: Wukong", [1, 3], ["动作", "角色扮演", "类魂系列"], "动作"),
    ]

    def test_primary_expectations(self):
        for appid, name, genres, tags, expected in self.CASES:
            with self.subTest(name=name):
                res = scoring.classify(g(appid, name, genres, tags))
                self.assertLessEqual(len(res.categories), 3)
                self.assertIn(expected, res.categories, res.categories)

    def test_description_keywords(self):
        res = scoring.classify(
            g(1, "Mystery", [], [], "A roguelike deckbuilder with permanent death")
        )
        self.assertIn("肉鸽", res.categories)

    def test_never_more_than_three(self):
        res = scoring.classify(g(
            2, "Stacked", [1, 2, 3, 25, 28],
            ["动作", "射击", "角色扮演", "策略", "恐怖", "心理恐怖", "生存恐怖", "解谜", "卡牌游戏"],
        ))
        self.assertLessEqual(len(res.categories), 3)


if __name__ == "__main__":
    unittest.main()
