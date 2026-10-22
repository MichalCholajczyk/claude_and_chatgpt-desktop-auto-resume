"""The next-step box and the new-session cards of Claude Desktop 2.37 (measured live on
16.10): reading them, pressing them, and when the program may. Every tree here copies a
measured one; nothing touches a real window."""
import contextlib
import datetime as dt
import itertools
import json
import os
import queue
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app
from session_automation import (MAX_STARTS, ClaudeUI, SessionEngine, SessionState, next_step_in,
                                session_offer_in, signals)
from test_sessions import NOW, FakeQuota, FixtureUI, node, observed, pane
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


def control(shortcut=""):
    return types.SimpleNamespace(AcceleratorKey=shortcut, IsEnabled=True, IsOffscreen=False)


def next_step_box(p, labels=("Tak, dalej",), shortcuts=(0,), question="Który wariant?", turn=None):
    """The box Claude Desktop draws when a turn ends with a question (16.10): in the last
    message, a group with the recap, the question and "Suggested next actions" holding the
    buttons. The main one carries Ctrl+Enter as its accelerator key."""
    turn = turn or entry(p)
    box = node(parent=turn)
    node("Test kroku 2.", "TextControl", node(parent=box))
    node(question, "TextControl", node(parent=box))
    actions = node("Suggested next actions", parent=box)
    for i, label in enumerate(labels):
        node(label, "ButtonControl", actions).control = control("Control+Enter" if i in shortcuts else "")
    return box


def suggestion_card(p, title="Poprawki drobnych uwag", turn=None, tasks=1):
    """A new session the agent proposes by itself (16.10): "New session", the title as a
    button that unfolds the brief, "Dismiss suggestion <title>", Options, Do it here, Start."""
    turn = turn or entry(p)
    group = node("%d suggested task%s" % (tasks, "" if tasks == 1 else "s"), parent=node(parent=turn))
    for _ in range(tasks):
        node("New session", "TextControl", group)
        unfold = node(title, "ButtonControl", group)
        node(title, "TextControl", node(parent=unfold))
        node("Opis zadania.", "TextControl", node(parent=unfold))
        node("Dismiss suggestion " + title, "ButtonControl", group)
        for label in ("Options", "Do it here", "Start"):
            node(label, "ButtonControl", group).control = control()
    return group


def offer_card(p, title="Test karty: odpowiedz OK", turn=None):
    """The agent asks whether to start a task in a session of its own and waits (16.10)."""
    turn = turn or entry(p)
    group = node("1 suggested task", parent=node(parent=turn))
    node("Start this in its own session?", "TextControl", group)
    node(title, "TextControl", group)
    for label, key in (("Options", ""), ("Don’t ask again", ""), ("Do it here", "Escape"),
                       ("New session", "Control+Enter")):
        node(label, "ButtonControl", group).control = control(key)
    return group


def chat(title="Alpha"):
    """A conversation as 2.37 lists it (16.10): the entries in a list of their own inside
    "Chat messages", your message first."""
    p = pane(title)
    feed = next(n for n in p.root.walk() if n.name == "Chat messages")
    node(parent=feed)
    entry(p, ("You said: This is a short UI test", "TextControl"))
    return p


def entry(p, *children):
    """One more entry at the end of the transcript, holding (name, control type) children."""
    entries = next(n for n in p.root.walk() if n.name == "Chat messages").children[0]
    item = node(parent=entries)
    for name, kind in children:
        node(name, kind, item)
    return item


class NextStepDetectionTests(unittest.TestCase):
    def test_a_single_button_is_the_primary(self):
        p = chat()
        box = next_step_box(p)
        found = next_step_in(p)
        self.assertIs(found["group"], box)
        self.assertEqual(found["label"], "Tak, dalej")
        self.assertEqual([b.name for b in found["buttons"]], ["Tak, dalej"])

    def test_the_shortcut_marks_the_primary(self):
        p = chat()
        next_step_box(p, ("Wariant A", "Wariant B"), shortcuts=(1,))
        self.assertEqual(next_step_in(p)["label"], "Wariant B")

    def test_without_a_shortcut_the_first_button_is_primary(self):
        p = chat()
        next_step_box(p, ("Wariant A", "Wariant B"), shortcuts=())
        self.assertEqual(next_step_in(p)["primary"].name, "Wariant A")

    def test_two_shortcuts_are_not_a_box(self):
        p = chat()
        next_step_box(p, ("Wariant A", "Wariant B"), shortcuts=(0, 1))
        self.assertIsNone(next_step_in(p))

    def test_an_app_action_is_marked(self):
        for labels in (("Commit",), ("Create PR", "Nie teraz"), ("merge",)):
            with self.subTest(labels=labels):
                p = chat()
                next_step_box(p, labels)
                self.assertTrue(next_step_in(p)["action"])

    def test_a_reply_is_not_an_app_action(self):
        for label in ("Tak, działaj", "Wariant A", "Dalej", "Pokaż ci wynik", "Przeprowadź testy"):
            with self.subTest(label=label):
                p = chat()
                next_step_box(p, (label,))
                self.assertFalse(next_step_in(p)["action"])

    def test_doubtful_app_action_names_are_held_back(self):
        # The app's own labels were never measured (§6): anything that reads like one stays
        # with the person, a reply about committing included.
        for label in ("Commit changes", "Create pull request", "Merge PR", "Archive session", "Fix CI",
                      "Resolve conflicts", "Address comments", "Verify", "New session", "Tak, zacommituj"):
            with self.subTest(label=label):
                p = chat()
                next_step_box(p, (label,))
                self.assertTrue(next_step_in(p)["action"])

    def test_a_box_before_your_last_message_is_old(self):
        p = chat()
        next_step_box(p)
        entry(p, ("You said: Krok 2", "TextControl"))
        self.assertIsNone(next_step_in(p))

    def test_no_box_no_next_step(self):
        p = chat()
        entry(p, ("Gotowe.", "TextControl"))
        self.assertIsNone(next_step_in(p))

    def test_same_box_same_fingerprint(self):
        first, second, other = chat(), chat(), chat()
        next_step_box(first, ("Wariant A", "Wariant B"))
        next_step_box(second, ("Wariant A", "Wariant B"))
        next_step_box(other, ("Wariant A", "Wariant C"))
        self.assertEqual(next_step_in(first)["fingerprint"], next_step_in(second)["fingerprint"])
        self.assertNotEqual(next_step_in(first)["fingerprint"], next_step_in(other)["fingerprint"])


class SessionOfferDetectionTests(unittest.TestCase):
    def test_a_suggestion(self):
        p = chat()
        suggestion_card(p)
        card = session_offer_in(p)
        self.assertEqual((card["kind"], card["title"], card["start"].name, card["here"].name),
                         ("suggestion", "Poprawki drobnych uwag", "Start", "Do it here"))

    def test_an_offer(self):
        p = chat()
        offer_card(p)
        card = session_offer_in(p)
        self.assertEqual((card["kind"], card["title"], card["start"].name, card["here"].name),
                         ("offer", "Test karty: odpowiedz OK", "New session", "Do it here"))

    def test_two_tasks_in_one_group_are_left_alone(self):
        p = chat()
        suggestion_card(p, tasks=2)
        self.assertIsNone(session_offer_in(p))

    def test_a_card_before_your_last_message_is_old(self):
        p = chat()
        offer_card(p)
        entry(p, ("You said: dalej", "TextControl"))
        self.assertIsNone(session_offer_in(p))

    def test_same_card_same_fingerprint(self):
        first, second, other = chat(), chat(), chat()
        offer_card(first)
        offer_card(second)
        offer_card(other, "Inne zadanie")
        self.assertEqual(session_offer_in(first)["fingerprint"], session_offer_in(second)["fingerprint"])
        self.assertNotEqual(session_offer_in(first)["fingerprint"], session_offer_in(other)["fingerprint"])


class CardSignalsTests(unittest.TestCase):
    def test_signals_carry_both_cards(self):
        p = chat()
        turn = entry(p)
        suggestion_card(p, turn=turn)
        next_step_box(p, turn=turn)
        state = signals(p, app.detect_limit_banner)
        self.assertEqual(state["next_step"]["label"], "Tak, dalej")
        self.assertEqual(state["offer"]["kind"], "suggestion")

    def test_card_texts_are_not_a_limit_or_an_error(self):
        # The box's question is the last text of the transcript, where an API error
        # would be: an agent asking about one must not be taken for one.
        p = chat()
        next_step_box(p, ("Spróbuj ponownie",), question="Something went wrong — spróbować jeszcze raz?")
        state = signals(p, app.detect_limit_banner)
        self.assertFalse(state["limit"])
        self.assertIsNone(state["error"])
        self.assertIsNone(state["retry"])


class PressCardTests(unittest.TestCase):
    """Invoke first (no focus change, no mouse), a real click only when Claude ignored it;
    never into a conversation with a draft, never a card that is gone."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.pane = chat()
        self.ui = ClaudeUI(self.worker)
        self.ui.resolve = lambda *a, **kw: self.pane
        self.ui.value = lambda n: ""
        self.pressed = []
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def show(self, build=next_step_box, accepts=("invoke", "click"), **kwargs):
        """Put a card on screen whose buttons react to the given kinds of press."""
        card = build(self.pane, **kwargs)

        def press(kind, button):
            self.pressed.append((kind, button.name))
            if kind in accepts and card in card.parent.children:
                card.parent.children.remove(card)
        for button in [n for n in card.walk() if n.type == "ButtonControl"]:
            button.control.GetInvokePattern = (
                lambda b=button: types.SimpleNamespace(Invoke=lambda: press("invoke", b)))
        self.ui.click = lambda n: press("click", n)
        return card

    def test_invoke_presses_the_primary(self):
        self.show(labels=("Wariant A", "Wariant B"))
        self.assertTrue(self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary"))
        self.assertEqual(self.pressed, [("invoke", "Wariant A")])

    def test_ignored_invoke_falls_back_to_a_real_click(self):
        self.show(labels=("Wariant A", "Wariant B"), accepts=("click",))
        self.assertTrue(self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary"))
        self.assertEqual(self.pressed, [("invoke", "Wariant A"), ("click", "Wariant A")])

    def test_a_card_that_stays_is_reported(self):
        self.show(accepts=())
        with self.assertRaisesRegex(RuntimeError, "not accepted"):
            self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary")
        self.assertEqual(self.pressed, [("invoke", "Tak, dalej"), ("click", "Tak, dalej")])

    def test_a_card_that_changed_is_not_pressed(self):
        box = self.show()
        seen = next_step_in(self.pane)
        box.parent.children.remove(box)          # someone answered it in the meantime
        self.assertFalse(self.ui.press_card(self.pane.key, next_step_in, seen, "primary"))
        self.assertEqual(self.pressed, [])

    def test_a_draft_blocks_the_press(self):
        self.show()
        self.ui.value = lambda n: "mój szkic"
        with self.assertRaisesRegex(RuntimeError, "Existing draft"):
            self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary")
        self.assertEqual(self.pressed, [])

    def test_offer_buttons(self):
        for button, label in (("start", "New session"), ("here", "Do it here")):
            with self.subTest(button=button):
                self.pane, self.pressed = chat(), []
                self.show(offer_card)
                self.assertTrue(self.ui.press_card(self.pane.key, session_offer_in,
                                                   session_offer_in(self.pane), button))
                self.assertEqual(self.pressed, [("invoke", label)])

    def test_a_button_below_the_visible_part_is_scrolled_into_view_first(self):
        # Live, 16.10: in a narrow split the box lay under the pane's lower edge (offscreen),
        # so it could not be pressed until scrolled into view.
        self.show()
        button = next_step_in(self.pane)["primary"]
        button.control.IsOffscreen = True
        scrolled = []

        def scroll():
            scrolled.append(button.name)
            button.control.IsOffscreen = False

        def pattern(pattern_id):
            self.assertEqual(pattern_id, app.auto.PatternId.ScrollItemPattern)
            return types.SimpleNamespace(ScrollIntoView=scroll)
        button.control.GetPattern = pattern
        self.assertTrue(self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary"))
        self.assertEqual(scrolled, ["Tak, dalej"])
        self.assertEqual(self.pressed, [("invoke", "Tak, dalej")])

    def test_nothing_is_pressed_while_a_stop_is_pending(self):
        self.show()
        self.worker._stop_event.set()
        with self.assertRaisesRegex(RuntimeError, "Pending command"):
            self.ui.press_card(self.pane.key, next_step_in, next_step_in(self.pane), "primary")
        self.assertEqual(self.pressed, [])


def sidebar(*rows):
    """A window whose sidebar lists these rows; a row is its button's name and its title."""
    root = node()
    bar = node("Sidebar", parent=root)
    for name, title in rows:
        row = node(parent=bar)
        node(name, "ButtonControl", row)
        node("More options for " + title, "ButtonControl", row)
    return root


class StartedKeyTests(unittest.TestCase):
    TITLE = "Test karty: odpowiedz OK"

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.ui = ClaudeUI(self.worker)
        for name, fake in (("sleep", lambda s: None), ("monotonic", itertools.count().__next__)):
            patcher = patch("session_automation.time." + name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def started(self, *roots):
        """started_key over these reads of the window, the last one repeating."""
        reads = list(roots)
        self.ui.snapshot = lambda: reads.pop(0) if len(reads) > 1 else reads[0]
        return self.ui.started_key(self.TITLE, wait_s=10)

    def test_the_new_row_is_found(self):
        root = sidebar((self.TITLE + " Started by Claude from Pomiar kart", self.TITLE))
        self.assertEqual(self.started(root), "code:" + self.TITLE)

    def test_a_suggestion_row_is_found(self):
        root = sidebar((self.TITLE + " Started from a suggestion in Pomiar kart", self.TITLE))
        self.assertEqual(self.started(root), "code:" + self.TITLE)

    def test_an_old_row_without_started_is_not_it(self):
        self.assertIsNone(self.started(sidebar((self.TITLE, self.TITLE))))

    def test_the_row_may_come_later(self):
        later = sidebar(("Inna", "Inna"), (self.TITLE + " Started by Claude from Pomiar kart", self.TITLE))
        self.assertEqual(self.started(sidebar(("Inna", "Inna")), later), "code:" + self.TITLE)


class CardsUI(FixtureUI):
    """Records what the engine presses; `result` is what press_card answers."""

    def __init__(self):
        super().__init__()
        self.pressed = []
        self.result = True
        self.error = None
        self.clicks = 0
        self.new_key = "code:New"

    def press_card(self, key, find, card, button):
        self.pressed.append((key, find.__name__, card["fingerprint"], button))
        if self.error:
            raise self.error
        if self.result:
            self.clicks += 1
        return self.result

    def started_key(self, title, wait_s=None):
        self.calls.append(("started_key", title))
        return self.new_key


def box(label="Tak, działaj", fingerprint="b1", action=False):
    return dict(label=label, action=action, fingerprint=fingerprint)


def offer(kind="offer", title="Test karty", fingerprint="o1"):
    return dict(kind=kind, title=title, fingerprint=fingerprint)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.logged = []
        self.worker.log = lambda key, level="info", **kw: self.logged.append(self.worker.t(key, **kw))
        self.ui = CardsUI()
        self.engine = SessionEngine(self.worker, self.ui)
        self.state = SessionState()

    def on(self, **cfg):
        self.worker.cfg.update(dict(auto_next_step=True, auto_sessions=True), **cfg)

    def step(self, state=None, **seen):
        self.engine.step("code:A", state or self.state, observed(**seen), NOW)

    def buttons(self):
        return [p[3] for p in self.ui.pressed]

    def emitted(self, kind):
        events = []
        while not self.worker.out.empty():
            events.append(self.worker.out.get())
        return [data for k, data in events if k == kind]


class CardPolicyTests(EngineCase):
    def test_off_by_default(self):
        self.assertFalse(app.DEFAULT_CONFIG["auto_next_step"])
        self.assertFalse(app.DEFAULT_CONFIG["auto_sessions"])
        self.assertEqual(app.DEFAULT_CONFIG["session_choice"], "start")
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.assertEqual(self.ui.pressed, [])

    def test_the_box_is_pressed_and_logged(self):
        self.on()
        self.step(next_step=box())
        self.assertEqual(self.ui.pressed, [("code:A", "next_step_in", "b1", "primary")])
        self.assertEqual(self.logged, ["A: Clicked the next step — Tak, działaj"])

    def test_an_app_action_is_left_for_you(self):
        self.on()
        self.step(next_step=box("Commit", action=True))
        self.step(next_step=box("Commit", action=True))
        self.assertEqual(self.ui.pressed, [])
        self.assertEqual(self.logged, ["A: Next step left for you — Commit"])

    def test_send_automatically_off_blocks_cards(self):
        self.on(auto_send=False)
        self.step(next_step=box(), offer=offer())
        self.assertEqual(self.ui.pressed, [])

    def test_no_click_while_the_limit_holds(self):
        # A wrap-up at the limit may end with a box: the limit's own resume answers it.
        self.on()
        self.step(next_step=box(), limit=True, wrap_up=True, reset=NOW + dt.timedelta(hours=1))
        self.assertEqual(self.ui.pressed, [])
        self.assertEqual((self.state.phase, self.state.reason), ("waiting", "limit"))

    def test_no_click_on_an_error_or_retry(self):
        self.on()
        for seen in (dict(error="API Error: 529"), dict(retry=object())):
            with self.subTest(seen=seen):
                self.step(SessionState(), next_step=box(), **seen)
                self.assertEqual(self.ui.pressed, [])

    def test_no_click_while_the_chat_works(self):
        self.on()
        self.step(next_step=box(), busy=True)
        self.assertEqual(self.ui.pressed, [])

    def test_no_click_during_a_handover(self):
        self.on()
        self.engine.handover.state_for("code:A").phase = "requested"
        self.step(next_step=box(), offer=offer())
        self.assertEqual(self.ui.pressed, [])

    def test_no_click_outside_watching(self):
        self.on()
        self.step(SessionState("verifying", "limit", NOW + dt.timedelta(seconds=30)), next_step=box())
        self.assertEqual(self.ui.pressed, [])

    def test_one_press_per_box(self):
        self.on()
        self.step(next_step=box())
        self.step(next_step=box())
        self.assertEqual(len(self.ui.pressed), 1)

    def test_the_same_box_in_a_later_turn_is_pressed_again(self):
        self.on()
        self.step(next_step=box("Tak"))
        self.step()
        self.step(next_step=box("Tak"))
        self.assertEqual(self.buttons(), ["primary", "primary"])

    def test_nothing_pressed_allows_a_later_try(self):
        self.on()
        self.ui.result = False
        self.step(next_step=box())
        self.assertEqual(self.state.box_id, "")
        self.assertEqual(self.logged, [])
        self.step(next_step=box())
        self.assertEqual(len(self.ui.pressed), 2)

    def test_a_failed_press_without_clicks_is_tried_again(self):
        self.on()
        self.ui.error = RuntimeError("Existing draft; leaving it untouched")
        with self.assertRaisesRegex(RuntimeError, "Existing draft"):
            self.step(next_step=box())
        self.assertEqual(self.state.box_id, "")

    def test_a_failed_press_after_a_click_is_not_repeated(self):
        self.on()

        def clicked_then_failed(*args):
            self.ui.pressed.append(args)
            self.ui.clicks += 1
            raise RuntimeError("Card click was not accepted")
        self.ui.press_card = clicked_then_failed
        with self.assertRaises(RuntimeError):
            self.step(next_step=box())
        self.step(next_step=box())
        self.assertEqual(len(self.ui.pressed), 1)

    def test_an_offer_is_answered_while_the_chat_shows_stop(self):
        self.on()
        self.step(offer=offer(kind="offer"), busy=True)
        self.assertEqual(self.ui.pressed, [("code:A", "session_offer_in", "o1", "start")])
        self.assertIn(("started_key", "Test karty"), self.ui.calls)
        self.assertIn("A: Started a new session — Test karty", self.logged)

    def test_a_suggestion_waits_while_the_chat_works(self):
        self.on()
        self.step(offer=offer(kind="suggestion"), busy=True)
        self.assertEqual(self.ui.pressed, [])

    def test_do_it_here_when_chosen(self):
        self.on(session_choice="here")
        self.step(offer=offer(kind="suggestion"))
        self.assertEqual(self.buttons(), ["here"])
        self.assertNotIn(("started_key", "Test karty"), self.ui.calls)
        self.assertEqual(self.logged, ["A: Clicked Do it here — Test karty"])

    def test_at_most_five_starts_per_run(self):
        self.on()
        for i in range(MAX_STARTS + 1):
            self.step(offer=offer(fingerprint="o%d" % i, title="Zadanie %d" % i))
        self.assertEqual(MAX_STARTS, 5)
        self.assertEqual(self.buttons(), ["start"] * 5)
        self.assertEqual(self.logged[-1], "A: New session left for you (limit of 5 reached) — Zadanie 5")

    def test_the_limit_does_not_count_do_it_here(self):
        self.on(session_choice="here")
        for i in range(MAX_STARTS + 2):
            self.step(offer=offer(kind="suggestion", fingerprint="o%d" % i))
        self.assertEqual(self.buttons(), ["here"] * 7)
        self.assertEqual(self.engine.started, 0)

    def test_reset_clears_the_start_count(self):
        self.engine.started = MAX_STARTS
        self.engine.reset()
        self.assertEqual(self.engine.started, 0)

    def test_a_card_comes_before_the_box(self):
        self.on()
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.assertEqual(self.buttons(), ["start"])

    def test_a_capped_card_lets_the_box_through(self):
        self.on()
        self.engine.started = MAX_STARTS
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.assertEqual(self.buttons(), ["primary"])
        self.assertEqual(self.logged, ["A: New session left for you (limit of 5 reached) — Test karty",
                                       "A: Clicked the next step — Tak, działaj"])

    def test_a_due_handover_goes_first(self):
        # The handover's request is the next message: pressing the box too would send two.
        self.on(handover_enabled=True, handover_threshold="700k")
        self.step(next_step=box(), offer=offer(kind="suggestion"),
                  context=dict(used=800_000, window=1_000_000, pct=80), last_text="")
        self.assertEqual(self.ui.pressed, [])

    def test_a_capped_card_beside_a_box_left_for_you_logs_once(self):
        self.on()
        self.engine.started = MAX_STARTS
        for _ in range(4):
            self.step(next_step=box("Commit", action=True), offer=offer(kind="suggestion"))
        self.assertEqual(self.ui.pressed, [])
        self.assertEqual(self.logged, ["A: New session left for you (limit of 5 reached) — Test karty",
                                       "A: Next step left for you — Commit"])

    def test_a_pressed_card_is_not_pressed_again_after_the_box(self):
        # A suggestion that stayed on screen after Start must not be started twice once the
        # box beside it has been pressed.
        self.on()
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.step(next_step=box(), offer=offer(kind="suggestion"))
        self.assertEqual(self.buttons(), ["start", "primary"])

    def test_chatgpt_observations_without_cards_still_work(self):
        self.on()
        self.step()
        self.assertEqual(self.ui.pressed, [])

    def test_unknown_session_choice_falls_back_to_start(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "config.json")
        for saved, loaded in (("x", "start"), ("here", "here")):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"session_choice": saved}, f)
            self.assertEqual(app.load_config(path)["session_choice"], loaded)


class FollowStartedTests(EngineCase):
    def test_open_scope_turns_into_checks_with_the_new_session(self):
        self.engine.watched = ["code:A", "code:B"]
        self.engine.follow_started("code:A", "code:New", "New")
        self.assertEqual(self.worker.cfg["watch_scope"], "selected")
        self.assertEqual(self.worker.cfg["selected_chats"], ["code:A", "code:B", "code:New"])
        self.assertEqual(self.emitted("selection"), [["code:A", "code:B", "code:New"]])
        self.assertEqual(self.logged, ["A: also watching the new session “New”",
                                       "A: now watching the checked conversations (3) and visiting them "
                                       "from the sidebar"])

    def test_selected_scope_gets_the_new_session_added(self):
        self.worker.cfg.update(watch_scope="selected", selected_chats=["code:A"])
        self.engine.watched = ["code:A", "code:B"]
        self.engine.follow_started("code:A", "code:New", "New")
        self.assertEqual(self.worker.cfg["selected_chats"], ["code:A", "code:New"])
        self.assertEqual(self.logged, ["A: also watching the new session “New”"])

    def test_a_session_that_did_not_show_up_is_only_logged(self):
        self.engine.follow_started("code:A", None, "Nowa")
        self.assertEqual(self.worker.cfg["watch_scope"], "open")
        self.assertEqual(self.emitted("selection"), [])
        self.assertEqual(self.logged,
                         ["A: the new session “Nowa” has not shown up in the sidebar yet — "
                          "it will be watched as soon as it does"])

    def test_a_started_session_is_followed(self):
        self.on()
        self.engine.watched = ["code:A"]
        self.step(offer=offer())
        self.assertEqual(self.worker.cfg["selected_chats"], ["code:A", "code:New"])


def started_row(bar, title, subtitle="", status=""):
    """A sidebar row as measured on 20.10: the button holds the session's title, the line a
    session started from another one gets under it ("Started by Claude from <title>") and
    a status bar ("Running", "Unread response" or nothing); "More options for <title>"
    sits beside it."""
    row = node(parent=bar)
    button = node(" ".join(filter(None, (title, subtitle))), "ButtonControl", row)
    texts = node(parent=button)
    node(title, "TextControl", texts)
    if subtitle:
        node(subtitle, "TextControl", texts)
    status_bar = node("", "StatusBarControl", texts)
    if status:
        node(status, "TextControl", status_bar)
    node("More options for " + title, "ButtonControl", node(parent=row))
    return row


class WindowUI:
    """A Claude window read again on every scan: `rows` are the sidebar rows under the
    conversations of `open`, each (title, subtitle, status)."""

    def __init__(self):
        self.open, self.rows = [], []

    def snapshot(self):
        root = node()
        for title in self.open:
            pane(title, parent=root)
        bar = node("Sidebar", parent=root)
        for title in self.open:
            started_row(bar, title)
        for row in self.rows:
            started_row(bar, *row)
        return root


class StartedElsewhereTests(unittest.TestCase):
    """A session started from a watched conversation is watched too, whoever started it:
    the program, you or Claude itself. Live on 20.10 "RIR podopiecznych w widokach trenera"
    was started by hand from "Historia ćwiczeń i skala RIR" before watching began, and
    waited for an answer that nobody gave."""
    PARENT, CHILD = "Historia ćwiczeń i skala RIR", "RIR podopiecznych w widokach trenera"
    FROM_PARENT = "Started by Claude from " + PARENT

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.logged = []
        self.worker.log = lambda key, level="info", **kw: self.logged.append(self.worker.t(key, **kw))
        self.window = WindowUI()
        self.window.open = [self.PARENT]
        self.engine = SessionEngine(self.worker, self.window, quota=FakeQuota())
        self.scanned = []
        self.engine.scan_one = lambda key, state, now: self.scanned.append(key)
        # The running Auto-Resume holds this lock while it acts: the tests stay off it.
        patcher = patch("session_automation.input_lock", lambda cancelled: contextlib.nullcontext())
        patcher.start()
        self.addCleanup(patcher.stop)

    def scan(self, times=1):
        for _ in range(times):
            self.engine.next_scan = 0
            self.engine.tick(NOW)

    def child(self, subtitle=FROM_PARENT, status="Running", title=CHILD):
        self.window.rows.append((title, subtitle, status))

    def watching(self):
        return self.worker.cfg["watch_scope"], self.worker.cfg["selected_chats"]

    def test_a_session_started_while_watching_is_watched_too(self):
        self.scan()
        self.child()
        self.scan(3)
        self.assertEqual(self.watching(), ("selected", ["code:" + self.PARENT, "code:" + self.CHILD]))
        self.assertEqual(self.logged, [
            "Historia ćwiczeń i skala RIR: also watching the new session “RIR podopiecznych w widokach trenera”",
            "Historia ćwiczeń i skala RIR: now watching the checked conversations (2) and visiting them "
            "from the sidebar"])
        # Round robin: the new session is scanned on the very next scan.
        self.assertEqual(self.scanned, ["code:" + self.PARENT, "code:" + self.CHILD,
                                        "code:" + self.PARENT, "code:" + self.CHILD])

    def test_the_window_checks_the_new_session(self):
        self.scan()
        self.child()
        self.scan()
        events = []
        while not self.worker.out.empty():
            events.append(self.worker.out.get())
        self.assertEqual([data for kind, data in events if kind == "selection"],
                         [["code:" + self.PARENT, "code:" + self.CHILD]])

    def test_a_session_from_a_suggestion_is_watched_too(self):
        self.scan()
        self.child("Started from a suggestion in " + self.PARENT)
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])

    def test_one_waiting_for_you_before_watching_began_is_watched_from_the_first_scan(self):
        self.child(status="Needs input")
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])

    def test_one_still_running_before_watching_began_is_watched_too(self):
        self.child(status="Running")
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])

    def test_finished_sessions_from_before_are_left_alone(self):
        self.child(status="Unread response")
        self.child(status="", title="Starsza sesja")
        self.scan(3)
        self.assertEqual(self.watching(), ("open", []))
        self.assertEqual(self.logged, [])

    def test_a_new_one_counts_even_when_it_has_already_finished(self):
        self.scan()
        self.child(status="Unread response")
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])

    def test_a_session_started_from_a_chat_not_watched_is_not(self):
        self.scan()
        self.child("Started by Claude from Inna rozmowa")
        self.scan(2)
        self.assertEqual(self.watching(), ("open", []))

    def test_with_checked_chats_a_closed_parent_counts(self):
        self.worker.cfg.update(watch_scope="selected", selected_chats=["code:" + self.PARENT])
        self.window.open = []
        self.window.rows = [(self.PARENT, "", "")]
        self.scan()
        self.child()
        self.scan()
        self.assertEqual(self.watching(), ("selected", ["code:" + self.PARENT, "code:" + self.CHILD]))
        self.assertEqual(self.logged, [
            "Historia ćwiczeń i skala RIR: also watching the new session “RIR podopiecznych w widokach trenera”"])

    def test_a_session_watched_already_is_not_added_again(self):
        self.window.open = [self.PARENT, self.CHILD]
        self.child(status="Needs input")
        self.scan(2)
        self.assertEqual(self.watching(), ("open", []))
        self.assertEqual(self.logged, [])

    def test_unchecking_a_followed_session_is_final(self):
        self.scan()
        self.child()
        self.scan()
        self.worker.cfg["selected_chats"] = ["code:" + self.PARENT]      # unchecked in the window
        self.scan(2)
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT])

    def test_unchecking_one_the_program_started_is_final_too(self):
        self.scan()
        self.engine.follow_started("code:" + self.PARENT, "code:" + self.CHILD, self.CHILD)
        self.child()
        self.worker.cfg["selected_chats"] = ["code:" + self.PARENT]
        self.scan(2)
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT])

    def test_one_that_showed_up_late_after_a_start_is_watched_when_it_does(self):
        self.scan()
        self.engine.follow_started("code:" + self.PARENT, None, self.CHILD)
        self.child()
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])

    def test_a_new_run_looks_at_the_sidebar_afresh(self):
        self.scan()
        self.engine.reset()
        self.child(status="Unread response")     # appeared between the runs, finished
        self.scan(2)
        self.assertEqual(self.watching(), ("open", []))

    def test_a_new_run_may_follow_again_what_you_unchecked(self):
        self.scan()
        self.child()
        self.scan()
        self.worker.cfg.update(watch_scope="open", selected_chats=[])     # all unchecked, then Stop
        self.engine.reset()                                                # Start: a new run
        self.scan()
        self.assertEqual(self.watching()[1], ["code:" + self.PARENT, "code:" + self.CHILD])


class CardOptionsTests(unittest.TestCase):
    def test_the_claude_window_offers_both_options_with_help(self):
        for key in ("auto_next_step", "auto_sessions"):
            self.assertIn(key, app.App.FEATURES)
        self.assertEqual(app.tr("en", "auto_next_step"), "Accept the next step automatically")
        self.assertEqual(app.tr("pl", "auto_next_step"), "Automatycznie akceptuj następny krok")
        self.assertEqual(app.tr("en", "auto_sessions"), "Answer suggested new sessions automatically")
        self.assertEqual(app.tr("pl", "auto_sessions"), "Automatycznie odpowiadaj na propozycje nowych sesji")
        for lang in ("en", "pl"):
            for label in ("Commit", "Create PR", "Merge"):
                self.assertIn(label, app.tr(lang, "auto_next_step_help"))
            for label in ("Start", "New session", "Do it here", "5"):
                self.assertIn(label, app.tr(lang, "auto_sessions_help"))

    def test_the_chatgpt_window_does_not(self):
        import chatgpt_auto_continue
        for key in ("auto_next_step", "auto_sessions"):
            self.assertNotIn(key, chatgpt_auto_continue.ChatGPTApp.FEATURES)

    def window(self, cls=app.App):
        saved = []
        for p in (patch.object(app, "load_config", lambda: dict(app.DEFAULT_CONFIG)),
                  patch.object(app, "save_config", lambda cfg: saved.append(dict(cfg))),
                  patch.object(app.MonitorWorker, "start", lambda w: None)):
            p.start()
            self.addCleanup(p.stop)
        ui = cls()
        self.addCleanup(ui.destroy)
        ui.withdraw()
        return ui, saved

    def test_the_choice_is_saved(self):
        ui, saved = self.window()
        self.assertEqual(ui.var_session_choice.get(), "start")
        ui.var_session_choice.set("here")
        ui._push_config()
        self.assertEqual(saved[-1]["session_choice"], "here")

    def test_the_choice_sits_next_to_its_option(self):
        ui, _ = self.window()
        labels = [w.cget("text") for w in ui.feature_checks["auto_sessions"].master.winfo_children()
                  if w.winfo_class() == "TRadiobutton"]
        self.assertEqual(labels, ["Start", "Do it here"])


if __name__ == "__main__":
    unittest.main()
