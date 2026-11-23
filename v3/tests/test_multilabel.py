from __future__ import annotations

import unittest
from unittest import mock

from steam_zh import category_batch, category_state, scoring
from steam_zh.metadata import GameInfo
from steam_zh.state import State


def game(appid, name, genres=None, tags=None, desc=""):
    return GameInfo(
        appid=appid, name=name, type="Game", genres=genres or [],
        tag_names=tags or [], tags=tags or [], description=desc,
    )


class MultiLabelTests(unittest.TestCase):
    def test_normalize_caps_and_dedupes(self):
        cats = ["动作", "策略", "动作", "肉鸽", "卡牌", "未知"]
        self.assertEqual(
            category_state.normalize_categories(cats),
            ["动作", "策略", "肉鸽"],
        )

    def test_legacy_single_category(self):
        rec = {"category": "策略"}
        self.assertEqual(category_state.get_categories(rec), ["策略"])

    def test_scoring_never_exceeds_three(self):
        g = game(
            1, "Test", [1, 2, 3, 25],
            ["动作", "射击", "角色扮演", "策略", "恐怖", "心理恐怖", "生存恐怖"],
        )
        res = scoring.classify(g)
        self.assertLessEqual(len(res.categories), 3)
        self.assertGreaterEqual(len(res.categories), 1)

    def test_horror_requires_multiple_tags(self):
        g = game(1, "Noise", [2], ["生存恐怖", "策略"])
        res = scoring.classify(g)
        self.assertNotIn("恐怖", res.categories)

    def test_batch_append_cap(self):
        st = State({"version": 2, "apps": {
            "1": {"categories": ["动作", "策略"], "category_locked": False},
        }, "settings": {}, "collections": {}})
        preview = category_batch.plan_batch(st, [1], ["肉鸽", "卡牌"], "append")
        self.assertEqual(preview.plans[0].new, ["动作", "策略", "肉鸽"])
        self.assertEqual(preview.plans[0].dropped, ["卡牌"])
        self.assertTrue(preview.plans[0].cap_truncated)

    def test_batch_replace_and_remove(self):
        st = State({"version": 2, "apps": {
            "1": {"categories": ["动作", "策略"], "category_locked": False},
        }, "settings": {}, "collections": {}})
        replace = category_batch.plan_batch(st, [1], ["肉鸽"], "replace")
        self.assertEqual(replace.plans[0].new, ["肉鸽"])
        remove = category_batch.plan_batch(st, [1], ["动作"], "remove")
        self.assertEqual(remove.plans[0].new, ["策略"])

    def test_batch_apply_is_capped(self):
        st = State({"version": 2, "apps": {
            "1": {"categories": ["动作"]},
        }, "settings": {}, "collections": {}})
        preview = category_batch.plan_batch(st, [1], ["策略", "肉鸽", "卡牌"], "append")
        with mock.patch.object(State, "save", return_value=None):
            snap = category_batch.apply_batch(st, preview)
        self.assertEqual(snap[1], ["动作"])
        self.assertEqual(category_state.get_categories(st.get_app(1)), ["动作", "策略", "肉鸽"])
        self.assertTrue(st.get_app(1).get("category_locked"))


if __name__ == "__main__":
    unittest.main()
