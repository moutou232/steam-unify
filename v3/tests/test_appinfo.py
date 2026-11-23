from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from steam_zh import appinfo, steamenv


class AppInfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = steamenv.detect_root()
            cls.src = steamenv.SteamEnv(root, steamenv.detect_account(root)).appinfo
        except steamenv.SteamError as exc:
            raise unittest.SkipTest(f"Steam not available: {exc}")
        if not cls.src.is_file():
            # Newer Steam clients use shards; fall back to a monolithic file
            # preserved in our own backups for the v29 tests.
            from steam_zh.apply import BACKUP_ROOT

            candidates = sorted(BACKUP_ROOT.glob("*/appcache/appinfo.vdf"), reverse=True)
            if candidates:
                cls.src = candidates[0]
        if not cls.src.is_file():
            raise unittest.SkipTest("no monolithic appinfo.vdf for v29 tests")
        cls.tmp = Path(tempfile.mkdtemp(prefix="appinfo-test-"))

    def setUp(self):
        self.copy = Path(tempfile.mkdtemp(prefix="appinfo-case-", dir=self.tmp)) / "appinfo.vdf"
        self.copy.write_bytes(self.src.read_bytes())
        self.ai = appinfo.AppInfo.load(self.copy)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            import shutil

            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_roundtrip_all_entries(self):
        for e in self.ai.entries:
            self.assertEqual(
                appinfo.serialize_node(e.root) + e.tail,
                e.raw_vdf,
                f"app {e.appid}",
            )

    def test_build_is_stable(self):
        data = self.ai.build()
        again = appinfo.AppInfo.from_bytes(data, self.copy)
        self.assertEqual(len(self.ai.entries), len(again.entries))
        self.assertEqual([e.appid for e in self.ai.entries], [e.appid for e in again.entries])

    def test_rename_and_remove_localized(self):
        target = None
        for e in self.ai.entries:
            if self.ai.get_localized(e.appid):
                target = e
                break
        if target is None:
            self.skipTest("no localized entry")
        old_name = self.ai.get_name(target.appid)
        old_loc = self.ai.get_localized(target.appid)
        self.ai.remove_localized(target.appid)
        self.ai.set_name(target.appid, "中文测试名称")
        data = self.ai.build()
        parsed = appinfo.AppInfo.from_bytes(data, self.copy)
        self.assertEqual(parsed.get_name(target.appid), "中文测试名称")
        self.assertIsNone(parsed.get_localized(target.appid))
        # Restore the original localized mapping and ensure it serializes.
        parsed.restore_localized(target.appid, old_loc)
        parsed.set_name(target.appid, old_name)
        again = appinfo.AppInfo.from_bytes(parsed.build(), self.copy)
        self.assertEqual(again.get_name(target.appid), old_name)
        self.assertEqual(again.get_localized(target.appid), old_loc)

    def test_hashes_and_sizes(self):
        target = next(e for e in self.ai.entries if self.ai.get_name(e.appid))
        self.ai.set_name(target.appid, "哈希测试")
        data = self.ai.build()
        magic, universe, stroff = struct.unpack_from("<IIQ", data, 0)
        self.assertEqual(magic, appinfo.MAGIC_V29)
        self.assertLess(stroff, len(data))
        parsed = appinfo.AppInfo.from_bytes(data, self.copy)
        e = parsed.entry(target.appid)
        self.assertEqual(e.size, 60 + len(e.raw_vdf))
        import hashlib

        self.assertEqual(e.vdf_sha1, hashlib.sha1(e.raw_vdf).digest())


if __name__ == "__main__":
    unittest.main()
