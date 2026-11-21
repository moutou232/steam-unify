from __future__ import annotations

import json
import unittest

from steam_zh import collections
from steam_zh.state import State


def _rec(cid: str, name: str, added=None, filter_spec=None) -> list:
    value = {"id": cid, "name": name, "added": list(added or []), "removed": []}
    if filter_spec:
        value["filterSpec"] = filter_spec
    return [
        collections.COLLECTION_PREFIX + cid,
        {
            "key": collections.COLLECTION_PREFIX + cid,
            "value": json.dumps(value, ensure_ascii=False),
            "version": "1",
        },
    ]


def _by_cid(namespace: list) -> dict[str, dict]:
    out = {}
    for key, rec in namespace:
        out[key] = rec
    return out


class CollectionSyncTests(unittest.TestCase):
    def test_same_name_collections_are_adopted(self):
        ns = [_rec("uc-a", "动作"), _rec("uc-b", "动作")]
        managed, ids, owned = {}, {}, {}
        changed, _version, skipped = collections.sync_collections(
            ns, managed, {"动作": [1, 2]}, ids, owned
        )
        self.assertEqual(changed, 2)
        self.assertEqual(skipped, [])
        self.assertEqual(set(ids["动作"]), {"uc-a", "uc-b"})
        self.assertEqual(owned.get("动作", []), [])
        self.assertEqual(managed["动作"], "uc-a")
        by = _by_cid(ns)
        for cid in ("uc-a", "uc-b"):
            value = json.loads(by[collections.COLLECTION_PREFIX + cid]["value"])
            self.assertEqual(value["added"], [1, 2])

    def test_stale_managed_id_falls_back_to_name(self):
        ns = [_rec("uc-live", "动作")]
        managed = {"动作": "uc-old"}
        ids = {"动作": ["uc-old"]}
        owned = {"动作": ["uc-old"]}
        changed, _version, skipped = collections.sync_collections(
            ns, managed, {"动作": [5]}, ids, owned
        )
        self.assertEqual(changed, 1)
        self.assertEqual(skipped, [])
        self.assertEqual(ids["动作"], ["uc-live"])
        # The old ID was ours; the live same-name collection is adopted, not owned.
        self.assertEqual(owned["动作"], [])
        value = json.loads(ns[0][1]["value"])
        self.assertEqual(value["added"], [5])

    def test_dynamic_same_name_is_not_rewritten(self):
        ns = [_rec("uc-filter", "动作", filter_spec={"tagids": [1]})]
        changed, _version, skipped = collections.sync_collections(
            ns, {}, {"动作": [1]}, {}, {}
        )
        self.assertEqual(changed, 0)
        self.assertEqual(skipped, ["动作"])
        self.assertEqual(json.loads(ns[0][1]["value"])["added"], [])

    def test_tombstone_deletes_only_owned(self):
        ns = [_rec("uc-owned", "动作"), _rec("uc-manual", "动作")]
        managed = {"动作": "uc-owned"}
        owned = {"动作": ["uc-owned"]}
        deleted, _version = collections.tombstone_collections(ns, managed, owned)
        self.assertEqual(deleted, ["动作"])
        self.assertTrue(ns[0][1].get("is_deleted"))
        self.assertFalse(ns[1][1].get("is_deleted"))
        self.assertEqual(managed, {})
        self.assertEqual(owned, {})

    def test_legacy_state_migrates_collection_maps(self):
        st = State({"collections": {"动作": "uc-old"}})
        self.assertEqual(st.collection_ids["动作"], ["uc-old"])
        self.assertEqual(st.collection_owned["动作"], ["uc-old"])


if __name__ == "__main__":
    unittest.main()
