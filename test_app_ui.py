"""Tk integration checks with the automation worker disabled."""
import ast
import json
import os
import queue
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app
import chatgpt_auto_continue as gpt
from session_automation import SessionState
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


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
        self.assertEqual(ui.cfg["watch_scope"], "open")
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

    def watch_column(self):
        return {self.ui.tree_chats.item(iid, "values")[1]: self.ui.tree_chats.item(iid, "values")[0]
                for iid in self.ui.tree_chats.get_children()}

    def toggle(self, *rows):
        for row in rows:
            self.ui.tree_chats.focus(row)
            self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))

    def test_no_scope_list_nothing_checked_watches_the_open_conversations(self):
        """The "All open conversation panes / Only checked conversations" list is gone:
        with nothing checked, what is open in Claude is watched, and the list says so."""
        widgets, stack = [], [self.ui.chats_tab]
        while stack:
            widget = stack.pop()
            widgets.append(widget)
            stack.extend(widget.winfo_children())
        self.assertFalse([w for w in widgets if isinstance(w, app.ttk.Combobox)])
        self.assertEqual(self.watch_column(), {"Alpha": "Yes", "Beta": "No"})
        self.assertEqual(self.ui.cfg["watch_scope"], "open")
        self.assertEqual(self.ui.lbl_watching.cget("text"), app.tr("en", "watch_open"))
        self.assertEqual(self.ui.lbl_chats_hint.cget("text"), app.tr("en", "chats_hint_open"))

    def test_clicking_a_row_flips_only_that_row(self):
        self.toggle("1")                                  # Beta, from the sidebar
        self.assertEqual(self.watch_column(), {"Alpha": "Yes", "Beta": "Yes"})
        self.assertEqual(self.ui.cfg["watch_scope"], "selected")
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta", "code:Alpha"])
        self.assertEqual(self.ui.lbl_watching.cget("text"), app.tr("en", "watch_picked", n=2))
        self.assertEqual(self.ui.lbl_chats_hint.cget("text"), app.tr("en", "chats_hint_picked"))
        self.toggle("0")                                  # Alpha off; Beta stays
        self.assertEqual(self.watch_column(), {"Alpha": "No", "Beta": "Yes"})
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta"])

    def test_unchecking_everything_goes_back_to_the_open_conversations(self):
        self.toggle("1", "0", "1")                        # Beta on, Alpha off, Beta off
        self.assertEqual(self.watch_column(), {"Alpha": "Yes", "Beta": "No"})
        self.assertEqual((self.ui.cfg["watch_scope"], self.ui.cfg["selected_chats"]), ("open", []))
        self.assertEqual(self.ui.lbl_watching.cget("text"), app.tr("en", "watch_open"))

    def test_handover_settings_show_only_while_the_option_is_on(self):
        group, check = self.ui.handover_rows, self.ui.feature_checks["handover_enabled"]
        self.assertFalse(self.ui.feature_vars["handover_enabled"].get())
        self.assertEqual(group.winfo_manager(), "")                  # hidden
        check.invoke()                                               # the user ticks it
        self.assertTrue(self.saved[-1]["handover_enabled"])
        self.assertEqual(group.winfo_manager(), "pack")
        slaves = self.ui.settings_tab.pack_slaves()
        message_row = self.ui.lbl_message.master.master
        self.assertLess(slaves.index(group), slaves.index(message_row))   # where it always was
        for name in ("lbl_threshold", "lbl_plan_folder", "btn_handover_texts"):
            widget = getattr(self.ui, name)
            while widget is not None and widget is not group:
                widget = widget.master
            self.assertIs(widget, group, name)
        check.invoke()
        self.assertEqual(group.winfo_manager(), "")

    def test_handover_settings_are_shown_at_start_when_the_option_is_saved_on(self):
        with patch.object(app, "load_config", lambda: dict(app.DEFAULT_CONFIG, handover_enabled=True)):
            ui = app.App()
        self.addCleanup(ui.destroy)
        ui.withdraw()
        self.assertEqual(ui.handover_rows.winfo_manager(), "pack")

    def test_handover_settings_are_explained(self):
        for key in ("handover_threshold", "plan_folder", "handover_texts"):
            self.assertIn(key, self.ui.help_icons)
            for lang in ("en", "pl"):
                text = app.tr(lang, key + "_help")
                self.assertNotEqual(text, key + "_help", (lang, key))
                self.assertGreater(len(text), 250, (lang, key))
        self.ui._show_help("plan_folder")
        self.assertEqual(self.ui.help_label.cget("text"), app.tr("en", "plan_folder_help"))
        self.ui._hide_help()
        self.assertIn("Settings", app.tr("en", "handover_enabled_help"))
        self.assertIn("Ustawienia", app.tr("pl", "handover_enabled_help"))

    def test_the_messages_dialog_explains_both_messages(self):
        self.ui._edit_handover_texts()
        dialog = next(w for w in self.ui.winfo_children()
                      if isinstance(w, app.tk.Toplevel) and w is not self.ui.help_tip)
        texts, stack = [], [dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if isinstance(widget, app.tk.Label):
                texts.append(widget.cget("text"))
        dialog.destroy()
        self.assertIn(app.tr("en", "dlg_handover_intro"), texts)

    def test_the_messages_dialog_opens_over_the_window_ready_for_the_keyboard(self):
        """26.09: it opened in the corner of the screen, and Escape did nothing until it
        was clicked, because the keyboard stayed with the main window."""
        self.ui.deiconify()          # the dialog is opened from the visible window
        self.ui.update()
        with patch.object(self.ui, "winfo_rootx", return_value=400), \
                patch.object(self.ui, "winfo_rooty", return_value=200), \
                patch.object(self.ui, "winfo_width", return_value=860):
            self.ui._edit_handover_texts()
        dialog = next(w for w in self.ui.winfo_children()
                      if isinstance(w, app.tk.Toplevel) and w is not self.ui.help_tip)
        dialog.update()
        x, y = (int(part) for part in dialog.geometry().split("+")[1:3])
        self.assertTrue(400 <= x <= 400 + 860 - dialog.winfo_reqwidth(), dialog.geometry())
        self.assertTrue(200 <= y <= 400, dialog.geometry())
        focused = dialog.focus_lastfor()
        dialog.destroy()
        self.assertIsInstance(focused, app.tk.Text)       # the first message, ready to edit

    def test_hovered_and_pressed_controls_stay_dark_and_readable(self):
        """clam paints a hovered control #eeebe7: a hovered heading of the list went white
        and its text disappeared. Hover and press must stay dark, with readable text."""
        def luminance(color):
            r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        style = app.ttk.Style(self.ui)
        for name in ("Treeview.Heading", "TNotebook.Tab", "TCombobox", "TSpinbox", "TEntry",
                     "TButton", "Panel.TCheckbutton", "Vertical.TScrollbar"):
            for state in (("active",), ("pressed",), ("readonly",), ("disabled",)):
                bg = style.lookup(name, "background", state)
                fg = style.lookup(name, "foreground", state)
                self.assertLess(luminance(bg), 0.1, (name, state, bg))
                if "disabled" not in state:
                    ratio = (luminance(fg) + 0.05) / (luminance(bg) + 0.05)
                    self.assertGreaterEqual(ratio, 4.5, (name, state, fg, bg))
            # no light bevel lines either (clam draws the top edge of a tab #eeebe7)
            for option in ("lightcolor", "darkcolor", "bordercolor", "fieldbackground"):
                for state in ((), ("active",), ("pressed",), ("selected",), ("readonly",)):
                    color = style.lookup(name, option, state)
                    if color:
                        self.assertLess(luminance(color), 0.1, (name, option, state, color))
        # a focused field is outlined in the accent colour, like every other focus ring
        self.assertEqual(style.lookup("TEntry", "lightcolor", ("focus",)), app.THEME["amber"])
        # the sortable headings and the tabs answer the pointer
        self.assertNotEqual(style.lookup("Treeview.Heading", "background", ("active",)),
                            style.lookup("Treeview.Heading", "background"))
        self.assertNotEqual(style.lookup("TNotebook.Tab", "background", ("active",)),
                            style.lookup("TNotebook.Tab", "background"))

    def test_a_conversation_handed_over_is_replaced_by_its_successor_in_the_list(self):
        self.toggle("1")                                          # Alpha and Beta checked
        self.ui._handle_event("selection", ["chat:Beta", "code:Alpha 2"])
        self.assertEqual(self.ui.checked_chats, {"chat:Beta", "code:Alpha 2"})
        self.ui._push_config()                                    # a later settings change keeps it
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta", "code:Alpha 2"])

    def drain(self):
        while not self.ui.worker.out.empty():
            self.ui._handle_event(*self.ui.worker.out.get_nowait())

    def test_log_lines_change_language_with_the_window(self):
        """Lines written before a language switch are shown again in the new language,
        with the parts inside them (a chat's status, the pause reason, an error)."""
        worker = self.ui.worker
        self.ui._set_lang("pl")
        worker.cfg["language"] = "pl"            # what the running worker would receive
        worker.log("log_started")
        worker.engine.note("code:Alpha", SessionState(), "waiting_api")
        worker.log("log_paused", "warn", why=worker.text("pause_user"))
        worker.engine.note("code:Beta", SessionState(), "Existing draft; leaving it untouched")
        self.drain()
        self.assertIn("Czuwanie uruchomione.", self.ui.txt_log.get("1.0", "end-1c"))
        self.ui._set_lang("en")
        english = self.ui.txt_log.get("1.0", "end-1c")
        for line in ("Watching started.", "Alpha: Server error — retry scheduled",
                     "Paused: someone is using the mouse.", "Beta: Existing draft; leaving it untouched"):
            self.assertIn(line, english)
        for polish in ("Czuwanie", "Błąd serwera", "Wstrzymane", "szkic"):
            self.assertNotIn(polish, english)
        self.assertTrue(self.ui.txt_log.tag_ranges("warn"))     # the colours survive the switch

    def test_status_line_and_hint_follow_the_language(self):
        self.toggle("1")
        self.ui._set_lang("pl")
        self.assertEqual(self.ui.lbl_watching.cget("text"), app.tr("pl", "watch_picked", n=2))
        self.assertEqual(self.ui.lbl_chats_hint.cget("text"), app.tr("pl", "chats_hint_picked"))
        self.assertNotIn("scope_open", app.STRINGS["en"])

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
        # Beta joins the open conversations, which stay watched as the list showed them.
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:Beta", "code:Alpha", "code:Newest"])

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
        self.assertEqual(self.ui.cfg["selected_chats"], ["chat:alpha", "code:Newest"])
        self.assertEqual(self.ui.tree_chats.focus(), "2")

    def test_watch_column_sorts_checked_first(self):
        self.load_sortable_rows()
        self.ui.tree_chats.focus("2")
        self.ui._toggle_chat(types.SimpleNamespace(keysym="space"))    # checks "Beta"; "alpha chat" is open
        self.ui._sort_by("watch")
        self.assertEqual(self.titles(), ["alpha chat", "Beta", "Zeta task"])

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

    def test_default_message_says_the_limit_has_reset(self):
        self.assertEqual(app.DEFAULT_MESSAGE,
                         "I hit my usage limit while you were working, but it has reset now. "
                         "Please continue from where you left off if possible autonomously.")
        self.assertEqual(app.DEFAULT_CONFIG["message"], app.DEFAULT_MESSAGE)
        self.assertEqual(self.ui.txt_message.get("1.0", "end-1c"), app.DEFAULT_MESSAGE)

    def test_a_blank_message_falls_back_to_the_default(self):
        self.ui.txt_message.delete("1.0", "end")
        self.ui._commit_message()
        self.assertEqual(self.saved[-1]["message"], app.DEFAULT_MESSAGE)
        self.assertEqual(self.ui.txt_message.get("1.0", "end-1c"), app.DEFAULT_MESSAGE)

    def test_countdown_length_is_a_saved_setting(self):
        self.ui.var_start_delay.set(25)
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["start_delay_s"], 25)
        self.assertEqual(app.DEFAULT_CONFIG["start_delay_s"], 10)



    def test_settings_labels_survive_a_usage_reading(self):
        """The usage meter must not write into the settings labels (they had the same
        attribute name once, so the plan folder lost its label)."""
        self.ui._handle_event("usage", {"rows": {"5h": {"pct": 12}, "weekly": {"pct": 30}},
                                        "session": "Alpha"})
        for name in ("lbl_quiet", "lbl_threshold", "lbl_plan_folder"):
            self.assertTrue(getattr(self.ui, name).cget("text"), name)
        self.assertIn("30", self.ui.lbl_plan.cget("text"))

    def test_threshold_field_is_saved_and_a_bad_value_is_kept_out(self):
        self.ui.var_threshold.set("70%")
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["handover_threshold"], "70%")
        self.assertEqual(self.ui.lbl_threshold_hint.cget("fg"), app.THEME["muted"])
        self.ui.var_threshold.set("dużo")
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["handover_threshold"], "70%")
        self.assertEqual(self.ui.lbl_threshold_hint.cget("fg"), app.THEME["red"])
        self.assertEqual(app.DEFAULT_CONFIG["handover_threshold"], "700k")

    def test_plan_folder_and_quiet_time_are_saved(self):
        self.ui.var_plan.set(os.path.join("C:", "projekt", "docs", "plan"))
        self.ui.var_quiet.set(45)
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["plan_folder"], os.path.join("C:", "projekt", "docs", "plan"))
        self.assertEqual(self.saved[-1]["foreign_input_quiet_s"], 45)

    def test_both_new_options_are_checkboxes_with_help(self):
        for key in ("pause_on_foreign_input", "handover_enabled"):
            self.assertIn(key, self.ui.feature_checks)
            self.assertIn(key, self.ui.feature_help)
            self.assertTrue(self.ui.feature_checks[key].cget("text"))
        self.assertTrue(self.ui.feature_vars["pause_on_foreign_input"].get())
        self.assertFalse(self.ui.feature_vars["handover_enabled"].get())

    def test_handover_texts_keep_an_edit_and_forget_the_default(self):
        self.ui.cfg["handover_request_text"] = "Zrób handover po swojemu."
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["handover_request_text"], "Zrób handover po swojemu.")
        self.ui.cfg["handover_request_text"] = ""
        self.ui._push_config()
        self.assertEqual(self.saved[-1]["handover_request_text"], "")

    def test_paused_state_names_who_holds_the_mouse(self):
        self.ui._handle_event("state", dict(state="PAUSED", pause_reason="claude_cu",
                                            reset_at=None, send_at=None, start_until=None))
        self.assertIn("Claude", self.ui.lbl_state.cget("text"))
        self.ui._update_caption()
        self.assertTrue(self.ui.lbl_caption.cget("text"))
        self.ui._handle_event("state", dict(state="PAUSED", pause_reason="user",
                                            reset_at=None, send_at=None, start_until=None))
        self.assertIn("mouse", self.ui.lbl_state.cget("text"))

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


class WindowListTests(unittest.TestCase):
    """The windows each Auto-Resume offers to watch. While Claude drives the computer it
    draws a click-through overlay over the whole screen; reading it would show nothing."""

    @staticmethod
    def desktop(*windows):
        children = [types.SimpleNamespace(ClassName="Chrome_WidgetWin_1", Name=name, ProcessId=7,
                                          NativeWindowHandle=hwnd,
                                          BoundingRectangle=types.SimpleNamespace(left=0, top=0, right=w, bottom=h))
                    for hwnd, name, (w, h) in windows]
        return types.SimpleNamespace(GetChildren=lambda: children)

    def test_claude_does_not_offer_its_overlay(self):
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        desktop = self.desktop((4522860, "Claude", (2560, 1440)), (67582, "Claude", (536, 728)))
        with patch.object(app.auto, "GetRootControl", return_value=desktop), \
                patch.object(app, "process_exe_name", return_value="claude.exe"), \
                patch.object(app, "click_through", side_effect=lambda hwnd: hwnd == 4522860):
            self.assertEqual(worker._enum_windows(), [(67582, "Claude")])

    def test_chatgpt_does_not_offer_an_overlay_either(self):
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.DEFAULT_CONFIG))
        desktop = self.desktop((1, "ChatGPT", (2560, 1440)), (2, "ChatGPT", (1280, 1392)))
        with patch.object(gpt.auto, "GetRootControl", return_value=desktop), \
                patch.object(gpt.app, "process_exe_name", return_value="chatgpt.exe"), \
                patch.object(gpt.app, "click_through", side_effect=lambda hwnd: hwnd == 1):
            self.assertEqual(worker._enum_windows(), [(2, "ChatGPT")])


class WindowClaimTests(unittest.TestCase):
    """Two copies must never watch the same window: both would type into the same
    conversation. Copies watching different windows (a second Claude window, or Claude
    and ChatGPT) run side by side, as before."""

    def worker(self, hwnd):
        w = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG, start_delay_s=0, keep_awake=False))
        w.hwnd, w.logged = hwnd, []
        w.log = lambda key, level="info", **kw: w.logged.append(key)
        self.addCleanup(w.release_window)
        return w

    def hwnd(self, offset=0):
        return 1_000_000 + os.getpid() % 100_000 * 10 + offset      # not a real window's handle

    def test_a_window_another_copy_watches_is_refused(self):
        first, second = self.worker(self.hwnd()), self.worker(self.hwnd())
        first._begin()
        self.assertEqual(first.state, first.MONITORING)
        second._begin()
        self.assertEqual(second.state, second.IDLE)
        self.assertIn("log_window_taken", second.logged)
        self.assertNotEqual(app.tr("pl", "log_window_taken"), "log_window_taken")

    def test_stopping_frees_the_window_for_another_copy(self):
        first, second = self.worker(self.hwnd(1)), self.worker(self.hwnd(1))
        first._begin()
        first.command("stop")
        first._process_commands()
        second._begin()
        self.assertEqual(second.state, second.MONITORING)

    def test_a_new_copy_picks_a_window_nobody_watches(self):
        first = self.worker(self.hwnd(2))
        first._begin()
        second = self.worker(None)
        windows = [(self.hwnd(2), "Claude"), (self.hwnd(3), "Claude")]
        self.assertEqual(second._pick_window(windows), self.hwnd(3))
        self.assertEqual(second._pick_window(windows[:1]), self.hwnd(2))   # the only one: shown, not watched

    def test_resume_now_is_refused_for_a_window_another_copy_watches(self):
        first, second = self.worker(self.hwnd(6)), self.worker(self.hwnd(6))
        first._begin()
        read = []
        second._refresh = lambda: read.append(True) or []
        second.command("send_now")
        second._process_commands()
        self.assertIn("log_window_taken", second.logged)
        self.assertEqual(read, [])                     # it did not even look for chats to resume

    def test_switching_to_a_watched_window_stops_the_watch(self):
        first, second = self.worker(self.hwnd(4)), self.worker(self.hwnd(5))
        first._begin()
        second._begin()
        second._refresh = lambda: None
        second.command("select_window", self.hwnd(4))
        second._process_commands()
        self.assertEqual(second.state, second.IDLE)
        self.assertIn("log_window_taken", second.logged)


class ErrorTextTests(unittest.TestCase):
    """The program's own error messages reach the log in the window's language."""
    RAISED = {"RuntimeError", "HandoverRefused", "InputPaused", "InputBusy", "WindowHidden"}

    def raised(self, name):
        with open(os.path.join(app.APP_DIR, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        messages = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and getattr(node.exc.func, "id", "") in self.RAISED):
                argument = node.exc.args[0]
                self.assertIsInstance(argument, ast.Constant,
                                      f"{name}:{node.lineno} builds its message, so it cannot be translated")
                messages.append(argument.value)
        return messages

    def test_every_error_the_program_raises_has_a_polish_text(self):
        for name in ("session_automation.py", "chatgpt_automation.py", "input_lock.py"):
            messages = self.raised(name)
            self.assertTrue(messages, name)
            for message in messages:
                self.assertEqual(app.tr("en", message), message)
                self.assertNotEqual(app.tr("pl", message), message, (name, message))

    def test_the_chatgpt_window_names_itself_in_shared_errors(self):
        self.assertEqual(app.tr("en", "Claude is not foreground", table=gpt.STRINGS), "ChatGPT is not foreground")
        self.assertIn("ChatGPT", app.tr("pl", "Claude window is unavailable", table=gpt.STRINGS))


if __name__ == "__main__":
    unittest.main()
