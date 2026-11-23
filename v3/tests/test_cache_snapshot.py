from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from steam_zh import cache
from steam_zh.steamenv import SteamEnv


def env_with(root: Path) -> SteamEnv:
    (root / "appcache").mkdir(parents=True, exist_ok=True)
    return SteamEnv(root, "123")


class SnapshotRestoreTests(unittest.TestCase):
    def test_restore_shards_after_steam_deletes_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = env_with(root)
            shard_dir = root / "appcache" / "appinfo"
            shard_dir.mkdir()
            (shard_dir / "appinfo_00.vdf").write_bytes(b"shard")
            backup = root / "backup"
            (backup / "appinfo").mkdir(parents=True)
            (backup / "appinfo" / "appinfo_00.vdf").write_bytes(b"shard")
            snapshot = cache.CacheSnapshot(
                cache.AppInfoCache(cache.CacheKind.SHARDS, shard_dir=shard_dir),
                shard_count=1,
            )
            import shutil
            shutil.rmtree(shard_dir)
            snapshot.restore_if_missing(env, backup)
            self.assertTrue((shard_dir / "appinfo_00.vdf").is_file())

    def test_restore_monolithic_after_steam_deletes_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = env_with(root)
            target = root / "appcache" / "appinfo.vdf"
            target.write_bytes(b"old")
            backup = root / "backup"
            (backup / "appcache").mkdir(parents=True)
            (backup / "appcache" / "appinfo.vdf").write_bytes(b"old")
            snapshot = cache.CacheSnapshot(
                cache.AppInfoCache(cache.CacheKind.MONOLITHIC, appinfo_path=target)
            )
            target.unlink()
            snapshot.restore_if_missing(env, backup)
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), b"old")

    def test_missing_shard_backup_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = env_with(root)
            snapshot = cache.CacheSnapshot(
                cache.AppInfoCache(cache.CacheKind.SHARDS,
                                   shard_dir=root / "appcache" / "appinfo"),
                shard_count=1,
            )
            with self.assertRaises(cache.CacheError):
                snapshot.restore_if_missing(env, root / "backup")


if __name__ == "__main__":
    unittest.main()
