from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from steam_zh import apply, cache, guard, steamenv
from steam_zh.appinfo_shards import ShardSet
from steam_zh.state import State


def live_env():
    try:
        root = steamenv.detect_root()
        env = steamenv.SteamEnv(root, steamenv.detect_account(root))
    except steamenv.SteamError as exc:
        raise unittest.SkipTest(f"Steam not available: {exc}")
    if not env.has_shards:
        raise unittest.SkipTest("sharded appinfo cache not present")
    return env


def temp_env(base: Path) -> steamenv.SteamEnv:
    env = steamenv.SteamEnv(base, "123")
    (base / "appcache").mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    return env


def copy_cache(src: steamenv.SteamEnv, env: steamenv.SteamEnv) -> Path:
    target = env.shard_dir
    shutil.copytree(src.shard_dir, target)
    # The live cache may contain read-only files from the lock feature; the
    # temp copy must stay writable so the tests can tamper with it.
    for path in target.glob("appinfo_*.vdf"):
        try:
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        except OSError:
            pass
    return target


def make_state(names: dict[int, str], **overrides) -> State:
    apps = {}
    for appid, zh in names.items():
        rec = {
            "zh_name": zh,
            "source": "xiaoheihe",
            "applied": True,
            "skipped": False,
        }
        rec.update(overrides)
        apps[str(appid)] = rec
    return State({"apps": apps})


class DriftScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = live_env()
        ss = ShardSet.load(cls.live.shard_dir)
        cls.appids = sorted(a for a in ss.by_appid if ss.get_name(a))[:6]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.env = temp_env(self.base)
        copy_cache(self.live, self.env)
        self.shards = ShardSet.load(self.env.shard_dir)

    def tearDown(self):
        self._td.cleanup()

    def _tamper(self, appid: int, name: str) -> None:
        ss = ShardSet.load(self.env.shard_dir)
        ss.set_name(appid, name)
        ss.save()

    def test_scan_detects_refreshed_name(self):
        first, second = self.appids[0], self.appids[1]
        second_name = ShardSet.load(self.env.shard_dir).get_name(second)
        state = make_state({first: "中文甲", second: second_name})
        self._tamper(first, "Official Refreshed Name")
        report = guard.scan_drift(self.env, state)
        self.assertEqual(report.cache_kind, "shards")
        self.assertIn(first, report.drifted)
        self.assertEqual(report.drifted[first], "Official Refreshed Name")
        self.assertNotIn(second, report.drifted)

    def test_scan_ignores_original_and_unapplied(self):
        first = self.appids[0]
        state = make_state({first: "中文甲"}, source="original")
        self.assertEqual(guard.scan_drift(self.env, state).count, 0)
        state = make_state({first: "中文甲"}, applied=False)
        self.assertEqual(guard.scan_drift(self.env, state).applied_total, 0)

    def test_scan_reports_missing_appid(self):
        state = make_state({999_000_111: "不存在的游戏"})
        report = guard.scan_drift(self.env, state)
        self.assertEqual(report.missing, [999_000_111])

    def test_repair_restores_only_drifted(self):
        first, second = self.appids[0], self.appids[1]
        second_name = ShardSet.load(self.env.shard_dir).get_name(second)
        state = make_state({first: "中文甲", second: second_name})
        self._tamper(first, "Official Refreshed Name")
        report = guard.scan_drift(self.env, state)
        with mock.patch.object(apply, "BACKUP_ROOT", self.base / "backups"), \
                mock.patch.object(State, "save", lambda self: None), \
                mock.patch.object(steamenv, "steam_running", lambda: False):
            res = guard.repair_drift(self.env, state, report, log=None)
        self.assertEqual(res.repaired, 1)
        self.assertTrue(res.backup and (res.backup / "manifest.json").is_file())
        check = ShardSet.load(self.env.shard_dir)
        self.assertEqual(check.get_name(first), "中文甲")
        self.assertEqual(check.get_name(second), second_name)
        manifest = json.loads((res.backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "guard")
        self.assertTrue(manifest["files"])

    def test_lock_and_unlock(self):
        first = self.appids[0]
        state = make_state({first: "中文甲"})
        with mock.patch.object(State, "save", lambda self: None):
            locked = guard.lock_shards(self.env, state, {first})
            self.assertGreater(locked, 0)
            self.assertGreaterEqual(guard.lock_state(self.env)[0], locked)
            guard.unlock_shards(self.env, state)
        self.assertEqual(guard.lock_state(self.env)[0], 0)

    def test_recent_refresh_appids_parses_log(self):
        log = self.env.root / "logs" / "appinfo_log.txt"
        log.write_text(
            "\n".join([
                "[2026-11-26 19:20:39] Apps changed: 602520=41510571, 257400=41510560,",
                "[2026-11-26 19:20:39] Change number 41510278->41510830, apps: 4/491",
                "[2026-11-26 19:20:44] App cache succeeded writing 6 shards (40 apps)",
            ]),
            encoding="utf-8",
        )
        found = guard.recent_refresh_appids(self.env)
        self.assertEqual(found, {602520: 41510571, 257400: 41510560})

    def test_compare_names_is_pure(self):
        class Stub:
            def __init__(self, names):
                self.names = names

            def get(self, appid):
                return self.names.get(appid)

        diff = cache.compare_names(Stub({1: "中文", 2: "Wrong"}), {1: "中文", 2: "对", 3: "缺"})
        self.assertEqual(diff.drifted, {2: "Wrong"})
        self.assertEqual(diff.missing, [3])


class ReconcileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = live_env()
        ss = ShardSet.load(cls.live.shard_dir)
        cls.appids = sorted(a for a in ss.by_appid if ss.get_name(a))[:4]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.env = temp_env(self.base)
        copy_cache(self.live, self.env)

    def tearDown(self):
        self._td.cleanup()

    def test_reconcile_repairs_replaced_names(self):
        first = self.appids[0]
        ss = ShardSet.load(self.env.shard_dir)
        ss.set_name(first, "Official Refreshed Name")
        ss.save()
        state = make_state({first: "中文甲"})
        names = {first: "中文甲"}
        result = apply.ApplyResult()
        with mock.patch.object(State, "save", lambda self: None), \
                mock.patch.object(steamenv, "steam_running", lambda: False), \
                mock.patch.object(steamenv, "start_steam", lambda *a, **k: None), \
                mock.patch.object(apply.time, "sleep", lambda *_: None):
            apply._reconcile_after_restart(
                self.env, state, names, result, None, restart=False, budget=8
            )
        self.assertTrue(result.cache_ready)
        self.assertEqual(result.repaired, 1)
        self.assertEqual(ShardSet.load(self.env.shard_dir).get_name(first), "中文甲")

    def test_reconcile_reports_ready_without_writing(self):
        first = self.appids[0]
        ss = ShardSet.load(self.env.shard_dir)
        ss.set_name(first, "中文甲")
        ss.save()
        state = make_state({first: "中文甲"})
        result = apply.ApplyResult()
        with mock.patch.object(State, "save", lambda self: None), \
                mock.patch.object(steamenv, "steam_running", lambda: False), \
                mock.patch.object(apply.time, "sleep", lambda *_: None):
            apply._reconcile_after_restart(
                self.env, state, {first: "中文甲"}, result, None, restart=False, budget=8
            )
        self.assertTrue(result.cache_ready)
        self.assertEqual(result.repaired, 0)


class DescribeResultTests(unittest.TestCase):
    def test_describe_never_claims_timeout(self):
        res = apply.ApplyResult(names_applied=3, names_expected=3)
        text = apply.describe_result(res, "names")
        self.assertIn("已写入中文名 3 个", text)
        self.assertNotIn("超时", text)

    def test_describe_mentions_drift(self):
        res = apply.ApplyResult(names_applied=3, names_expected=3, drifted=[1])
        text = apply.describe_result(res, "names")
        self.assertIn("已被 Steam 重新下载覆盖", text)


if __name__ == "__main__":
    unittest.main()
