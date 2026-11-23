from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from steam_zh import collections


class CollectionTests(unittest.TestCase):
    def base(self):
        return [
            ["showcases.0", {"key": "showcases.0", "version": "1038", "value": "{}"}],
            [
                "user-collections.favorite",
                {
                    "key": "user-collections.favorite",
                    "version": "927",
                    "value": json.dumps(
                        {"id": "favorite", "name": "收藏夹", "added": [], "removed": []},
                        ensure_ascii=False,
                    ),
                },
            ],
            [
                "user-collections.uc-old",
                {
                    "key": "user-collections.uc-old",
                    "version": "100",
                    "value": json.dumps(
                        {"id": "uc-old", "name": "手工分类", "added": [1], "removed": []},
                        ensure_ascii=False,
                    ),
                },
            ],
        ]

    def test_create_update_tombstone(self):
        ns = self.base()
        managed = {}
        computed = {"角色扮演": [413150, 105600], "视觉小说": [324160]}
        changed, version, skipped = collections.sync_collections(ns, managed, computed)
        self.assertEqual(changed, 2)
        self.assertEqual(skipped, [])
        self.assertGreater(version, 1038)
        names = collections.existing_user_names(ns)
        self.assertIn("角色扮演", names)
        self.assertIn("视觉小说", names)
        self.assertIn("收藏夹", names)
        self.assertIn("手工分类", names)

        # Re-run is idempotent.
        changed2, _, _ = collections.sync_collections(ns, managed, computed)
        self.assertEqual(changed2, 0)

        # Change membership.
        changed3, _, _ = collections.sync_collections(
            ns, managed, {"角色扮演": [413150], "视觉小说": [324160]}
        )
        self.assertEqual(changed3, 1)

        deleted, _ = collections.tombstone_collections(ns, managed)
        self.assertEqual(sorted(deleted), sorted(["角色扮演", "视觉小说"]))
        self.assertEqual(managed, {})
        # Hand-made collection untouched.
        self.assertIn("手工分类", collections.existing_user_names(ns))

    def test_name_clash_is_adopted(self):
        ns = self.base()
        managed = {}
        changed, _, skipped = collections.sync_collections(
            ns, managed, {"手工分类": [99]}
        )
        self.assertEqual(changed, 1)
        self.assertEqual(skipped, [])
        self.assertIn("手工分类", managed)

    def test_namespaces_version(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cloud-storage-namespaces.json"
            p.write_text('[[1,"1040"],[3,"18"],[4,"0"]]', encoding="utf-8")
            collections.update_namespaces_version(p, 1, 1234)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data[0], [1, "1234"])
            self.assertEqual(data[1], [3, "18"])
            self.assertEqual(data[2], [4, "0"])


if __name__ == "__main__":
    unittest.main()
