from __future__ import annotations

import unittest
from types import SimpleNamespace

from steam_zh import providers


def candidate(source, name, confidence=0.9, score=0.0):
    return providers.NameCandidate(source, name, confidence, "", "ok", "", score)


class PriorityTests(unittest.TestCase):
    def test_normalize_defaults_and_unknown(self):
        order = providers.normalize_priority_order(["3dm", "unknown", "3dm"])
        self.assertEqual(order[0], "3dm")
        self.assertNotIn("unknown", order)
        self.assertEqual(order.count("3dm"), 1)
        self.assertEqual(set(order), set(providers.DEFAULT_SOURCE_ORDER))

    def test_normalize_empty_uses_default(self):
        order = providers.normalize_priority_order([])
        self.assertEqual(order, providers.DEFAULT_SOURCE_ORDER)
        self.assertEqual(order[0], "xiaoheihe")

    def test_user_order_wins(self):
        items = [
            candidate("steam_store", "Steam 名", 0.92, 150.4),
            candidate("xiaoheihe", "黑盒名", 0.90, 148.0),
            candidate("3dm", "3DM 名", 0.85, 139.5),
        ]
        ordered = providers.order_name_candidates(items, ["3dm", "xiaoheihe", "steam_store"])
        self.assertEqual(ordered[0].source, "3dm")
        self.assertEqual(ordered[1].source, "xiaoheihe")

    def test_manual_is_always_first(self):
        items = [
            candidate("3dm", "哈迪斯"),
            candidate("manual", "哈迪斯"),
        ]
        ordered = providers.order_name_candidates(items, ["3dm"])
        self.assertEqual(ordered[0].source, "manual")

    def test_missing_top_source_falls_through(self):
        items = [candidate("xiaoheihe", "哈迪斯"), candidate("3dm", "哈迪斯")]
        ordered = providers.order_name_candidates(items, ["steam_store", "3dm"])
        self.assertEqual(ordered[0].source, "3dm")

    def test_duplicate_name_keeps_higher_rank(self):
        items = [
            candidate("3dm", "哈迪斯", 0.99),
            candidate("xiaoheihe", "哈迪斯", 0.80),
        ]
        ordered = providers.order_name_candidates(items, ["3dm", "xiaoheihe"])
        self.assertEqual(len(ordered), 1)
        self.assertEqual(ordered[0].source, "3dm")


class ReselectTests(unittest.TestCase):
    def test_reselect_auto_only(self):
        class FakeState:
            def __init__(self):
                self.apps = {
                    1: {
                        "zh_name": "旧名",
                        "source": "xiaoheihe",
                        "name_selected_by": "auto",
                        "name_candidates": [
                            {"source": "xiaoheihe", "name": "黑盒名", "confidence": 0.9},
                            {"source": "3dm", "name": "3DM名", "confidence": 0.85},
                        ],
                    },
                    2: {
                        "zh_name": "手动名",
                        "source": "manual",
                        "name_selected_by": "manual",
                        "name_candidates": [
                            {"source": "3dm", "name": "3DM名", "confidence": 0.85},
                        ],
                    },
                    3: {
                        "zh_name": "锁定名",
                        "source": "xiaoheihe",
                        "locked": True,
                        "name_candidates": [
                            {"source": "3dm", "name": "3DM名", "confidence": 0.85},
                        ],
                    },
                }
                self.saved = 0

            def save(self):
                self.saved += 1

        state = FakeState()
        changed = providers.reselect_cached_candidates(state, ["3dm", "xiaoheihe"])
        self.assertEqual(changed, 1)
        self.assertEqual(state.apps[1]["zh_name"], "3DM名")
        self.assertEqual(state.apps[2]["zh_name"], "手动名")
        self.assertEqual(state.apps[3]["zh_name"], "锁定名")


if __name__ == "__main__":
    unittest.main()
