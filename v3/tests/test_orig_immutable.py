from __future__ import annotations

import unittest

from steam_zh.metadata import GameInfo
from steam_zh.state import State


class OriginalNameImmutableTests(unittest.TestCase):
    def test_merge_does_not_overwrite_original_with_live_chinese_name(self):
        st = State({
            "version": 2,
            "apps": {
                "730": {
                    "orig_name": "Counter-Strike 2",
                    "orig_localized": {"sc_schinese": "反恐精英：全球攻势"},
                    "zh_name": "反恐精英：全球攻势",
                    "source": "dictionary",
                    "applied": True,
                },
            },
            "collections": {},
            "settings": {},
        })
        game = GameInfo(
            appid=730,
            name="反恐精英：全球攻势",
            type="Game",
            localized={"sc_schinese": "反恐精英：全球攻势"},
        )
        st.merge_games([game])
        self.assertEqual(st.get_app(730)["orig_name"], "Counter-Strike 2")

    def test_merge_keeps_original_even_after_undo_style_state(self):
        st = State({
            "version": 2,
            "apps": {
                "730": {
                    "orig_name": "Counter-Strike 2",
                    "zh_name": "Counter-Strike 2",
                    "source": "original",
                    "locked": True,
                },
            },
            "collections": {},
            "settings": {},
        })
        st.merge_games([GameInfo(appid=730, name="反恐精英：全球攻势", type="Game")])
        self.assertEqual(st.get_app(730)["orig_name"], "Counter-Strike 2")


if __name__ == "__main__":
    unittest.main()
