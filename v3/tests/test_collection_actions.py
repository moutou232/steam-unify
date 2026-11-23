from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steam_zh import collection_actions, collections, steamenv
from steam_zh.state import State


def ctx(root: Path):
    cloud = root / "userdata" / "1" / "config" / "cloudstorage"
    cloud.mkdir(parents=True)
    (cloud / "cloud-storage-namespace-1.json").write_text("[]", encoding="utf-8")
    (cloud / "cloud-storage-namespaces.json").write_text("[]", encoding="utf-8")
    env = steamenv.SteamEnv(root, "1")
    return type("Ctx", (), {"env": env, "state": State({}), "games": []})()


class CollectionActionTests(unittest.TestCase):
    def test_create_add_remove_rename_delete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c = ctx(root)
            with mock.patch.object(collection_actions.apply, "BACKUP_ROOT", root / "b"), \
                 mock.patch.object(collection_actions.steamenv, "steam_running", return_value=False), \
                 mock.patch.object(collection_actions.steamenv, "start_steam"):
                collection_actions.create_collection(c, "动作", [1, 2], restart=False)
                ns = collections.load_json_array(c.env.namespace_file)
                self.assertIn("动作", collections.existing_user_names(ns))
                collection_actions.add_games(c, "动作", [3], restart=False)
                collection_actions.remove_games(c, "动作", [1], restart=False)
                collection_actions.rename_collection(c, "动作", "动作游戏", restart=False)
                ns = collections.load_json_array(c.env.namespace_file)
                self.assertIn("动作游戏", collections.existing_user_names(ns))
                collection_actions.delete_collection(c, "动作游戏", restart=False)
                ns = collections.load_json_array(c.env.namespace_file)
                self.assertNotIn("动作游戏", collections.existing_user_names(ns))

    def test_duplicate_name_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c = ctx(root)
            with mock.patch.object(collection_actions.apply, "BACKUP_ROOT", root / "b"), \
                 mock.patch.object(collection_actions.steamenv, "steam_running", return_value=False), \
                 mock.patch.object(collection_actions.steamenv, "start_steam"):
                collection_actions.create_collection(c, "同名", [], restart=False)
                with self.assertRaises(collection_actions.ActionError):
                    collection_actions.create_collection(c, "同名", [], restart=False)


if __name__ == "__main__":
    unittest.main()
