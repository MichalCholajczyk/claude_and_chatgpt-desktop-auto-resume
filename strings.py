# -*- coding: utf-8 -*-
"""UI and log texts for both Auto-Resumes (English / Polish).

Kept apart from claude_auto_continue.py: the tables are a quarter of that file and grow
with every option. ChatGPT Auto-Resume derives its own table from these (it replaces
Claude's name and overrides what differs), so every key belongs in both languages.
"""


class Text(str):
    """A translated text that remembers its key and values, so the log can show it again
    in the other language after a switch. Everywhere else it is the plain string it was
    rendered as. A key of None joins `parts` with `sep` (a list of such texts)."""

    def __new__(cls, rendered, key, kw=None):
        text = super().__new__(cls, rendered)
        text.key, text.kw = key, dict(kw or {})
        return text

    @classmethod
    def joined(cls, sep, parts):
        parts = list(parts)
        return cls(sep.join(parts), None, dict(sep=sep, parts=parts))


STRINGS = {
    "en": {
        # window states
        "state_idle": "Watch is off",
        "state_monitoring": "Watching your session",
        "state_armed": "Limit hit — waiting for reset",
        "state_verify": "Sent — checking result",
        # header captions
        "cap_click_start": "Click “Start watching”.",
        "cap_no_window": "Can’t see the Claude window — open the app and click “Refresh”.",
        "cap_guarding": "Guarding session: {session}",
        "cap_scanning": "Scanning the Claude window every {sec} s.",
        "cap_armed_reset": "Reset {reset} — I’ll send “continue” at {send}.",
        "cap_armed_noreset": "Reset time unknown — I’ll try at {send}.",
        "cap_verify": "About to check whether the session resumed.",
        # usage row
        "usage_5h_none": "5-hour limit —",
        "usage_5h": "5-hour limit {pct}%",
        "usage_weekly": "weekly {pct}%",
        "usage_fable": "Fable {pct}%",
        "usage_5h_reset": "resets {reset}",
        # section headers
        "sec_control": "CONTROL",
        "sec_log": "LOG",
        # controls
        "lbl_window": "Claude window:",
        "btn_refresh": "Refresh",
        "btn_start": "Start watching",
        "btn_stop": "Stop",
        "btn_send_now": "Send “continue” now",
        "lbl_scan_every": "Scan every",
        "lbl_seconds": "s",
        "chk_autosend": "Send automatically",
        "chk_awake": "Keep the PC awake",
        "lbl_message": "Message to send:",
        "hint_message": "drag the handle to resize — line breaks become spaces",
        "lbl_know_reset": "Know the reset time?",
        "btn_arm": "Arm",
        "lbl_arm_hint": "format HH:MM — I’ll send a minute after that time",
        "combo_handle": "{title}  (handle {hwnd})",
        # dialogs
        "dlg_send_title": "Confirm send",
        "dlg_send_body": "Type “continue” and press Enter in the Claude window now?",
        "dlg_time_title": "Invalid time",
        "dlg_time_format": "Enter the reset time as HH:MM, e.g. 15:00.",
        "dlg_time_range": "Time must be between 00:00 and 23:59.",
        # log lines
        "log_monitor_error": "Monitor ERROR: {err}",
        "log_no_window_start": "Claude window not found — open Claude and click Refresh.",
        "log_started": "Watching started.",
        "log_stopped": "Watching stopped.",
        "log_manual_send": "Resuming now, as you asked…",
        "log_armed_manual": "Armed manually: reset {reset}, send at {send}.",
        "log_autosend_off": "Send time passed, but auto-send is OFF — alert only.",
        "log_window_refound": "Claude window found again (handle {hwnd}).",
        "log_5h_hit": "5-HOUR LIMIT HIT ({pct}%). Resets {reset}. I’ll send at {send}.",
        "log_banner_hit": "“USAGE LIMIT REACHED” notice detected. Resets {reset}. I’ll send at {send}.",
        "log_5h_reset_updated": "5-hour reset updated: {reset}.",
        "log_panel_unreadable": "Couldn’t read the usage panel — will try again.",
        "log_send_fail_nowin": "SEND FAILED: no Claude window.",
        "log_send_abort_fg": "SEND ABORTED: Claude window isn’t in front (won’t type into another app).",
        "log_sent": "Sent “{message}” + Enter.",
        "log_send_fail": "SEND FAILED: {err}",
        "log_retries_done": "Out of retries — back to normal watching.",
        "log_retry_at": "Retry at {send} ({n}/{max}).",
        "log_still_done": "Limit still active, out of retries — keep watching.",
        "log_still_new": "Limit still active — new reset {reset}, send at {send}.",
        "log_still_retry": "Limit still active — retry at {send} ({n}/{max}).",
        "log_success": "SUCCESS — limit cleared, session resumed. Watching on.",
    },
    "pl": {
        "state_idle": "Czuwanie wyłączone",
        "state_monitoring": "Czuwam nad sesją",
        "state_armed": "Limit strzelony — czekam na reset",
        "state_verify": "Wysłano — sprawdzam efekt",
        "cap_click_start": "Kliknij „Rozpocznij czuwanie”.",
        "cap_no_window": "Nie widzę okna Claude — uruchom aplikację i kliknij „Odśwież”.",
        "cap_guarding": "Pilnuję sesji: {session}",
        "cap_scanning": "Skanuję okno Claude co {sec} s.",
        "cap_armed_reset": "Reset {reset} — wyślę „continue” o {send}.",
        "cap_armed_noreset": "Nie znam godziny resetu — spróbuję o {send}.",
        "cap_verify": "Za chwilę sprawdzę, czy sesja ruszyła.",
        "usage_5h_none": "limit 5-godzinny —",
        "usage_5h": "limit 5-godzinny {pct}%",
        "usage_weekly": "tygodniowy {pct}%",
        "usage_fable": "Fable {pct}%",
        "usage_5h_reset": "reset {reset}",
        "sec_control": "STEROWANIE",
        "sec_log": "DZIENNIK",
        "lbl_window": "Okno Claude:",
        "btn_refresh": "Odśwież",
        "btn_start": "Rozpocznij czuwanie",
        "btn_stop": "Zatrzymaj",
        "btn_send_now": "Wyślij „continue” teraz",
        "lbl_scan_every": "Skanuj co",
        "lbl_seconds": "s",
        "chk_autosend": "Wysyłaj automatycznie",
        "chk_awake": "Nie usypiaj komputera",
        "lbl_message": "Wysyłany tekst:",
        "hint_message": "przeciągnij uchwyt, by zmienić rozmiar — złamania linii zamieniam na spacje",
        "lbl_know_reset": "Znasz godzinę resetu?",
        "btn_arm": "Uzbrój",
        "lbl_arm_hint": "format HH:MM — wyślę minutę po tej godzinie",
        "combo_handle": "{title}  (uchwyt {hwnd})",
        "dlg_send_title": "Potwierdź wysyłkę",
        "dlg_send_body": "Wpisać „continue” i wcisnąć Enter w oknie Claude teraz?",
        "dlg_time_title": "Nieprawidłowa godzina",
        "dlg_time_format": "Wpisz godzinę resetu w formacie HH:MM, np. 15:00.",
        "dlg_time_range": "Godzina musi być z zakresu 00:00–23:59.",
        "log_monitor_error": "BŁĄD monitora: {err}",
        "log_no_window_start": "Nie znaleziono okna Claude — uruchom Claude i kliknij Odśwież.",
        "log_started": "Czuwanie uruchomione.",
        "log_stopped": "Czuwanie zatrzymane.",
        "log_manual_send": "Wznawiam teraz, na Twoją prośbę…",
        "log_armed_manual": "Uzbrojono ręcznie: reset {reset}, wysyłka {send}.",
        "log_autosend_off": "Czas wysyłki minął, ale auto-wysyłka jest WYŁĄCZONA — tylko alarm.",
        "log_window_refound": "Okno Claude odnalezione ponownie (uchwyt {hwnd}).",
        "log_5h_hit": "LIMIT 5-GODZINNY STRZELONY ({pct}%). Reset {reset}. Wyślę o {send}.",
        "log_banner_hit": "Wykryto powiadomienie „USAGE LIMIT REACHED”. Reset {reset}. Wyślę o {send}.",
        "log_5h_reset_updated": "Zaktualizowano reset 5h: {reset}.",
        "log_panel_unreadable": "Nie udało się odczytać panelu zużycia — spróbuję ponownie.",
        "log_send_fail_nowin": "WYSYŁKA NIEUDANA: brak okna Claude.",
        "log_send_abort_fg": "WYSYŁKA PRZERWANA: okno Claude nie jest na wierzchu (nie będę pisać do innej aplikacji).",
        "log_sent": "Wysłano „{message}” + Enter.",
        "log_send_fail": "WYSYŁKA NIEUDANA: {err}",
        "log_retries_done": "Wyczerpano próby — wracam do zwykłego czuwania.",
        "log_retry_at": "Ponowna próba o {send} ({n}/{max}).",
        "log_still_done": "Limit nadal aktywny, wyczerpano próby — czuwam dalej.",
        "log_still_new": "Limit nadal aktywny — nowy reset {reset}, wysyłka o {send}.",
        "log_still_retry": "Limit nadal aktywny — ponowię o {send} ({n}/{max}).",
        "log_success": "SUKCES — limit zniknął, sesja wznowiona. Czuwam dalej.",
    },
}


STRINGS["en"].update({
    "chats_refresh": "Read chats",
    # what is watched: nothing checked = the open conversations, else the checked ones
    "watch_open": "Watching the conversations open in Claude",
    "watch_picked": "Watching the checked conversations ({n})",
    "chats_hint_open": ("Click a row to choose the conversations yourself — then only the checked ones "
                        "are watched. To add an older chat, open it in Claude and click “Read chats”."),
    "chats_hint_picked": ("Click a row to check or uncheck it. Uncheck them all to go back to the "
                          "conversations open in Claude."),
    "chats_empty": "Click “Read chats” to list the conversations open in Claude and the ones in its sidebar.",
    "col_watch": "Watch", "col_chat": "Conversation", "col_source": "Source", "col_status": "Status / next attempt",
    "prefer_try_again": "Prefer “Try again” after a limit reset",
    "retry_api_errors": "Retry API and server errors",
    "auto_approach": "Answer approach questions automatically",
    "auto_permissions": "Approve permission prompts automatically",
    "prefer_try_again_help": (
        "When the usage limit resets, the program tries to click Claude’s “Try again” button, "
        "which repeats the request that was cut off, instead of typing your message. If it can’t "
        "find the button, it sends your message as usual.\n\n"
        "Off: your message is always sent after a reset."),
    "retry_api_errors_help": (
        "When Claude stops because of a temporary problem on its side (an overloaded server, "
        "“API Error”, a dropped connection), the program tries again by itself: it clicks "
        "“Try again” or sends your message.\n\n"
        "First retry after 30 s, each next one later (at most every 15 min), up to 6 times. "
        "Account and access errors (e.g. signed out) are not retried — they need you. "
        "Works only with “Send automatically” on."),
    "auto_approach_help": (
        "Sometimes Claude pauses and asks how to continue, offering a few answers to pick from. "
        "The program picks the answer marked “Recommended”; in a multiple-choice question it "
        "ticks every such answer and clicks “Next” or “Submit”. Follow-up questions are "
        "handled one by one.\n\n"
        "If nothing is recommended (or a single-choice question recommends more than one "
        "answer), it types “Pick your recommended option(s).” into “Other”, so Claude chooses. "
        "It won’t touch a question you’ve already started answering. Permission requests have "
        "their own option below. Works only with “Send automatically” on."),
    "auto_permissions_help": (
        "When Claude stops to ask for permission — e.g. “Allow Claude to fetch …?” or “Allow "
        "Claude to run …?” in “Accept edits” mode — the program clicks “Always allow” within a "
        "few seconds, or “Allow once” when that’s the only choice. It never clicks “Deny”.\n\n"
        "“Always allow” saves a lasting rule in Claude’s settings, so the same request won’t ask "
        "again. Works only in the conversations you watch and with “Send automatically” on. "
        "Each click is written to the log."),
    "log_chat_action": "{chat}: {action}",
    "btn_send_now": "Resume now…", "dlg_send_body": "Resume all conversations in the chosen scope now? Existing drafts and busy sessions will be skipped.",
    "state_monitoring": "Watching conversations", "state_armed": "Waiting for the next attempt",
    "cap_armed_noreset": "Next attempt at {send}. See each conversation’s status below.",
    "cap_armed_reset": "Reset {reset} — next attempt at {send}.",
    "watching": "Watching", "waiting": "Waiting", "verifying": "Checking", "exhausted": "Needs attention",
    "sidebar": "Sidebar", "open": "Open pane", "unavailable": "Unavailable — open it in Claude",
    "waiting_limit_at": "Usage limit — resets {reset}, next attempt {send}",
    "waiting_limit_unknown": "Usage limit — reset time unknown, waiting for it to clear (at the latest {send})",
    "waiting_api": "Server error — retry scheduled",
    "approach_submitted": "Recommended approach submitted", "resumed": "Block cleared",
    "permission_always": "Clicked Always allow — {title}", "permission_once": "Clicked Allow once — {title}",
    "permanent_error": "Account / access error — manual action required", "retry_limit": "Retry limit reached — stop/start to rearm",
    "alert_only": "Alert only — automatic sending is off", "retry": "Clicked Try again", "message": "Sent configured message",
    "cleared": "Error cleared", "busy": "Claude is already working",
    "inactive": "Not watching", "not_visible": "Not currently loaded",
    "question": "Awaiting an answer",
    "state_starting": "Autonomous control in {sec} s",
    "cap_starting": "Then Auto-Resume starts switching conversations and typing in Claude. Click “Stop” to cancel.",
    "log_starting": "Autonomous control starts in {sec} s — click Stop to cancel.",
    "starting": "Starting soon",
    "lbl_start_delay": "Countdown before taking control",
    "btn_default_order": "Default order",
    "log_window_taken": ("Another copy of Auto-Resume is already watching this Claude window, so this one "
                         "won’t — pick another window above, or close the other copy."),
    "cap_hidden": "The Claude window is covered or minimized — keep at least part of it visible.",
    "log_hidden": "Can’t read the Claude window: it is covered by other windows or minimized. "
                  "Keep at least part of it visible (e.g. snap it to one half of the screen).",
})
STRINGS["pl"].update({
    "chats_refresh": "Odczytaj czaty",
    "watch_open": "Pilnuję rozmów otwartych w Claude",
    "watch_picked": "Pilnuję zaznaczonych rozmów ({n})",
    "chats_hint_open": ("Kliknij wiersz, aby samodzielnie wybrać rozmowy — wtedy pilnuję tylko "
                        "zaznaczonych. Starszy czat otwórz w Claude i kliknij „Odczytaj czaty”."),
    "chats_hint_picked": ("Kliknij wiersz, aby go zaznaczyć lub odznaczyć. Odznacz wszystkie, by wrócić "
                          "do rozmów otwartych w Claude."),
    "chats_empty": "Kliknij „Odczytaj czaty”, aby zobaczyć rozmowy otwarte w Claude i te z paska bocznego.",
    "col_watch": "Pilnuj", "col_chat": "Rozmowa", "col_source": "Źródło", "col_status": "Stan / następna próba",
    "prefer_try_again": "Preferuj „Try again” po resecie limitu",
    "retry_api_errors": "Ponawiaj błędy API i serwera",
    "auto_approach": "Automatycznie odpowiadaj na pytania o podejście",
    "auto_permissions": "Automatycznie zatwierdzaj prośby o uprawnienia",
    "prefer_try_again_help": (
        "Gdy limit użycia się zresetuje, program spróbuje kliknąć w Claude przycisk „Try again”, "
        "który ponawia przerwaną prośbę, zamiast wpisywać Twoją wiadomość. Jeśli go nie znajdzie, "
        "wyśle wiadomość jak zwykle.\n\n"
        "Wyłączone: po resecie zawsze idzie Twoja wiadomość."),
    "retry_api_errors_help": (
        "Gdy Claude przerwie pracę przez chwilowy błąd po swojej stronie (np. przeciążony serwer, "
        "„API Error”, zerwane połączenie), program sam spróbuje ponownie: kliknie „Try again” "
        "albo wyśle Twoją wiadomość.\n\n"
        "Pierwsza próba po 30 s, każda kolejna później (maks. co 15 min), najwyżej 6 razy. "
        "Błędów konta i dostępu (np. wylogowanie) nie ponawia — wtedy potrzebna jest Twoja reakcja. "
        "Działa tylko z włączonym „Wysyłaj automatycznie”."),
    "auto_approach_help": (
        "Czasem Claude zatrzymuje się i pyta, jak dalej działać, pokazując kilka odpowiedzi do wyboru. "
        "Program wybiera wtedy odpowiedź oznaczoną jako „Recommended” (rekomendowana), a w pytaniu "
        "wielokrotnego wyboru zaznacza wszystkie takie odpowiedzi i klika „Next” albo „Submit”. "
        "Kolejne pytania obsługuje po kolei.\n\n"
        "Jeśli nic nie jest polecane (albo pytanie jednokrotnego wyboru poleca kilka odpowiedzi), "
        "wpisze w polu „Other”: „Pick your recommended option(s).”, żeby Claude sam wybrał. "
        "Nie rusza pytań z już rozpoczętą odpowiedzią. Prośby o uprawnienia mają osobną opcję poniżej. "
        "Działa tylko z włączonym „Wysyłaj automatycznie”."),
    "auto_permissions_help": (
        "Gdy Claude zatrzymuje się i prosi o zgodę — np. „Allow Claude to fetch …?” albo „Allow "
        "Claude to run …?” w trybie „Accept edits” — program w ciągu kilku sekund klika „Always "
        "allow”, a gdy go nie ma, „Allow once”. Nigdy nie klika „Deny”.\n\n"
        "„Always allow” zapisuje trwałą regułę w ustawieniach Claude, więc takie samo pytanie już "
        "nie wróci. Działa tylko w pilnowanych rozmowach i z włączonym „Wysyłaj automatycznie”. "
        "Każde kliknięcie trafia do dziennika."),
    "log_chat_action": "{chat}: {action}",
    "btn_send_now": "Wznów teraz…", "dlg_send_body": "Wznowić teraz wszystkie rozmowy z wybranego zakresu? Szkice i pracujące sesje zostaną pominięte.",
    "state_monitoring": "Czuwam nad rozmowami", "state_armed": "Czekam na następną próbę",
    "cap_armed_noreset": "Następna próba o {send}. Stan poszczególnych rozmów znajdziesz na liście.",
    "cap_armed_reset": "Reset {reset} — następna próba o {send}.",
    "watching": "Czuwanie", "waiting": "Oczekiwanie", "verifying": "Sprawdzanie", "exhausted": "Wymaga uwagi",
    "sidebar": "Pasek boczny", "open": "Otwarty panel", "unavailable": "Niedostępna — otwórz w Claude",
    "waiting_limit_at": "Limit użycia — reset {reset}, wysyłka o {send}",
    "waiting_limit_unknown": "Limit użycia — nieznana godzina resetu, czekam aż zniknie (najpóźniej {send})",
    "waiting_api": "Błąd serwera — zaplanowano próbę",
    "approach_submitted": "Wysłano rekomendowane podejście", "resumed": "Blokada ustąpiła",
    "permission_always": "Kliknięto Always allow — {title}", "permission_once": "Kliknięto Allow once — {title}",
    "permanent_error": "Błąd konta / dostępu — potrzebne działanie użytkownika", "retry_limit": "Wyczerpano próby — zatrzymaj i uruchom czuwanie ponownie",
    "alert_only": "Tylko alarm — automatyczna wysyłka wyłączona", "retry": "Kliknięto Try again", "message": "Wysłano ustawioną wiadomość",
    "cleared": "Błąd ustąpił", "busy": "Claude już pracuje",
    "inactive": "Czuwanie wyłączone", "not_visible": "Obecnie niewczytana",
    "question": "Czeka na odpowiedź",
    "state_starting": "Autonomiczna kontrola za {sec} s",
    "cap_starting": "Potem Auto-Resume zacznie przełączać rozmowy i pisać w Claude. Kliknij „Zatrzymaj”, aby anulować.",
    "log_starting": "Autonomiczna kontrola ruszy za {sec} s — kliknij „Zatrzymaj”, aby anulować.",
    "starting": "Za chwilę start",
    "lbl_start_delay": "Odliczanie przed przejęciem kontroli",
    "btn_default_order": "Domyślna kolejność",
    "log_window_taken": ("Inna kopia Auto-Resume już pilnuje tego okna Claude, więc ta nie będzie — wybierz "
                         "inne okno powyżej albo zamknij tamtą kopię."),
    "cap_hidden": "Okno Claude jest zasłonięte albo zminimalizowane — zostaw widoczną choć jego część.",
    "log_hidden": "Nie mogę odczytać okna Claude: zasłaniają je inne okna albo jest zminimalizowane. "
                  "Zostaw widoczną choć jego część (np. przyciągnij je do połowy ekranu).",
})

# --- pausing while someone else uses the mouse (input_guard.py) ---------------
STRINGS["en"].update({
    "state_paused": "Paused — {why}",
    "pause_user": "someone is using the mouse",
    "pause_claude_cu": "Claude is using the computer",
    "pause_chatgpt_cu": "ChatGPT is using the computer",
    "cap_paused": "Waiting until the mouse and keyboard are free again.",
    "log_paused": "Paused: {why}. I won’t click or type until it is over.",
    "log_pause_over": "The mouse is free again — watching on.",
    "pause_on_foreign_input": "Pause while someone else uses the mouse",
    "lbl_quiet": "Calm needed before acting",
    "pause_on_foreign_input_help": (
        "Whenever the program clicks or types, it takes over the mouse and keyboard for a "
        "moment. With this option on it waits while someone else is using them.\n\n"
        "It waits when you move the mouse or press a key, when Claude runs its "
        "computer-use tools (its own session log says so) and when ChatGPT shows its "
        "“is using your computer” overlay. It carries on after the calm time set below — "
        "long enough that the screenshot an agent has just taken is still what it clicks "
        "on. “Resume now…” doesn’t wait for calm, because you clicked it yourself."),
})
STRINGS["pl"].update({
    "state_paused": "Wstrzymane — {why}",
    "pause_user": "ktoś używa myszy",
    "pause_claude_cu": "Claude używa komputera",
    "pause_chatgpt_cu": "ChatGPT używa komputera",
    "cap_paused": "Czekam, aż mysz i klawiatura znów będą wolne.",
    "log_paused": "Wstrzymane: {why}. Nie klikam i nie piszę, dopóki to nie minie.",
    "log_pause_over": "Mysz znów wolna — czuwam dalej.",
    "pause_on_foreign_input": "Wstrzymuj, gdy ktoś inny używa myszy",
    "lbl_quiet": "Wymagany spokój przed akcją",
    "pause_on_foreign_input_help": (
        "Za każdym kliknięciem i wpisaniem tekstu program na moment przejmuje mysz i "
        "klawiaturę. Z włączoną opcją czeka, gdy używa ich ktoś inny.\n\n"
        "Czeka, gdy ruszasz myszą albo naciskasz klawisz, gdy Claude korzysta z narzędzi "
        "computer-use (widać to w jego logu sesji) i gdy ChatGPT pokazuje nakładkę "
        "„is using your computer”. Wraca do pracy po czasie spokoju ustawionym poniżej — "
        "na tyle długim, że zrzut ekranu zrobiony właśnie przez agenta nadal odpowiada "
        "temu, w co kliknie. „Wznów teraz…” nie czeka na spokój, bo to Ty kliknąłeś."),
})

# --- handing a full conversation over to a fresh one (handover.py) -------------
STRINGS["en"].update({
    # what the full conversation is asked to do (the body can be edited in Settings)
    "handover_request_default": (
        "[Auto-Resume] Your context window is nearly full ({context}). This conversation "
        "ends here and a fresh agent takes the work over. I interrupted you on purpose: "
        "first make sure nothing is left half-done. Then write a handover for your "
        "successor: the goal, which part of the plan you are on, what is done, what is in "
        "progress and in exactly what state (files, uncommitted changes, running "
        "processes), the next concrete steps, the decisions you made and the traps you "
        "found, and how to check the result. Do not start anything new."),
    "handover_request_tail": (
        "Save it as {path} in the project folder (create the folder if it is missing). "
        "End your reply with this single line of plain text: HANDOVER READY {id}: followed "
        "by the file's full path."),
    "handover_request_again": (
        "[Auto-Resume] Carry on where you stopped: finish the handover file and end your "
        "reply with the line HANDOVER READY {id}: followed by the file's full path."),
    # what the fresh conversation is told
    "handover_continue_default": (
        "[Auto-Resume] You are taking over from another agent that ran out of context. "
        "Read its handover first. Then get to know the project with the graphify skill - "
        "if graphify-out/ already exists, update it instead of building it from scratch - "
        "and check the state of the work yourself instead of taking the notes on trust. "
        "Then carry on: if the task from the handover is not finished, finish it. Work on "
        "your own; nobody is at the keyboard."),
    "handover_continue_tail": "The handover is in {path}.",
    "handover_continue_plan": (
        "The plan is in {plan}. If the task from the handover is finished, pick the next "
        "section there that is not done yet and start it; when every section is done, stop "
        "and write a short summary of what is left for a person. Note in the plan what you "
        "have finished, so the next agent knows where things stand."),
    "handover_continue_noplan": (
        "There is no plan folder: when the task from the handover is finished, stop and "
        "write a short summary of what is left for a person."),
    # conversation list
    "handover_stop": "Handover - stopping the work",
    "handover_wait": "Handover - waiting for the file",
    "handover_new": "Handover - new conversation",
    "handed": "Handed over",
    # log lines and notices
    "log_handover_start": "{chat}: context {context} - stopping the work and asking for a handover.",
    "log_handover_file": "{chat}: handover accepted - {path}",
    "log_handover_new_chat": "{chat}: work handed over to a new conversation ({title}).",
    "handover_no_marker": "Handover unfinished - no marker line in the reply",
    "handover_no_file": "Handover unfinished - the file is missing",
    "handover_no_project": "Can't tell which project the handover belongs to",
    "handover_timeout": "The handover is taking too long",
    "handover_stop_failed": "The conversation would not stop",
    "handover_reminded": "Reminded about the handover",
    # settings
    "handover_enabled": "Hand the work over to a new conversation",
    "lbl_threshold": "Context threshold",
    "hint_threshold": "e.g. 700k or 70%",
    "lbl_plan": "Plan folder",
    "btn_plan_pick": "Choose…",
    "btn_handover_texts": "Edit the handover messages…",
    "dlg_handover_title": "Handover messages",
    "dlg_handover_request": "Asking for the handover:",
    "dlg_handover_continue": "Instruction for the new conversation:",
    "btn_defaults": "Restore defaults",
    "btn_save": "Save",
    "handover_enabled_help": (
        "When a conversation has used up most of its context window, the program hands the "
        "work over instead of letting it run out: it clicks Stop, asks the agent to write a "
        "handover for its successor, and waits for the file and the agreed marker line.\n\n"
        "Then it opens a new conversation in the same project and tells it to read the "
        "handover, get to know the project with graphify and carry on - or, if the task is "
        "done and a plan folder is set, to start the next section of the plan. If anything "
        "cannot be confirmed (no marker, no file, the project of the new conversation), it "
        "sends nothing and marks the conversation “Needs attention”. Works only "
        "with “Send automatically” on.\n\n"
        "Its settings — the context threshold, the plan folder and the two messages — "
        "appear in the Settings tab while this option is on."),
    "sec_handover": "HANDOVER TO A NEW CONVERSATION",
    "handover_threshold_help": (
        "When a conversation has used this much of its context window, the program starts "
        "the handover. Enter tokens — 700k, 0.7M or 700000 — or a share of the window, "
        "e.g. 70%.\n\n"
        "Leave some room: the agent still has to finish what it is doing and write its notes. "
        "A share suits ChatGPT, whose Codex window is about 258k tokens. Values outside a safe "
        "range (below 20k tokens, or outside 20–95%) are pulled into it, so a fresh "
        "conversation never hands the work straight on."),
    "plan_folder_help": (
        "Optional. A folder of your project that holds its plan — the work split into sections "
        "or stages, e.g. docs/plan with one file per stage, or a single file with a checklist.\n\n"
        "After a handover the new conversation first finishes the task it took over. With a "
        "plan folder set, it then opens the plan, picks the next section that isn’t done yet, "
        "starts it and marks in the plan what it has finished — so the work can go on through "
        "many conversations in a row, until every section is done. Without one, it stops once "
        "the handed-over task is done and writes a short summary of what is left for you."),
    "handover_texts_help": (
        "A handover is carried by two messages that the program types for you:\n\n"
        "• Asking for the handover — goes to the conversation whose context is nearly full. It "
        "asks the agent to wrap up and write notes for whoever takes over.\n"
        "• Instruction for the new conversation — goes to the fresh conversation. It tells the "
        "new agent to read those notes, check the project and carry on.\n\n"
        "Here you can change their wording, e.g. to add rules of your own. The technical part — "
        "where the notes are saved, the marker line and the paths — is always added by the "
        "program, so it can’t be edited away. “Restore defaults” brings the built-in texts back."),
    "dlg_handover_intro": (
        "These two messages carry the work over when a conversation’s context is nearly full. "
        "The first goes to the full conversation and asks for handover notes; the second goes "
        "to the new conversation and tells it to read them and carry on. Where the notes are "
        "saved, the marker line and the paths are added by the program: you only edit the wording."),
})
STRINGS["pl"].update({
    "handover_request_default": (
        "[Auto-Resume] Twój kontekst jest prawie pełny ({context}). Ta rozmowa kończy się "
        "tutaj, a pracę przejmie nowy agent. Przerwałem Cię celowo: najpierw upewnij się, "
        "że nic nie zostało zrobione w połowie. Potem napisz handover dla następcy: cel, "
        "etap planu, na którym jesteś, co zrobione, co w toku i w jakim dokładnie stanie "
        "(pliki, niezacommitowane zmiany, uruchomione procesy), kolejne konkretne kroki, "
        "podjęte decyzje i napotkane pułapki oraz jak sprawdzić efekt. Nie zaczynaj nowej "
        "pracy."),
    "handover_request_tail": (
        "Zapisz go jako {path} w katalogu projektu (utwórz folder, jeśli go nie ma). "
        "Na końcu odpowiedzi dodaj jedną linię zwykłym tekstem: HANDOVER READY {id}: "
        "a po dwukropku pełną ścieżkę pliku."),
    "handover_request_again": (
        "[Auto-Resume] Wróć do tego, na czym stanąłeś: dokończ plik handoveru i zakończ "
        "odpowiedź linią HANDOVER READY {id}: a po dwukropku pełną ścieżką pliku."),
    "handover_continue_default": (
        "[Auto-Resume] Przejmujesz pracę po innym agencie, któremu skończył się kontekst. "
        "Najpierw przeczytaj jego handover. Potem poznaj projekt skillem graphify - jeśli "
        "graphify-out/ już istnieje, zaktualizuj go, nie buduj od zera - i sam sprawdź stan "
        "pracy, zamiast wierzyć notatkom na słowo. Dalej: jeśli zadanie z handoveru nie "
        "jest skończone, skończ je. Pracuj samodzielnie, nikogo nie ma przy klawiaturze."),
    "handover_continue_tail": "Handover znajdziesz w {path}.",
    "handover_continue_plan": (
        "Plan jest w {plan}. Jeśli zadanie z handoveru jest skończone, wybierz tam "
        "następną niezrobioną sekcję i zacznij ją; gdy wszystkie są zrobione, zakończ "
        "pracę i napisz krótkie podsumowanie tego, co zostaje dla człowieka. Odnotowuj w "
        "planie, co skończyłeś, żeby następny agent wiedział, na czym stoi."),
    "handover_continue_noplan": (
        "Nie ma folderu z planem: gdy zadanie z handoveru będzie skończone, zatrzymaj się "
        "i napisz krótkie podsumowanie tego, co zostaje dla człowieka."),
    "handover_stop": "Handover - przerywam pracę",
    "handover_wait": "Handover - czekam na plik",
    "handover_new": "Handover - nowa rozmowa",
    "handed": "Przekazane",
    "log_handover_start": "{chat}: kontekst {context} - przerywam pracę i proszę o handover.",
    "log_handover_file": "{chat}: handover przyjęty - {path}",
    "log_handover_new_chat": "{chat}: praca przekazana nowej rozmowie ({title}).",
    "handover_no_marker": "Handover niedokończony - brak linii ze znacznikiem",
    "handover_no_file": "Handover niedokończony - brak pliku",
    "handover_no_project": "Nie wiem, do którego projektu należy handover",
    "handover_timeout": "Handover trwa zbyt długo",
    "handover_stop_failed": "Rozmowa nie chce się zatrzymać",
    "handover_reminded": "Przypomniano o handoverze",
    "handover_enabled": "Przekazuj pracę nowej rozmowie",
    "lbl_threshold": "Próg kontekstu",
    "hint_threshold": "np. 700k albo 70%",
    "lbl_plan": "Folder z planem",
    "btn_plan_pick": "Wybierz…",
    "btn_handover_texts": "Edytuj wiadomości handoveru…",
    "dlg_handover_title": "Wiadomości handoveru",
    "dlg_handover_request": "Prośba o handover:",
    "dlg_handover_continue": "Instrukcja dla nowej rozmowy:",
    "btn_defaults": "Przywróć domyślne",
    "btn_save": "Zapisz",
    "handover_enabled_help": (
        "Gdy rozmowa zużyje większość swojego okna kontekstu, program przekazuje pracę "
        "dalej, zamiast czekać, aż kontekst się skończy: klika Stop, prosi agenta o "
        "handover dla następcy i czeka na plik oraz na umówioną linię ze znacznikiem.\n\n"
        "Potem otwiera nową rozmowę w tym samym projekcie i każe jej przeczytać handover, "
        "poznać projekt przez graphify i kontynuować - albo, jeśli zadanie jest skończone "
        "i wskazałeś folder z planem, zacząć następną sekcję planu. Gdy czegoś nie da się "
        "potwierdzić (brak znacznika, brak pliku, projekt nowej rozmowy), nie wysyła nic i "
        "oznacza rozmowę jako „Wymaga uwagi”. Działa tylko z włączonym "
        "„Wysyłaj automatycznie”.\n\n"
        "Jej ustawienia — próg kontekstu, folder z planem i obie wiadomości — pojawiają się "
        "w zakładce Ustawienia, gdy ta opcja jest włączona."),
    "sec_handover": "PRZEKAZYWANIE PRACY NOWEJ ROZMOWIE",
    "handover_threshold_help": (
        "Gdy rozmowa zużyje tyle swojego okna kontekstu, program zaczyna przekazanie pracy. "
        "Wpisz liczbę tokenów — 700k, 0.7M albo 700000 — albo część okna, np. 70%.\n\n"
        "Zostaw zapas: agent musi jeszcze dokończyć to, co robi, i napisać notatki. Dla ChatGPT "
        "lepiej podać procent, bo okno Codex ma około 258k tokenów. Wartości spoza bezpiecznego "
        "zakresu (poniżej 20k tokenów albo poza 20–95%) są do niego przycinane, żeby nowa "
        "rozmowa nie oddawała pracy od razu dalej."),
    "plan_folder_help": (
        "Opcjonalnie. Folder Twojego projektu z planem pracy — zadaniem podzielonym na sekcje "
        "albo etapy, np. docs/plan z osobnym plikiem na każdy etap albo jeden plik z listą "
        "kontrolną.\n\n"
        "Po przekazaniu nowa rozmowa najpierw kończy przejęte zadanie. Gdy folder z planem jest "
        "wskazany, otwiera plan, wybiera następną niezrobioną sekcję, zaczyna ją i odnotowuje w "
        "planie, co skończyła — dzięki temu praca idzie dalej przez wiele rozmów z rzędu, aż "
        "wszystkie sekcje będą zrobione. Bez niego zatrzymuje się po skończeniu przejętego "
        "zadania i pisze krótkie podsumowanie tego, co zostało dla Ciebie."),
    "handover_texts_help": (
        "Przekazanie pracy odbywa się przez dwie wiadomości, które program wpisuje za Ciebie:\n\n"
        "• Prośba o handover — trafia do rozmowy, której kontekst jest prawie pełny. Prosi "
        "agenta, żeby domknął pracę i napisał notatki dla następcy.\n"
        "• Instrukcja dla nowej rozmowy — trafia do nowej rozmowy. Każe nowemu agentowi "
        "przeczytać te notatki, sprawdzić projekt i kontynuować.\n\n"
        "Tutaj możesz zmienić ich treść, np. dopisać własne zasady. Część techniczną — gdzie "
        "zapisać notatki, linię ze znacznikiem i ścieżki — program zawsze dodaje sam, więc nie "
        "da się jej usunąć. „Przywróć domyślne” przywraca wbudowane teksty."),
    "dlg_handover_intro": (
        "Te dwie wiadomości przenoszą pracę, gdy kontekst rozmowy jest prawie pełny. Pierwsza "
        "trafia do pełnej rozmowy i prosi o notatki dla następcy; druga trafia do nowej rozmowy "
        "i każe je przeczytać i kontynuować. Miejsce zapisu notatek, linię ze znacznikiem i "
        "ścieżki program dodaje sam: zmieniasz tylko treść."),
})

# --- the log -------------------------------------------------------------------
# Every line of the log is this template, so a line can be shown again in the other language.
STRINGS["en"]["log_line"] = STRINGS["pl"]["log_line"] = "[{stamp}] {msg}"

# --- what went wrong, as the log shows it --------------------------------------
# The program's own error messages are raised in English (session_automation.py,
# chatgpt_automation.py, input_lock.py); the log looks them up here like any other key.
# ChatGPT Auto-Resume's copy of the table names ChatGPT where these name Claude.
ERRORS_PL = {
    "Claude window is unavailable": "Okno Claude jest niedostępne",
    "Accessibility scan incomplete; no action taken": "Odczyt okna był niepełny — nic nie zrobiono",
    "Accessibility tree changed; retry on next scan":
        "Okno zmieniło się w trakcie odczytu — spróbuję przy następnym skanie",
    "The app window is covered or minimized": "Okno aplikacji jest zasłonięte albo zminimalizowane",
    "Pending command; action deferred": "Czeka polecenie z okna programu — akcja odłożona",
    "Someone is using the mouse; action deferred": "Ktoś używa myszy — akcja odłożona",
    "Claude is not foreground": "Okno Claude nie jest na wierzchu",
    "Control is unavailable": "Przycisk albo pole jest niedostępne",
    "Someone is using the mouse; keys were not sent": "Ktoś używa myszy — klawisze nie zostały wysłane",
    "Cannot verify composer contents": "Nie da się sprawdzić zawartości pola wiadomości",
    "Existing draft; leaving it untouched": "W polu wiadomości jest szkic — zostawiam go bez zmian",
    "Composer focus could not be verified": "Nie da się potwierdzić, że pole wiadomości jest aktywne",
    "Text write outcome is uncertain; leaving the draft untouched":
        "Nie wiadomo, czy tekst się wpisał — zostawiam pole bez zmian",
    "Input interrupted; message was not submitted": "Wpisywanie przerwane — wiadomość nie została wysłana",
    "Someone took the mouse; message was not submitted": "Ktoś przejął mysz — wiadomość nie została wysłana",
    "Focus changed; message was not submitted": "Zmieniło się aktywne okno — wiadomość nie została wysłana",
    "Typed text could not be verified; message was not submitted":
        "Nie da się potwierdzić wpisanego tekstu — wiadomość nie została wysłana",
    "Nothing to send": "Nie ma czego wysłać",
    "Pending command; message was not submitted": "Czeka polecenie z okna programu — wiadomość nie została wysłana",
    "Selected conversation is missing or ambiguous": "Wybranej rozmowy nie ma albo jest kilka o tej nazwie",
    "The handover names no project folder": "Handover nie wskazuje folderu projektu",
    "The conversation the handover came from is missing": "Nie ma rozmowy, z której pochodzi handover",
    "Cannot tell which button starts a new conversation": "Nie wiem, który przycisk otwiera nową rozmowę",
    "The new conversation's screen could not be read": "Nie da się odczytać ekranu nowej rozmowy",
    "The project of the handover is not on the list": "Projektu z handoveru nie ma na liście",
    "The new conversation is not in the project of the handover": "Nowa rozmowa nie jest w projekcie z handoveru",
    "Question already has a draft answer": "Pytanie ma już wpisaną odpowiedź",
    "Cannot verify question selection state": "Nie da się sprawdzić, co zaznaczono w pytaniu",
    "Question already has a selected answer": "W pytaniu zaznaczono już odpowiedź",
    "Recommended selections could not be verified": "Nie da się potwierdzić zaznaczenia rekomendowanych odpowiedzi",
    "No recommended option or Other field": "Brak rekomendowanej odpowiedzi i pola „Other”",
    "Answer click was not accepted": "Kliknięcie odpowiedzi nie zadziałało",
    "Permission click was not accepted": "Kliknięcie zgody nie zadziałało",
    "ChatGPT did not accept the message (the limit may still be on); it was removed from the composer":
        "ChatGPT nie przyjął wiadomości (limit może nadal trwać) — usunięto ją z pola",
    "ChatGPT did not accept the message (the limit may still be on); it is left in the composer":
        "ChatGPT nie przyjął wiadomości (limit może nadal trwać) — została w polu",
    "Message was typed but not sent; it was removed from the composer":
        "Wiadomość wpisana, ale niewysłana — usunięto ją z pola",
    "Message was typed but not sent; it is left in the composer": "Wiadomość wpisana, ale niewysłana — została w polu",
    "The other Auto-Resume is using the keyboard; will try again":
        "Drugi Auto-Resume używa klawiatury — spróbuję ponownie",
}
STRINGS["en"].update({text: text for text in ERRORS_PL})
STRINGS["pl"].update(ERRORS_PL)
