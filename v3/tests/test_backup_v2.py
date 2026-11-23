from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from steam_zh import backup_bundle


def _zip(tmp: Path, manifest: dict, extra: dict[str, bytes] | None = None) -> Path:
    p = tmp / "backup.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup/manifest.json", json.dumps(manifest))
        for name, data in (extra or {}).items():
            zf.writestr(f"backup/{name}", data)
    return p


class BackupBundleTests(unittest.TestCase):
    def test_rejects_non_zip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.zip"
            p.write_text("no", encoding="utf-8")
            with self.assertRaises(backup_bundle.BackupError):
                backup_bundle.peek_backup(p)

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            p = _zip(Path(td), {
                "format": backup_bundle.FORMAT,
                "format_version": 1,
            })
            with zipfile.ZipFile(p, "a") as zf:
                zf.writestr("../outside", b"x")
            with self.assertRaises(backup_bundle.BackupError):
                backup_bundle.peek_backup(p)

    def test_accepts_valid_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            p = _zip(Path(td), {
                "format": backup_bundle.FORMAT,
                "format_version": 1,
                "cache_kind": "shards",
                "account": "123",
                "files": [],
            })
            m = backup_bundle.peek_backup(p)
            self.assertEqual(m["cache_kind"], "shards")


if __name__ == "__main__":
    unittest.main()
