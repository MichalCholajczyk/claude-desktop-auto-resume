"""Tk integration checks with the automation worker disabled."""
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app


class AppUITests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.patches = [patch.object(app, "load_config", lambda: dict(app.DEFAULT_CONFIG)),
                        patch.object(app, "save_config", lambda cfg: self.saved.append(dict(cfg))),
                        patch.object(app.MonitorWorker, "start", lambda w: None)]
        for p in self.patches:
            p.start()
        self.ui = app.App()
        self.ui.withdraw()
        self.ui._handle_event("chats", [
            dict(key="code:Alpha", title="Alpha", source="open", available=True, phase="inactive"),
            dict(key="chat:Beta", title="Beta", source="sidebar", available=True, phase="inactive")])

    def tearDown(self):
        self.ui.destroy()
        for p in reversed(self.patches):
            p.stop()

    def test_checking_row_persists_selection_and_switches_scope(self):
        self.ui.tree_chats.focus("1")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))
        self.assertEqual(self.saved[-1]["watch_scope"], "selected")
        self.assertEqual(self.saved[-1]["selected_chats"], ["chat:Beta"])
        self.ui._toggle_chat(types.SimpleNamespace(keysym="Return"))
        self.assertEqual(self.saved[-1]["selected_chats"], [])
        self.assertEqual(self.saved[-1]["watch_scope"], "selected")

    def test_checkboxes_save_and_worker_config_is_independent(self):
        for var in self.ui.feature_vars.values():
            var.set(True)
        self.ui._push_config()
        for key in self.ui.feature_vars:
            self.assertTrue(self.saved[-1][key])
        self.assertFalse(self.ui.worker.cfg["auto_approach"])
        commands = []
        while not self.ui.worker.cmds.empty():
            commands.append(self.ui.worker.cmds.get_nowait())
        payload = next(payload for name, payload in commands if name == "config")
        self.assertTrue(payload["auto_approach"])

    def test_open_scope_shows_effective_targets_and_polish_copy(self):
        self.assertEqual(self.ui.tree_chats.item("0", "values")[0], "Yes")
        self.assertEqual(self.ui.tree_chats.item("1", "values")[0], "No")
        self.ui._set_lang("pl")
        self.assertEqual(self.ui.tree_chats.item("0", "values")[0], "Tak")
        self.assertIn("podejście", self.ui.feature_checks["auto_approach"].cget("text"))
        self.assertEqual(self.ui.notebook.tab(self.ui.settings_tab, "text"), "Ustawienia")
        self.ui._set_lang("en")
        self.assertEqual(self.ui.tree_chats.item("0", "values")[0], "Yes")
        self.assertEqual(self.ui.feature_checks["auto_approach"].cget("text"),
                         "Answer approach questions automatically")
        self.assertEqual(self.ui.notebook.tab(self.ui.settings_tab, "text"), "Settings")
        self.assertEqual(set(app.STRINGS["en"]), set(app.STRINGS["pl"]))


if __name__ == "__main__":
    unittest.main()
