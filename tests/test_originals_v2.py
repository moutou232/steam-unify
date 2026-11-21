from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steam_zh import originals
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class OriginalNameTests(unittest.TestCase):
    def test_chinese_current_without_cache_is_unknown(self):
        st = State({})
        g = GameInfo(570, "Dota 2 刀塔", "game")
        st.merge_games([g])
        rec = st.get_app(570)
        self.assertEqual(rec["orig_name"], "")
        self.assertTrue(rec["orig_unknown"])

    def test_english_current_becomes_original(self):
        st = State({})
        g = GameInfo(570, "Dota 2", "game")
        st.merge_games([g])
        rec = st.get_app(570)
        self.assertEqual(rec["orig_name"], "Dota 2")
        self.assertFalse(rec["orig_unknown"])

    def test_existing_original_is_preserved(self):
        st = State({})
        st.apps["570"] = {"orig_name": "Dota 2", "zh_name": "Dota 2 刀塔"}
        st.merge_games([GameInfo(570, "Dota 2 刀塔", "game")])
        self.assertEqual(st.get_app(570)["orig_name"], "Dota 2")

    def test_repair_marks_translated_original_unverified(self):
        st = State({})
        st.apps["570"] = {
            "orig_name": "Dota 2 刀塔",
            "zh_name": "Dota 2 刀塔",
            "source": "original",
            "locked": True,
            "applied": False,
        }
        g = GameInfo(570, "Dota 2 刀塔", "game")
        with mock.patch.object(st, "save"):
            res = originals.repair_state(st, [g], fetch=False)
        rec = st.get_app(570)
        self.assertEqual(rec["orig_name"], "Dota 2 刀塔")
        self.assertFalse(rec["orig_verified"])
        self.assertIn(570, res.pending)

    def test_apply_steam_english_name(self):
        st = State({})
        st.apps["570"] = {"orig_name": "", "zh_name": "Dota 2 刀塔", "orig_unknown": True}
        g = GameInfo(570, "Dota 2 刀塔", "game")
        rec = st.get_app(570)
        ok = originals._apply_result(rec, g, originals.OriginalNameResult(570, "Dota 2", "ok"))
        self.assertTrue(ok)
        self.assertEqual(rec["orig_name"], "Dota 2")
        self.assertFalse(rec["orig_unknown"])

    def test_steam_english_fetch_and_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            fake = originals.OriginalNameResult(570, "Dota 2", "ok")
            with mock.patch.object(originals, "CACHE_PATH", cache), \
                 mock.patch.object(originals, "_MEMORY_CACHE", None), \
                 mock.patch.object(originals, "_fetch_one", return_value=fake) as fetch:
                got = originals.fetch_store_originals([570], workers=1)
                self.assertEqual(got[570].name, "Dota 2")
                self.assertEqual(got[570].status, "ok")
                self.assertTrue(cache.is_file())
                again = originals.fetch_store_originals([570], workers=1)
                self.assertEqual(again[570].name, "Dota 2")
                self.assertEqual(fetch.call_count, 1)

    def test_steam_chinese_store_name_is_accepted(self):
        rec = {"orig_unknown": True}
        g = GameInfo(1, "当前中文", "game")
        ok = originals._apply_result(rec, g, originals.OriginalNameResult(1, "官方中文", "ok"))
        self.assertTrue(ok)
        self.assertEqual(rec["orig_name"], "官方中文")
        self.assertTrue(rec["orig_verified"])


if __name__ == "__main__":
    unittest.main()
