from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from steam_zh import service
from steam_zh.state import State


class UndoResetTests(unittest.TestCase):
    def make_ctx(self):
        st = State({
            "version": 2,
            "apps": {
                "1": {
                    "orig_name": "Original One",
                    "zh_name": "Original One",
                    "source": "original",
                    "locked": True,
                    "categories": [],
                    "category_locked": True,
                    "category_source": "undo",
                },
                "2": {
                    "orig_name": "Manual Game",
                    "zh_name": "手动译名",
                    "source": "manual",
                    "locked": True,
                    "categories": ["动作"],
                    "category_locked": True,
                    "category_source": "manual",
                },
            },
            "collections": {},
            "settings": {},
        })
        return SimpleNamespace(state=st, games=[])

    def test_explicit_translate_resets_only_undo_records(self):
        ctx = self.make_ctx()
        with mock.patch.object(State, "save", return_value=None):
            n = service.reset_undo_names(ctx)
        self.assertEqual(n, 1)
        rec = ctx.state.get_app(1)
        self.assertEqual(rec["zh_name"], "")
        self.assertFalse(rec["locked"])
        manual = ctx.state.get_app(2)
        self.assertEqual(manual["zh_name"], "手动译名")
        self.assertTrue(manual["locked"])

    def test_explicit_categorize_resets_only_undo_records(self):
        ctx = self.make_ctx()
        with mock.patch.object(State, "save", return_value=None):
            n = service.reset_undo_categories(ctx)
        self.assertEqual(n, 1)
        rec = ctx.state.get_app(1)
        self.assertFalse(rec["category_locked"])
        manual = ctx.state.get_app(2)
        self.assertTrue(manual["category_locked"])
        self.assertEqual(manual["categories"], ["动作"])


if __name__ == "__main__":
    unittest.main()
