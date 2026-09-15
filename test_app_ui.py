"""Tk integration checks with the automation worker disabled."""
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app


class SelectionMemoryTests(unittest.TestCase):
    """Checked chats and the watch scope last only until the program closes."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        self.path = os.path.join(folder, "auto_continue_config.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"language": "pl", "message": "kontynuuj", "watch_scope": "selected",
                       "selected_chats": ["code:Podprojekt C w ActiveFlowChart"]}, f)
        patcher = patch.object(app, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_saved_selection_is_ignored_and_dropped(self):
        cfg = app.load_config()
        self.assertEqual((cfg["watch_scope"], cfg["selected_chats"]), ("open", []))
        self.assertEqual((cfg["language"], cfg["message"]), ("pl", "kontynuuj"))
        cfg.update(watch_scope="selected", selected_chats=["chat:Beta"])
        app.save_config(cfg)
        self.assertNotIn("watch_scope", self.saved())
        self.assertNotIn("selected_chats", self.saved())
        self.assertEqual(self.saved()["message"], "kontynuuj")

    def test_new_window_starts_with_nothing_checked(self):
        with patch.object(app.MonitorWorker, "start", lambda w: None):
            ui = app.App()
        self.addCleanup(ui.destroy)
        ui.withdraw()
        self.assertEqual(ui.checked_chats, set())
        self.assertEqual(ui.cmb_scope.current(), 0)
        ui._handle_event("chats", [dict(key="chat:Beta", title="Beta", source="sidebar",
                                        available=True, phase="inactive")])
        ui.tree_chats.focus("0")
        ui._toggle_chat(types.SimpleNamespace(keysym="space"))
        commands = []
        while not ui.worker.cmds.empty():
            commands.append(ui.worker.cmds.get_nowait())
        payload = [p for name, p in commands if name == "config"][-1]
        # The running worker gets the selection; the settings file never does.
        self.assertEqual((payload["watch_scope"], payload["selected_chats"]), ("selected", ["chat:Beta"]))
        self.assertNotIn("selected_chats", self.saved())
        self.assertNotIn("watch_scope", self.saved())


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

    def test_checking_row_selects_it_and_switches_scope(self):
        self.ui.tree_chats.focus("1")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))
        self.assertEqual(self.ui.cfg["watch_scope"], "selected")
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta"])
        self.ui._toggle_chat(types.SimpleNamespace(keysym="Return"))
        self.assertEqual(self.ui.cfg["selected_chats"], [])
        self.assertEqual(self.ui.cfg["watch_scope"], "selected")

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

    def test_refresh_reorders_rows_without_moving_keyboard_focus_to_another_chat(self):
        self.ui.tree_chats.focus("1")
        previous = list(self.ui.chat_rows)
        self.ui._handle_event("chats", [
            dict(key="code:Newest", title="Newest", source="open", available=True,
                 phase="inactive"), *previous])
        self.assertEqual(self.ui.tree_chats.item("0", "values")[1], "Newest")
        self.assertEqual(self.ui.tree_chats.focus(), "2")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta"])

    def load_sortable_rows(self):
        # Claude's order: newest conversation first.
        self.ui._handle_event("chats", [
            dict(key="code:Zeta", title="Zeta task", source="sidebar", available=True, phase="inactive"),
            dict(key="chat:alpha", title="alpha chat", source="open", available=True, phase="inactive"),
            dict(key="code:Beta", title="Beta", source="sidebar", available=True, phase="inactive")])

    def titles(self):
        return [self.ui.tree_chats.item(iid, "values")[1] for iid in self.ui.tree_chats.get_children()]

    def test_heading_click_sorts_and_reverses(self):
        self.load_sortable_rows()
        self.ui.tk.call(self.ui.tree_chats.heading("chat", "command"))
        self.assertEqual(self.titles(), ["alpha chat", "Beta", "Zeta task"])
        self.assertEqual(self.ui.tree_chats.heading("chat", "text"), "Conversation ▲")
        self.ui.tk.call(self.ui.tree_chats.heading("chat", "command"))
        self.assertEqual(self.titles(), ["Zeta task", "Beta", "alpha chat"])
        self.assertEqual(self.ui.tree_chats.heading("chat", "text"), "Conversation ▼")
        self.ui.tk.call(self.ui.tree_chats.heading("source", "command"))
        self.assertEqual(self.titles()[0], "alpha chat")       # "Open pane" before "Sidebar"
        self.assertEqual(self.ui.tree_chats.heading("chat", "text"), "Conversation")

    def test_default_order_button_restores_claude_order(self):
        self.load_sortable_rows()
        self.assertEqual(str(self.ui.btn_default_order.cget("state")), "disabled")
        self.ui._sort_by("chat")
        self.assertEqual(str(self.ui.btn_default_order.cget("state")), "normal")
        self.ui.btn_default_order.invoke()
        self.assertEqual(self.titles(), ["Zeta task", "alpha chat", "Beta"])
        self.assertEqual(self.ui.tree_chats.heading("chat", "text"), "Conversation")
        self.assertEqual(str(self.ui.btn_default_order.cget("state")), "disabled")

    def test_sort_survives_refresh_and_toggles_the_chosen_row(self):
        self.load_sortable_rows()
        self.ui._sort_by("chat")
        self.ui._handle_event("chats", [
            dict(key="code:Newest", title="Newest", source="sidebar", available=True, phase="inactive"),
            *self.ui.chat_rows])
        self.assertEqual(self.titles(), ["alpha chat", "Beta", "Newest", "Zeta task"])
        self.ui.tree_chats.focus("2")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))
        self.assertEqual(self.ui.cfg["selected_chats"], ["code:Newest"])
        self.assertEqual(self.ui.tree_chats.focus(), "2")

    def test_watch_column_sorts_checked_first(self):
        self.load_sortable_rows()
        self.ui.tree_chats.focus("2")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))    # checks "Beta"
        self.ui._sort_by("watch")
        self.assertEqual(self.titles(), ["Beta", "Zeta task", "alpha chat"])

    def test_clicking_a_heading_does_not_check_a_row(self):
        self.load_sortable_rows()
        with patch.object(self.ui.tree_chats, "identify_region", return_value="heading"), \
                patch.object(self.ui.tree_chats, "identify_row", return_value="0"):
            self.ui._toggle_chat(types.SimpleNamespace(keysym="??", x=40, y=6))
        self.assertEqual(self.ui.checked_chats, set())

    def test_each_option_has_a_question_mark_with_plain_help(self):
        for key, check in self.ui.feature_checks.items():
            icon = self.ui.feature_help[key]
            self.assertEqual(icon.master, check.master)            # sits next to its checkbox
            for sequence in ("<Enter>", "<Leave>", "<Button-1>", "<FocusIn>", "<Return>"):
                self.assertTrue(icon.bind(sequence), f"{key} icon lacks {sequence}")
            for lang in ("en", "pl"):
                text = app.tr(lang, key + "_help")
                self.assertNotEqual(text, key + "_help")
                self.assertGreater(len(text), 3 * len(app.tr(lang, key)))
        self.assertIn("Try again", app.tr("pl", "prefer_try_again_help"))
        self.assertIn("15 min", app.tr("pl", "retry_api_errors_help"))
        self.assertIn("Pick your recommended option(s).", app.tr("en", "auto_approach_help"))

    def test_question_mark_shows_and_hides_the_explanation(self):
        self.ui._show_help("retry_api_errors")
        self.assertEqual(self.ui.help_tip.state(), "normal")
        self.assertEqual(self.ui.help_label.cget("text"), app.tr("en", "retry_api_errors_help"))
        self.ui._toggle_help("retry_api_errors")                 # Enter / Space again closes it
        self.assertEqual(self.ui.help_tip.state(), "withdrawn")
        self.ui._set_lang("pl")
        self.ui._toggle_help("auto_approach")
        self.assertEqual(self.ui.help_label.cget("text"), app.tr("pl", "auto_approach_help"))
        self.ui._hide_help()
        self.assertEqual(self.ui.help_tip.state(), "withdrawn")

    def test_countdown_is_shown_while_starting(self):
        until = app.dt.datetime.now() + app.dt.timedelta(seconds=8.5)
        self.ui._handle_event("state", dict(state="STARTING", reset_at=None, send_at=None, start_until=until))
        self.ui._tick_ui()
        self.assertRegex(self.ui.lbl_state.cget("text"), r"^Autonomous control in [89] s$")
        self.assertRegex(self.ui.lbl_clock.cget("text"), r"^00:00:0[89]$")
        self.assertIn("Stop", self.ui.lbl_caption.cget("text"))
        self.assertEqual(str(self.ui.btn_start.cget("state")), "disabled")
        self.assertEqual(str(self.ui.btn_stop.cget("state")), "normal")
        self.ui._handle_event("chats", [dict(key="code:Alpha", title="Alpha", source="open",
                                             available=True, phase="starting")])
        self.assertEqual(self.ui.tree_chats.item("0", "values")[3], "Starting soon")

    def test_countdown_length_is_a_saved_setting(self):
        self.ui.var_start_delay.set(25)
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["start_delay_s"], 25)
        self.assertEqual(app.DEFAULT_CONFIG["start_delay_s"], 10)

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
