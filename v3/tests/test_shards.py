from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from steam_zh import steamenv
from steam_zh.appinfo_shards import ShardFile, ShardSet


class ShardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = steamenv.detect_root()
            cls.env = steamenv.SteamEnv(root, steamenv.detect_account(root))
        except steamenv.SteamError as exc:
            raise unittest.SkipTest(f"Steam not available: {exc}")
        if not cls.env.has_shards:
            raise unittest.SkipTest("sharded appinfo cache not present")
        cls.shards = list(cls.env.shard_dir.glob("appinfo_*.vdf"))

    def test_clean_roundtrip(self):
        ss = ShardSet.load(self.env.shard_dir)
        self.assertEqual(len(ss.shards), len(self.shards))
        for sh in ss.shards:
            self.assertEqual(sh.build(), sh._original, sh.path.name)

    def test_modify_single_shard(self):
        src = self.shards[0]
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / src.name
            dst.write_bytes(src.read_bytes())
            sh = ShardFile.load(dst)
            appid = sh.records[0].appid
            old = sh.get_name(appid)
            sh.set_name(appid, "中文测试名称")
            data = sh.build()
            dst.write_bytes(data)
            parsed = ShardFile.load(dst)
            self.assertEqual(parsed.get_name(appid), "中文测试名称")
            # CRCs must match the documented fields.
            ec, idxsz, kc, ktsz, dsz = struct.unpack_from("<IIIII", data, 16)
            key_start = 44 + idxsz
            data_start = key_start + ktsz
            c_index, c_header = struct.unpack_from("<II", data, 36)
            self.assertEqual(c_index, zlib.crc32(data[44:data_start]) & 0xFFFFFFFF)
            self.assertEqual(c_header, zlib.crc32(data[:40]) & 0xFFFFFFFF)
            self.assertEqual(data_start + dsz, len(data))

    def test_set_many_names(self):
        import shutil

        with tempfile.TemporaryDirectory() as td:
            for src in self.shards:
                shutil.copy2(src, Path(td) / src.name)
            ss = ShardSet.load(td)
            targets = list(ss.by_appid)[:20]
            for appid in targets:
                ss.set_name(appid, "测试名")
            ss.save()
            check = ShardSet.load(td)
            for appid in targets:
                self.assertEqual(check.get_name(appid), "测试名")


if __name__ == "__main__":
    unittest.main()
