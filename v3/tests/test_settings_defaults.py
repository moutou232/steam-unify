from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steam_zh import state


class SettingsDefaultsTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "settings.json"
        self._patch = mock.patch.object(state, "SETTINGS_PATH", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_fresh_install_defaults(self):
        settings = state.State.load_settings()
        self.assertIs(settings["lock_appinfo"], True)
        self.assertEqual(settings["settings_rev"], state.SETTINGS_REV)
        self.assertIs(settings["first_run_done"], False)
        self.assertIs(settings["auto_repair_names"], True)

    def test_legacy_settings_unlock_flag_is_migrated(self):
        self._write({"lock_appinfo": False, "restart_steam": False})
        settings = state.State.load_settings()
        self.assertIs(settings["lock_appinfo"], True)
        self.assertEqual(settings["settings_rev"], state.SETTINGS_REV)
        self.assertIs(settings["restart_steam"], False)

    def test_explicit_choice_after_migration_is_respected(self):
        self._write({"lock_appinfo": False, "settings_rev": state.SETTINGS_REV})
        settings = state.State.load_settings()
        self.assertIs(settings["lock_appinfo"], False)

    def test_unknown_and_custom_fields_are_preserved(self):
        self._write({
            "steam_root": "D:/Games/Steam",
            "source_priority": ["steam_official"],
            "unknown_field": {"kept": True},
            "lock_appinfo": False,
        })
        settings = state.State.load_settings()
        self.assertEqual(settings["steam_root"], "D:/Games/Steam")
        self.assertEqual(settings["source_priority"], ["steam_official"])
        self.assertEqual(settings["unknown_field"], {"kept": True})

    def test_first_run_flag_is_never_forced(self):
        self._write({"first_run_done": True})
        self.assertIs(state.State.load_settings()["first_run_done"], True)
        self._write({"settings_rev": state.SETTINGS_REV})
        self.assertIs(state.State.load_settings()["first_run_done"], False)


if __name__ == "__main__":
    unittest.main()
