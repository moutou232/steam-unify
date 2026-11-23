from __future__ import annotations

import json
import unittest

from steam_zh import collections


def _rec(cid: str, name: str, filter_spec=None, deleted=False) -> list:
    value = {"id": cid, "name": name, "added": [], "removed": []}
    if filter_spec:
        value["filterSpec"] = filter_spec
    rec = {
        "key": collections.COLLECTION_PREFIX + cid,
        "value": json.dumps(value, ensure_ascii=False),
        "version": "1",
    }
    if deleted:
        rec = {"is_deleted": True, "version": "1"}
    return [collections.COLLECTION_PREFIX + cid, rec]


class CategoryCleanupTests(unittest.TestCase):
    def test_removes_static_non_system_only(self):
        ns = [
            _rec("favorite", "收藏夹"),
            _rec("hidden", "已隐藏"),
            _rec("uc-a", "动作"),
            _rec("uc-b", "角色扮演"),
            _rec("uc-c", "其他"),
            _rec("uc-filter", "智能", filter_spec={"tagids": [1]}),
            _rec("uc-deleted", "旧分类", deleted=True),
        ]
        keys = collections.category_collection_keys(ns)
        self.assertEqual(
            sorted(name for _key, name in keys),
            sorted(["动作", "角色扮演", "其他"]),
        )
        deleted, _version = collections.tombstone_category_collections(ns)
        self.assertEqual(sorted(deleted), sorted(["动作", "角色扮演", "其他"]))
        by_key = {k: v for k, v in ns}
        self.assertFalse(by_key[collections.COLLECTION_PREFIX + "favorite"].get("is_deleted"))
        self.assertFalse(by_key[collections.COLLECTION_PREFIX + "hidden"].get("is_deleted"))
        self.assertFalse(by_key[collections.COLLECTION_PREFIX + "uc-filter"].get("is_deleted"))
        for cid in ("uc-a", "uc-b", "uc-c"):
            self.assertTrue(by_key[collections.COLLECTION_PREFIX + cid].get("is_deleted"))

    def test_empty_namespace(self):
        self.assertEqual(collections.category_collection_keys([]), [])
        deleted, _version = collections.tombstone_category_collections([])
        self.assertEqual(deleted, [])


if __name__ == "__main__":
    unittest.main()
