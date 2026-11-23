from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from steam_zh import cache
from steam_zh.steamenv import SteamEnv


def _root(tmp: Path, *, shards: bool = False, monolithic: bool = False) -> SteamEnv:
    (tmp / "appcache").mkdir(parents=True)
    if shards:
        d = tmp / "appcache" / "appinfo"
        d.mkdir()
        (d / "appinfo_00.vdf").write_bytes(b"x")
    if monolithic:
        (tmp / "appcache" / "appinfo.vdf").write_bytes(b"x")
    return SteamEnv(tmp, "123")


class CacheLayoutTests(unittest.TestCase):
    def test_monolithic_is_authoritative_when_both_exist(self):
        with tempfile.TemporaryDirectory() as td:
            env = _root(Path(td), shards=True, monolithic=True)
            got = cache.AppInfoCache.detect(env)
            self.assertEqual(got.kind, cache.CacheKind.MONOLITHIC)
            self.assertTrue(got.appinfo_path and got.appinfo_path.is_file())

    def test_monolithic(self):
        with tempfile.TemporaryDirectory() as td:
            env = _root(Path(td), monolithic=True)
            got = cache.AppInfoCache.detect(env)
            self.assertEqual(got.kind, cache.CacheKind.MONOLITHIC)
            self.assertTrue(got.appinfo_path and got.appinfo_path.is_file())

    def test_missing_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            env = _root(Path(td))
            got = cache.AppInfoCache.detect(env)
            self.assertEqual(got.kind, cache.CacheKind.MISSING)
            with self.assertRaises(cache.CacheError):
                got.require()

    def test_steamapps_monolithic_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            env = _root(Path(td))
            d = Path(td) / "steamapps"
            d.mkdir()
            (d / "appinfo.vdf").write_bytes(b"x")
            got = cache.AppInfoCache.detect(env)
            self.assertEqual(got.kind, cache.CacheKind.MONOLITHIC)
            self.assertEqual(got.appinfo_path, d / "appinfo.vdf")


if __name__ == "__main__":
    unittest.main()
