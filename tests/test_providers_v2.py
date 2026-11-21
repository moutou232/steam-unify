from __future__ import annotations

import unittest

from steam_zh.metadata import GameInfo
from steam_zh.providers import _local_candidates, _make_candidate, fetch_name_candidates


class ProviderTests(unittest.TestCase):
    def test_english_game_with_official_chinese(self):
        g = GameInfo(413150, "Stardew Valley", "game",
                     localized={"schinese": "星露谷物语"})
        got = fetch_name_candidates(g, network=False)
        self.assertTrue(got.candidates)
        self.assertEqual(got.selected.source, "steam_official")
        self.assertEqual(got.selected.name, "星露谷物语")

    def test_english_name_is_not_a_chinese_candidate(self):
        g = GameInfo(1, "Some Game", "game")
        self.assertIsNone(_make_candidate("steam_official", g, "Some Game"))
        self.assertEqual(fetch_name_candidates(g, network=False).candidates, [])

    def test_dictionary_candidate(self):
        g = GameInfo(9999999, "No Such Game", "game")
        self.assertEqual(_local_candidates(g, {}), [])
        c = _make_candidate("dictionary", g, "测试译名")
        self.assertIsNotNone(c)
        self.assertEqual(c.source, "dictionary")


if __name__ == "__main__":
    unittest.main()
