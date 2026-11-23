from __future__ import annotations

import unittest
from unittest import mock

from steam_zh import originals
from steam_zh.state import State


class _Shards:
    appids = [1, 2, 3, 4]

    def get_name(self, appid):
        return {
            1: "中文一",
            2: "中文二",
            3: "中文三",
            4: "English",
        }.get(appid, "")


class _Cache:
    def require(self):
        return self

    def load_shards(self):
        return _Shards()


class UndoPreparationTests(unittest.TestCase):
    def test_tracked_untracked_and_natural(self):
        st = State({})
        st.apps["1"] = {"zh_name": "中文一", "orig_name": "", "source": "original"}
        names = {
            1: originals.OriginalNameResult(1, "官方一", "ok"),
            2: originals.OriginalNameResult(2, "English Two", "ok"),
            3: originals.OriginalNameResult(3, "中文三", "ok"),
        }
        with mock.patch.object(st, "save"), \
             mock.patch.object(originals.cache.AppInfoCache, "detect", return_value=_Cache()), \
             mock.patch.object(originals, "fetch_store_originals", return_value=names):
            res = originals.prepare_undo(object(), st)
        self.assertEqual(res.tracked_restorable, 1)
        self.assertEqual(res.untracked_restorable, 1)
        self.assertEqual(res.natural_skipped, 1)
        self.assertEqual(st.get_app(1)["orig_name"], "官方一")
        self.assertTrue(st.get_app(1)["orig_verified"])
        self.assertTrue(st.get_app(2)["cache_entry"])
        self.assertFalse(st.get_app(3).get("cache_entry", False))

    def test_failed_fetch_is_unknown(self):
        st = State({})
        st.apps["1"] = {"zh_name": "中文一", "orig_name": "", "source": "original"}
        names = {1: originals.OriginalNameResult(1, "", "not_found")}
        with mock.patch.object(st, "save"), \
             mock.patch.object(originals.cache.AppInfoCache, "detect", return_value=_Cache()), \
             mock.patch.object(originals, "fetch_store_originals", return_value=names):
            res = originals.prepare_undo(object(), st)
        self.assertEqual(res.unknown_skipped, 1)
        self.assertFalse(st.get_app(1)["orig_verified"])


if __name__ == "__main__":
    unittest.main()
