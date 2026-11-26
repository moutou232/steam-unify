from __future__ import annotations

import unittest

from steam_zh import gui, gui_dialogs


class AboutTextTests(unittest.TestCase):
    def test_about_lines_show_author_repo_and_group(self):
        lines = gui_dialogs.about_lines()
        self.assertIn(f"制作者：{gui_dialogs.AUTHOR}", lines)
        self.assertIn("GitHub：moutou232/steam-unify", lines)
        self.assertIn("反馈群：811327972", lines)
        self.assertTrue(lines[0].startswith("Steam 统译 v"))

    def test_first_run_adds_usage_hints(self):
        normal = gui_dialogs.about_lines()
        welcome = gui_dialogs.about_lines(first_run=True)
        self.assertGreater(len(welcome), len(normal))
        self.assertIn("使用提示：", welcome)

    def test_contact_constants(self):
        self.assertEqual(gui_dialogs.AUTHOR, "moutou")
        self.assertEqual(gui_dialogs.GITHUB_REPO, "moutou232/steam-unify")
        self.assertEqual(gui_dialogs.GITHUB_URL, "https://github.com/moutou232/steam-unify")
        self.assertEqual(gui_dialogs.FEEDBACK_GROUP, "811327972")


class WorkspaceTests(unittest.TestCase):
    def test_only_two_workspaces(self):
        self.assertEqual([key for key, _label in gui.WORKSPACES], ["names", "categories"])
        self.assertEqual(gui.DEFAULT_WORKSPACE, "names")


if __name__ == "__main__":
    unittest.main()
