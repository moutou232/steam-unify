from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from steam_zh import collection_plan, collections, steamenv
from steam_zh.state import State


def make_ctx(root: Path):
    cloud = root / "userdata" / "123" / "config" / "cloudstorage"
    cloud.mkdir(parents=True)
    ns = [
        ["user-collections.uc-1", {
            "key": "user-collections.uc-1",
            "timestamp": 1,
            "value": json.dumps({"id": "uc-1", "name": "视觉小说", "added": [1, 2], "removed": []}),
            "version": "1",
        }],
        ["user-collections.uc-dyn", {
            "key": "user-collections.uc-dyn",
            "timestamp": 1,
            "value": json.dumps({"id": "uc-dyn", "name": "智能", "filterSpec": {"x": 1}, "added": []}),
            "version": "1",
        }],
    ]
    (cloud / "cloud-storage-namespace-1.json").write_text(json.dumps(ns), encoding="utf-8")
    (cloud / "cloud-storage-namespaces.json").write_text(json.dumps([]), encoding="utf-8")
    env = steamenv.SteamEnv(root, "123")
    games = [
        SimpleNamespace(appid=1, name="Game One", official_zh=""),
        SimpleNamespace(appid=2, name="Game Two", official_zh=""),
    ]
    state = State({})
    return SimpleNamespace(
        env=env, state=state, games=games,
        games_by_id={g.appid: g for g in games},
    )


class PlanTests(unittest.TestCase):
    def test_read_live_collections_skips_dynamic_and_system(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td))
            live = collection_plan.read_live_collections(ctx)
            self.assertEqual([c.name for c in live], ["视觉小说"])
            self.assertEqual(live[0].appids, [1, 2])

    def test_export_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td))
            plan = collection_plan.build_plan(ctx, plan_name="测试方案")
            path = Path(td) / "plan.zip"
            collection_plan.export_plan(plan, path)
            loaded = collection_plan.read_plan(path)
            self.assertEqual(loaded.name, "测试方案")
            self.assertEqual(loaded.collections[0].name, "视觉小说")
            self.assertEqual(loaded.collections[0].appids, [1, 2])

    def test_match_filters_missing_games(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td))
            plan = collection_plan.Plan("p", [
                collection_plan.PlanCollection("视觉小说", [1, 999], [
                    collection_plan.PlanGame(1, ["Game One"]),
                    collection_plan.PlanGame(999, ["Missing Game"]),
                ])
            ])
            preview = collection_plan.match_plan(ctx, plan)
            self.assertEqual(preview.collections[0].appids, [1])
            self.assertEqual(len(preview.collections[0].unmatched), 1)
            self.assertTrue(preview.collections[0].existing)

    def test_match_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td))
            plan = collection_plan.Plan("p", [
                collection_plan.PlanCollection("视觉小说", [999], [
                    collection_plan.PlanGame(999, ["game two"]),
                ])
            ])
            preview = collection_plan.match_plan(ctx, plan)
            self.assertEqual(preview.collections[0].appids, [2])

    def test_unique_fuzzy_match(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td))
            plan = collection_plan.Plan("p", [
                collection_plan.PlanCollection("视觉小说", [999], [
                    collection_plan.PlanGame(999, ["Game Two Deluxe Edition"]),
                ])
            ])
            preview = collection_plan.match_plan(ctx, plan)
            self.assertEqual(preview.collections[0].appids, [2])

    def test_apply_creates_and_merges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctx = make_ctx(root)
            plan = collection_plan.Plan("p", [
                collection_plan.PlanCollection("视觉小说", [1, 2]),
                collection_plan.PlanCollection("新分类", [2]),
            ])
            preview = collection_plan.match_plan(ctx, plan)
            with mock.patch.object(collection_plan.apply, "BACKUP_ROOT", root / "backups"), \
                 mock.patch.object(collection_plan.steamenv, "steam_running", return_value=False), \
                 mock.patch.object(collection_plan.steamenv, "start_steam"):
                result = collection_plan.apply_preview(ctx, preview, restart=False)
            self.assertEqual(result["created"], 1)
            ns = collections.load_json_array(ctx.env.namespace_file)
            names = collections.existing_user_names(ns)
            self.assertIn("视觉小说", names)
            self.assertIn("新分类", names)


if __name__ == "__main__":
    unittest.main()
