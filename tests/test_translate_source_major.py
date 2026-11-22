from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from steam_zh import providers


class FakeState:
    def __init__(self):
        self.apps: dict[int, dict] = {}
        self.saves = 0

    def get_app(self, appid):
        return self.apps.setdefault(appid, {})

    def save(self):
        self.saves += 1


def game(appid: int, name: str):
    return SimpleNamespace(appid=appid, name=name, official_zh="", has_cjk=False)


def candidate(source: str, name: str):
    return providers.NameCandidate(source, name, 0.9, "", "ok", source)


class SourceMajorTests(unittest.TestCase):
    def test_priority_phase_stops_after_first_hit(self):
        calls: list[tuple[int, str]] = []

        def fake_fetch(g, source, **kwargs):
            calls.append((g.appid, source))
            if g.appid == 1 and source == "xiaoheihe":
                return "ok", candidate("xiaoheihe", "黑盒名")
            if g.appid == 2 and source == "3dm":
                return "ok", candidate("3dm", "3DM名")
            return "no-result", None

        state = FakeState()
        logs: list[str] = []
        with mock.patch.object(providers, "fetch_source_candidate", side_effect=fake_fetch):
            result = providers.translate_games_v2(
                [game(1, "Game One"), game(2, "Game Two")],
                state,
                use_network=True,
                priority_order=["xiaoheihe", "3dm", "steam_official"],
                log=logs.append,
            )

        self.assertEqual(result[1], "xiaoheihe")
        self.assertEqual(result[2], "3dm")
        self.assertEqual(state.get_app(1)["zh_name"], "黑盒名")
        self.assertEqual(state.get_app(2)["source"], "3dm")
        # Game 1 must not be queried again after the Xiaoheihe hit.
        self.assertNotIn((1, "3dm"), calls)
        self.assertNotIn((1, "steam_official"), calls)
        self.assertIn((2, "xiaoheihe"), calls)
        self.assertIn((2, "3dm"), calls)
        self.assertTrue(any("小黑盒" in line for line in logs))
        self.assertTrue(any("自动翻译开始" in line for line in logs))

    def test_all_sources_missing_marks_game(self):
        with mock.patch.object(
            providers, "fetch_source_candidate", return_value=("no-result", None)
        ):
            state = FakeState()
            result = providers.translate_games_v2(
                [game(1, "Game One")],
                state,
                use_network=False,
                priority_order=["xiaoheihe", "steam_official"],
            )
        self.assertEqual(result[1], "missing")
        self.assertIn("所有来源均未找到", state.get_app(1)["error"])

    def test_cancel_does_not_mark_unprocessed_as_missing(self):
        state = FakeState()
        with mock.patch.object(
            providers, "fetch_source_candidate", return_value=("ok", candidate("xiaoheihe", "test"))
        ):
            result = providers.translate_games_v2(
                [game(1, "Game One")],
                state,
                use_network=False,
                priority_order=["xiaoheihe"],
                should_stop=lambda: True,
            )
        self.assertEqual(result, {})
        self.assertNotIn("error", state.get_app(1))


if __name__ == "__main__":
    unittest.main()
