from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from steam_zh import providers


def game(appid=1145360, name="Hades"):
    return SimpleNamespace(appid=appid, name=name, official_zh="")


class XiaoheiheFetchTests(unittest.TestCase):
    def test_detail_json_name(self):
        payload = json.dumps({"status": "ok", "result": {"name": "哈迪斯"}})
        with mock.patch.object(providers, "_text", return_value=(200, payload)) as text:
            result = providers._fetch_xiaoheihe(game(), None, 5.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate"]["name"], "哈迪斯")
        self.assertIn("get_game_detail", text.call_args.args[0])
        self.assertIn("appid=1145360", text.call_args.args[0])
        self.assertIn("_time=", text.call_args.args[0])

    def test_topic_detail_fallback_name(self):
        payload = json.dumps({
            "status": "ok",
            "result": {"topic_detail": {"name": "哈迪斯"}},
        })
        with mock.patch.object(providers, "_text", return_value=(200, payload)):
            result = providers._fetch_xiaoheihe(game(), None, 5.0)
        self.assertEqual(result["candidate"]["name"], "哈迪斯")

    def test_share_page_title_fallback(self):
        responses = [
            (200, '{"status":"failed","result":{}}'),
            (200, "<html><head><title>哈迪斯</title></head></html>"),
        ]
        with mock.patch.object(providers, "_text", side_effect=responses):
            result = providers._fetch_xiaoheihe(game(), None, 5.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate"]["name"], "哈迪斯")
        self.assertIn("get_game_detail_share", result["url"])

    def test_english_only_is_not_a_candidate(self):
        payload = json.dumps({"status": "ok", "result": {"name": "Hades"}})
        with mock.patch.object(providers, "_text", side_effect=[
            (200, payload),
            (200, "<title>Hades</title>"),
        ]):
            result = providers._fetch_xiaoheihe(game(), None, 5.0)
        self.assertIsNone(result["candidate"])
        self.assertIn(result["status"], ("no-cjk", "no-result"))

    def test_old_failed_cache_is_ignored(self):
        old = providers._MEMORY_CACHE
        try:
            providers._MEMORY_CACHE = {
                "1145360:name:xiaoheihe": {
                    "status": "failed",
                    "at": providers.time.time(),
                }
            }
            self.assertIsNone(providers._cache_get(1145360, "name", "xiaoheihe"))
        finally:
            providers._MEMORY_CACHE = old

    def test_default_source_order_puts_xiaoheihe_first(self):
        self.assertEqual(providers.DEFAULT_SOURCE_ORDER[0], "xiaoheihe")
        items = [
            providers.NameCandidate("steam_store", "商店名", 0.92),
            providers.NameCandidate("xiaoheihe", "黑盒名", 0.90),
        ]
        ordered = providers.order_name_candidates(items)
        self.assertEqual(ordered[0].source, "xiaoheihe")


if __name__ == "__main__":
    unittest.main()
