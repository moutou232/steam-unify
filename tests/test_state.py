from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steam_zh import state as state_module
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class StateTests(unittest.TestCase):
    def test_merge_keeps_original_when_live_name_is_translation(self):
        st = State()
        st.set_app(
            1,
            orig_name="Stardew Valley",
            zh_name="星露谷物语",
            source="dictionary",
            applied=False,
        )
        g = GameInfo(appid=1, name="星露谷物语", type="Game")
        st.merge_games([g])
        self.assertEqual(st.get_app(1)["orig_name"], "Stardew Valley")
        self.assertEqual(st.get_app(1)["zh_name"], "星露谷物语")

    def test_merge_updates_original_when_steam_refreshes(self):
        st = State()
        st.set_app(2, orig_name="Old Name", zh_name="中文名", source="manual", applied=True)
        g = GameInfo(appid=2, name="New English Name", type="Game")
        st.merge_games([g])
        self.assertEqual(st.get_app(2)["orig_name"], "New English Name")

    def test_default_settings_show_all_known_and_xiaoheihe_first(self):
        self.assertTrue(state_module.DEFAULT_SETTINGS["all_known"])
        self.assertEqual(
            state_module.DEFAULT_SETTINGS["source_priority"][0], "xiaoheihe"
        )

    def test_old_default_source_priority_is_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({
                "source_priority": state_module.OLD_SOURCE_PRIORITY,
            }), encoding="utf-8")
            with mock.patch.object(state_module, "SETTINGS_PATH", path):
                loaded = State.load_settings()
            self.assertEqual(loaded["source_priority"][0], "xiaoheihe")

    def test_custom_source_priority_is_preserved(self):
        custom = ["3dm", "xiaoheihe", "steam_store"]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"source_priority": custom}), encoding="utf-8")
            with mock.patch.object(state_module, "SETTINGS_PATH", path):
                loaded = State.load_settings()
            self.assertEqual(loaded["source_priority"], custom)

    def test_explicit_all_known_false_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"all_known": False}), encoding="utf-8")
            with mock.patch.object(state_module, "SETTINGS_PATH", path):
                loaded = State.load_settings()
            self.assertFalse(loaded["all_known"])


if __name__ == "__main__":
    unittest.main()
