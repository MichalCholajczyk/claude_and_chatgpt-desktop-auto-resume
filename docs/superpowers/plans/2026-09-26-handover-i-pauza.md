# Plan wdrożenia: handover przy pełnym kontekście i pauza przy cudzej myszy

> **Dla wykonawcy:** plan realizuje specyfikację
> `docs/superpowers/specs/2026-09-25-handover-i-pauza-design.md`. Kroki mają pola
> wyboru (`- [ ]`) do odhaczania. Plan jest wykonywany w tej samej sesji, w której
> powstał (executing-plans, wykonanie wsadowe z punktami kontrolnymi).

**Cel:** Auto-Resume przestaje ruszać myszą, gdy steruje nią ktoś inny, a po
przekroczeniu progu kontekstu przekazuje pracę nowemu czatowi w tym samym projekcie.

**Architektura:** Dwa nowe moduły bez wywołań UI Automation (`input_guard.py`,
`handover.py`) plus wąskie wpięcia w istniejący silnik (`SessionEngine`) i adaptery
okien (`ClaudeUI`, `ChatGPTUI`). Cała logika decyzyjna jest testowalna na sztucznych
drzewach `Node` i podstawionych zegarach; okna dotykają tylko trzy nowe metody
adaptera (`stop`, `send`, `new_chat`).

**Stos:** Python 3.10+, `uiautomation`, `tkinter`, `unittest`, ctypes (Win32).

## Ograniczenia globalne

- Windows 10/11, Python 3.10+; jedyna zależność zewnętrzna to `uiautomation`.
- Interfejs i wszystkie teksty dwujęzycznie: `en` i `pl`, domyślnie `en`.
- Żadne współrzędne ani obiekty UIA nie przetrwają skanu; każda akcja odnajduje swoją
  rozmowę na nowo.
- Fail closed: niejednoznaczny tytuł, cudzy szkic, niepotwierdzony projekt i nieznana
  kontrolka oznaczają rezygnację z działania.
- Nowe klucze konfiguracji są zapisywane; `RUNTIME_ONLY` zostaje bez zmian
  (`watch_scope`, `selected_chats` nadal nie przechodzą między uruchomieniami).
- Wartości domyślne: `pause_on_foreign_input=True`, `foreign_input_quiet_s=30`,
  `computer_use_hold_s=120`, `handover_enabled=False`, `handover_threshold="700k"`
  (ChatGPT: `"70%"`), `plan_folder=""`, `handover_request_text=""`,
  `handover_continue_text=""`, `handover_timeout_min=30`.
- Zakresy po obcięciu: `foreign_input_quiet_s` 5–300, `computer_use_hold_s` 30–900,
  `handover_timeout_min` 5–240, próg: ≥20 000 tokenów albo 20–95%.
- **Bez commitów.** W drzewie roboczym leży niezacommitowana cudza praca (funkcja
  `auto_permissions` w `session_automation.py`, `claude_auto_continue.py`, `README.md`),
  a moje zmiany dotykają tych samych plików. Każde zadanie kończy się pełnym
  przebiegiem testów, nie commitem. Podział na commity proponuję użytkownikowi na końcu.
- Punkt odniesienia: `py -3 -m unittest test_sessions test_permissions test_chatgpt
  test_codex_log test_launcher test_detection test_quota_log test_chatgpt_limits
  test_send_after_reset` → `Ran 192 tests`, `OK`. Ten zestaw musi być zielony po każdym
  zadaniu (rosnąc o nowe testy).

---

## Stan wykonania (2026-09-26)

Zadania 1–12 wykonane. Testy: `Ran 299 tests OK` (jednostkowe), `Ran 21 tests OK`
(okno Tk), `all tests passed` (`test_usage_meter.py`, `test_detection.py`).
Punkt wyjścia przed zmianami: 192 testy.

Odstępstwa od planu, wszystkie wymuszone tym, co pokazało prawdziwe okno:

- **Zadanie 2** — nakładki computer-use po stronie Claude **nie ma**; wykrywanie
  nakładki zostaje dla ChatGPT, a computer-use Claude rozpoznaje log sesji. Przycisk
  projektu w nowej sesji rozpoznajemy po tym, że stoi tuż przed polem gałęzi; lista
  projektów podaje **nazwy folderów**, a nagłówek otwartej sesji **nazwę repozytorium**
  (patrz rozdział 12 specyfikacji).
- **Zadanie 9** — doszło rozwijanie schowanego paska bocznego: w wąskim oknie Claude
  nie pokazuje przycisku „New”, więc handover nie miałby czym otworzyć rozmowy.
- **Zadanie 11** — `lbl_plan` kolidowało z etykietą licznika planu w nagłówku (nowa
  etykieta przesłaniała starą i `_render_usage` czyścił jej tekst). Nazwa zmieniona na
  `lbl_plan_folder`, doszedł test `test_settings_labels_survive_a_usage_reading`.
- **Zadanie 12** — sprawdzone na żywo: pauza przy cudzym wejściu, pauza przy
  computer-use Claude, zwolnienie po ciszy, okno ustawień z nowymi polami oraz odmowa
  otwarcia nowej rozmowy, gdy okna nie da się potwierdzić. **Niesprawdzona na żywo
  została udana ścieżka `new_chat`** — okno Claude'a powtarzalnie pokazuje okienko
  błędu MCP (`stitch`), które chowa pasek boczny. Do dokończenia przy wolnym oknie.

---

## Struktura plików

| Plik | Odpowiedzialność |
|---|---|
| `strings.py` *(nowy)* | tabele tłumaczeń `STRINGS` (przeniesione z `claude_auto_continue.py`) |
| `input_guard.py` *(nowy)* | „czy wolno teraz użyć myszy”: cudze wejście, computer-use Claude, nakładka ChatGPT |
| `handover.py` *(nowy)* | próg, odczyt kontekstu z tekstu licznika, znacznik, treści wiadomości, automat handoveru |
| `tools/probe_ui.py` *(nowy)* | diagnostyka tylko do czytania: nazwy kontrolek potrzebnych do handoveru i nakładek |
| `test_input_guard.py`, `test_handover.py` *(nowe)* | testy obu modułów i ich wpięć |
| `session_automation.py` | `signals()` + kontekst i tekst ostatniej wiadomości, poprawka `METER_CONTEXT`, `ClaudeUI.stop/send/press/new_chat`, wpięcie strażnika i handoveru w `SessionEngine` |
| `chatgpt_automation.py` | to samo dla okna ChatGPT (`ChatGPTUI.stop/send/new_chat`, kontekst z logów Codexa) |
| `codex_log.py` | `context_for(title)` — kontekst rozmowy Codexa z jej pliku sesji |
| `claude_auto_continue.py` | konfiguracja i obcinanie zakresów, nowe teksty, widgety ustawień, stan `PAUSED` |
| `chatgpt_auto_continue.py` | inne wartości domyślne progu i tekstów dla Codexa |
| `README.md` | opis obu funkcji i nowych ustawień |

---

## Zadanie 1: `strings.py` — przeniesienie tłumaczeń

**Pliki:**
- Utwórz: `strings.py`
- Zmień: `claude_auto_continue.py` (usunięcie bloku `STRINGS` i import)

**Interfejsy:**
- Udostępnia: `strings.STRINGS` — `{"en": {...}, "pl": {...}}`; `claude_auto_continue`
  re-eksportuje nazwę `STRINGS`, więc `app.STRINGS` i `tr(..., table=STRINGS)` działają
  jak dziś (`chatgpt_auto_continue._chatgpt_strings()` bierze `copy.deepcopy(app.STRINGS)`).

- [ ] **Krok 1: Utwórz `strings.py`**

Przenieś do niego dosłownie cały blok od `STRINGS = {` do zamykającego nawiasu wraz z
następującymi po nim wywołaniami `STRINGS["en"].update({...})` i
`STRINGS["pl"].update({...})` z `claude_auto_continue.py` (obecnie linie 109–440).
Nagłówek pliku:

```python
# -*- coding: utf-8 -*-
"""UI and log texts for both Auto-Resumes (English / Polish).

Kept apart from claude_auto_continue.py: the tables are a quarter of that file and
grow with every option. ChatGPT Auto-Resume derives its own table from these.
"""
```

- [ ] **Krok 2: W `claude_auto_continue.py` zamień blok na import**

W miejscu usuniętego bloku (po sekcji konfiguracji, przed `def tr(`):

```python
# ----------------------------------------------------------------- translations

from strings import STRINGS
```

- [ ] **Krok 3: Uruchom cały zestaw testów**

Uruchom: `py -3 -m unittest test_sessions test_permissions test_chatgpt test_codex_log test_launcher test_detection test_quota_log test_chatgpt_limits test_send_after_reset`
Oczekiwane: `Ran 192 tests`, `OK`.

- [ ] **Krok 4: Uruchom testy skryptowe okna**

Uruchom: `py -3 test_app_ui.py` i `py -3 test_usage_meter.py`
Oczekiwane: w obu `all tests passed`.

---

## Zadanie 2: Sonda okien — ustalenie czterech nieznanych

Specyfikacja, rozdział 9. Bez tych nazw dalsze zadania musiałyby zgadywać.

**Pliki:**
- Utwórz: `tools/probe_ui.py`
- Zmień: `docs/superpowers/specs/2026-09-25-handover-i-pauza-design.md` (dopisanie
  rozdziału „12. Ustalenia z okien”)

**Interfejsy:**
- Udostępnia: `probe_ui.dump(app_name, pattern=None)` — wypisuje nazwy kontrolek okna
  wskazanej aplikacji (`claude` albo `chatgpt`) poza transkryptem rozmowy, oraz
  `probe_ui.overlays()` — wszystkie widoczne okna wierzchnie procesów `claude.exe`,
  `chatgpt.exe`, `codex.exe` z ich tytułem, rozmiarem i płytkim tekstem.

- [ ] **Krok 1: Napisz `tools/probe_ui.py`**

Tylko czytanie: `WalkControl` + `GetWindowTextW`, żadnych kliknięć. Pomija poddrzewo
„Chat messages”. Uruchamiany jako `py -3 tools/probe_ui.py claude "usage|context"`,
`py -3 tools/probe_ui.py claude --new-session`, `py -3 tools/probe_ui.py overlays`.

- [ ] **Krok 2: Sprawdź nakładkę computer-use Claude**

Wywołaj w tej sesji narzędzie computer-use (zrzut ekranu) i w trakcie uruchom
`py -3 tools/probe_ui.py overlays`. Zapisz: czy pojawia się dodatkowe okno
`claude.exe`, jaki ma tytuł, rozmiar i jaki tekst.

- [ ] **Krok 3: Sprawdź ekran nowej sesji Claude**

Ręcznie otwórz w Claude Desktop nową sesję (przycisk „New” w pasku bocznym) i uruchom
`py -3 tools/probe_ui.py claude --new-session`. Zapisz: nazwę przycisku tworzącego
sesję, nazwę kontrolki pokazującej projekt/folder i sposób wyboru innego projektu.

- [ ] **Krok 4: Sprawdź ekran nowego czatu ChatGPT i kontekst Codexa**

Uruchom `py -3 tools/probe_ui.py chatgpt "projekt|project|context|kontekst|%"` przy
otwartej rozmowie Codexa i na ekranie nowego czatu. Zapisz: nazwę przycisku „nowy
czat”, nazwę przycisku projektu i czy gdziekolwiek widać zużycie kontekstu.

- [ ] **Krok 5: Dopisz ustalenia do specyfikacji**

Nowy rozdział „12. Ustalenia z okien” z dosłownymi nazwami kontrolek i datą pomiaru.
Jeśli nakładki Claude nie ma, zapisz to wprost: sygnał 3 obejmuje wtedy tylko ChatGPT,
a computer-use Claude wykrywa sygnał 2 (log) i sygnał 1 (wstrzykiwane wejście).

---

## Zadanie 3: `input_guard.py` — strażnik wejścia

**Pliki:**
- Utwórz: `input_guard.py`
- Utwórz: `test_input_guard.py`

**Interfejsy:**
- Konsumuje: nic z wcześniejszych zadań (poza nazwami z Zadania 2).
- Udostępnia:
  - `class InputPaused(RuntimeError)`
  - `last_input_age_ms() -> int` — wiek ostatniego wejścia (mysz/klawiatura, dowolne
    źródło) w ms, z `GetLastInputInfo`.
  - `class OwnInputClock` z `mark()` i `age_ms() -> int | None` — wspólny dla obu
    programów 8-bajtowy zapis w pamięci nazwanej `Local\AutoResume.OwnInput`.
  - `class ClaudeComputerUse` z `active(hold_s) -> bool` (+ `__init__(root=None)`).
  - `overlay_phrases(codex_home=None) -> tuple[str, ...]`
  - `overlay_visible(skip_hwnd=None, phrases=None) -> bool`
  - `class InputGuard(worker, *, last_input=None, own=None, cu=None, overlay=None)` z
    `mark_own_input()`, `foreign_input_age_s() -> float | None`, `user_busy() -> bool`,
    `ignore_user_for(seconds)`, `pause_reason() -> str | None`
    (`None`, `"user"`, `"claude_cu"`, `"chatgpt_cu"`).

- [ ] **Krok 1: Testy cudzego wejścia (piszemy je pierwsze)**

`test_input_guard.py`:

```python
"""Tests for the input guard: who is holding the mouse and keyboard."""
import json
import os
import queue
import tempfile
import time
import unittest

import claude_auto_continue as app
from input_guard import InputGuard, InputPaused, ClaudeComputerUse, overlay_phrases


class FakeOwn:
    """Stand-in for the shared own-input record; ages are set by the test."""
    def __init__(self, age=None):
        self.age = age
        self.marks = 0

    def mark(self):
        self.marks += 1
        self.age = 0

    def age_ms(self):
        return self.age


def guard(worker, last_input_ms=10_000, own=None, cu=False, overlay=False):
    return InputGuard(worker, last_input=lambda: last_input_ms, own=own or FakeOwn(),
                      cu=FakeCU(cu), overlay=lambda skip_hwnd=None, phrases=None: overlay)


class FakeCU:
    def __init__(self, active=False):
        self.value, self.holds = active, []

    def active(self, hold_s):
        self.holds.append(hold_s)
        return self.value


class ForeignInputTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None

    def test_fresh_foreign_input_pauses(self):
        g = guard(self.worker, last_input_ms=1_000, own=FakeOwn(age=90_000))
        self.assertAlmostEqual(g.foreign_input_age_s(), 1.0)
        self.assertTrue(g.user_busy())
        self.assertEqual(g.pause_reason(), "user")

    def test_our_own_input_is_not_foreign(self):
        g = guard(self.worker, last_input_ms=1_000, own=FakeOwn(age=1_100))
        self.assertIsNone(g.foreign_input_age_s())
        self.assertFalse(g.user_busy())
        self.assertIsNone(g.pause_reason())

    def test_quiet_period_releases(self):
        self.worker.cfg["foreign_input_quiet_s"] = 30
        g = guard(self.worker, last_input_ms=31_000, own=FakeOwn(age=90_000))
        self.assertFalse(g.user_busy())
        self.assertIsNone(g.pause_reason())

    def test_option_off_never_pauses(self):
        self.worker.cfg["pause_on_foreign_input"] = False
        g = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000), cu=True, overlay=True)
        self.assertIsNone(g.pause_reason())

    def test_manual_command_ignores_the_user_signal_only(self):
        g = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000), cu=True)
        g.ignore_user_for(60)
        self.assertFalse(g.user_busy())
        self.assertEqual(g.pause_reason(), "claude_cu")

    def test_shared_clock_marks_are_recorded(self):
        own = FakeOwn(age=90_000)
        g = guard(self.worker, last_input_ms=100, own=own)
        g.mark_own_input()
        self.assertEqual(own.marks, 1)
        self.assertIsNone(g.foreign_input_age_s())
```

- [ ] **Krok 2: Uruchom — testy mają nie przejść**

Uruchom: `py -3 -m unittest test_input_guard -v`
Oczekiwane: `ModuleNotFoundError: No module named 'input_guard'`.

- [ ] **Krok 3: Napisz `input_guard.py` — wejście i wspólny zegar**

```python
"""May Auto-Resume touch the mouse and keyboard right now?

Three things can hold them: a person at the PC, Claude running its computer-use
tools, or ChatGPT doing the same. Reading the window never disturbs anyone, so only
clicking and typing are gated. Own clicks are recorded in a record shared by both
Auto-Resumes, so one program's typing does not look like a stranger to the other.
"""
import ctypes
import ctypes.wintypes
import json
import os
import re
import time

OWN_NAME = "Local\\AutoResume.OwnInput"
OWN_MARGIN_MS = 300          # the input we just sent may be timestamped a moment later
SIGNAL_CACHE_S = 2.0         # logs and windows are scanned at most this often


class InputPaused(RuntimeError):
    """Someone else is using the mouse; the action was not performed."""


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]


def last_input_age_ms():
    """Milliseconds since the last mouse or keyboard event from any source."""
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0                      # unknown: treat as "just now" and stay out of the way
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


class OwnInputClock:
    """When this PC last received input that WE sent. Shared by both Auto-Resumes."""

    def __init__(self, name=OWN_NAME):
        self.view = None
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateFileMappingW(ctypes.c_void_p(-1), None, 0x4, 0, 8, name)
            if handle:
                address = kernel32.MapViewOfFile(handle, 0xF001F, 0, 0, 8)
                if address:
                    self.view = ctypes.cast(address, ctypes.POINTER(ctypes.c_ulonglong))
        except Exception:
            self.view = None          # no shared record: own input is simply unknown

    def mark(self):
        if self.view is not None:
            self.view[0] = ctypes.windll.kernel32.GetTickCount64()

    def age_ms(self):
        if self.view is None or not self.view[0]:
            return None
        return max(0, ctypes.windll.kernel32.GetTickCount64() - self.view[0])
```

- [ ] **Krok 4: Dopisz `InputGuard` i uruchom testy z kroku 1**

```python
class InputGuard:
    def __init__(self, worker, *, last_input=None, own=None, cu=None, overlay=None):
        self.worker = worker
        self._last_input = last_input or last_input_age_ms
        self.own = own if own is not None else OwnInputClock()
        self.cu = cu if cu is not None else ClaudeComputerUse()
        self.overlay = overlay if overlay is not None else overlay_visible
        self._ignore_user_until = 0.0
        self._cached = (0.0, None)

    def _cfg(self, key, default):
        value = self.worker.cfg.get(key, default)
        return default if value is None else value

    def mark_own_input(self):
        self.own.mark()

    def foreign_input_age_s(self):
        """Seconds since the newest input that was not ours, or None when it was ours."""
        age = self._last_input()
        own = self.own.age_ms()
        if own is not None and age >= own - OWN_MARGIN_MS:
            return None
        return age / 1000.0

    def ignore_user_for(self, seconds):
        """A command the user just clicked in our own window must not wait for calm."""
        self._ignore_user_until = time.monotonic() + seconds

    def user_busy(self):
        if not self._cfg("pause_on_foreign_input", True) or time.monotonic() < self._ignore_user_until:
            return False
        age = self.foreign_input_age_s()
        return age is not None and age < self._cfg("foreign_input_quiet_s", 30)

    def pause_reason(self):
        if not self._cfg("pause_on_foreign_input", True):
            return None
        if self.user_busy():
            return "user"
        fresh, reason = self._cached
        if time.monotonic() < fresh:
            return reason
        reason = None
        if self.cu.active(self._cfg("computer_use_hold_s", 120)):
            reason = "claude_cu"
        elif self.overlay(skip_hwnd=getattr(self.worker, "hwnd", None)):
            reason = "chatgpt_cu"
        self._cached = (time.monotonic() + SIGNAL_CACHE_S, reason)
        return reason
```

Uruchom: `py -3 -m unittest test_input_guard -v`
Oczekiwane: 6 testów `ok`.

- [ ] **Krok 5: Testy wykrywania computer-use w logach Claude**

Dopisz do `test_input_guard.py`:

```python
CU_LINE = ('{{"type":"assistant","timestamp":"{ts}","message":{{"model":"claude-opus-5",'
           '"content":[{{"type":"tool_use","name":"mcp__computer-use__screenshot"}}],'
           '"stop_reason":"tool_use"}}}}')
END_LINE = ('{{"type":"assistant","timestamp":"{ts}","message":{{"content":[{{"type":"text",'
            '"text":"done"}}],"stop_reason":"end_turn"}}}}')


def stamp(seconds_ago):
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ClaudeComputerUseTests(unittest.TestCase):
    def logs(self, *lines):
        root = tempfile.mkdtemp()
        project = os.path.join(root, "C--project")
        os.makedirs(project)
        with open(os.path.join(project, "session.jsonl"), "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return ClaudeComputerUse(root)

    def test_recent_tool_call_in_an_open_turn_is_active(self):
        cu = self.logs(CU_LINE.format(ts=stamp(20)))
        self.assertTrue(cu.active(120))

    def test_end_turn_after_the_tool_call_releases(self):
        cu = self.logs(CU_LINE.format(ts=stamp(20)), END_LINE.format(ts=stamp(5)))
        self.assertFalse(cu.active(120))

    def test_older_than_the_hold_releases(self):
        cu = self.logs(CU_LINE.format(ts=stamp(600)))
        self.assertFalse(cu.active(120))

    def test_no_logs_at_all_is_not_active(self):
        self.assertFalse(ClaudeComputerUse(tempfile.mkdtemp()).active(120))

    def test_unreadable_root_never_raises(self):
        self.assertFalse(ClaudeComputerUse(os.path.join(tempfile.mkdtemp(), "gone")).active(120))
```

- [ ] **Krok 6: Uruchom — nowe testy mają nie przejść**

Uruchom: `py -3 -m unittest test_input_guard.ClaudeComputerUseTests -v`
Oczekiwane: `AttributeError` / `TypeError` na `ClaudeComputerUse`.

- [ ] **Krok 7: Napisz `ClaudeComputerUse`**

Wzorowane na `quota_log.QuotaLog`: tylko końcówki plików zmienionych w ostatnich
`LOG_WINDOW_S` sekundach, wynik zapamiętany po `(mtime_ns, size)`.

```python
CU_TOOL = b"mcp__computer-use__"
CU_END = re.compile(rb'"stop_reason"\s*:\s*"end_turn"')
CU_TS = re.compile(rb'"timestamp"\s*:\s*"([0-9\-]{10}T[0-9:]{8})')
LOG_WINDOW_S = 300
TAIL_BYTES = 256 * 1024
MAX_FILES = 40


def claude_projects_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


class ClaudeComputerUse:
    """Is a Claude Code session in the middle of driving the screen?

    Its own log says so: the tool call ("mcp__computer-use__…") is the last thing that
    happened in a turn that has not ended yet. A turn that ended (stop_reason
    "end_turn") releases the mouse at once, and a call older than the hold is stale.
    """

    def __init__(self, root=None):
        self.root = root if root is not None else claude_projects_root()
        self._cache = {}

    def active(self, hold_s):
        newest = None
        cache = {}
        for path, stat in self._recent_logs():
            key = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(path)
            if cached is None or cached[0] != key:
                cached = (key, self._scan(path, stat.st_size))
            cache[path] = cached
            age = cached[1]
            if age is not None and (newest is None or age < newest):
                newest = age
        self._cache = cache
        return newest is not None and newest <= hold_s
```

`_recent_logs()` — jak w `quota_log` (skan `self.root`, `*.jsonl`, `st_mtime` młodsze
niż `LOG_WINDOW_S`, najwyżej `MAX_FILES`, sortowane od najnowszego, wszystkie błędy
`OSError` połykane). `_scan(path, size)` — czyta ogon, dzieli na linie, idzie od końca:
pierwsza linia z `CU_END` → `None` (tura zamknięta), pierwsza linia z `CU_TOOL` →
wiek w sekundach z `CU_TS` (czas UTC, `dt.datetime.now(dt.timezone.utc)` jako teraz;
brak znacznika → `0.0`, czyli „przed chwilą”).

- [ ] **Krok 8: Uruchom testy computer-use**

Uruchom: `py -3 -m unittest test_input_guard -v`
Oczekiwane: 11 testów `ok`.

- [ ] **Krok 9: Testy i implementacja nakładki**

Test (dopisz do `test_input_guard.py`):

```python
class OverlayTests(unittest.TestCase):
    def test_phrases_come_from_the_codex_config(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, "computer-use"))
        with open(os.path.join(home, "computer-use", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"strings": {"usingComputer": "ChatGPT używa Twojego komputera"}}, f)
        phrases = overlay_phrases(home)
        self.assertIn("chatgpt używa twojego komputera", phrases)
        self.assertIn("is using your computer", phrases)      # fallbacks stay

    def test_missing_config_still_gives_fallbacks(self):
        phrases = overlay_phrases(os.path.join(tempfile.mkdtemp(), "gone"))
        self.assertIn("is using your computer", phrases)
```

Implementacja: `overlay_phrases(codex_home=None)` czyta
`<CODEX_HOME|~/.codex>/computer-use/config.json` → `strings.usingComputer`, dokłada
stałe `("is using your computer", "używa twojego komputera")`, wszystko małymi literami.
`overlay_visible(skip_hwnd=None, phrases=None)` — `EnumWindows`, bierze okna widoczne,
nie `skip_hwnd`, z procesów `("claude.exe", "chatgpt.exe", "codex.exe")`, o powierzchni
mniejszej niż 40% ekranu; dopasowuje frazę do tytułu (`GetWindowTextW`), a gdy tytuł
nie pasuje, robi płytki odczyt UIA (`maxDepth=6`, najwyżej 200 węzłów) tylko tych okien.
Wynik nakładki jest wyłącznie danymi wejściowymi strażnika — nigdy nie klika.
Dokładne nazwy z Zadania 2 wchodzą tu jako stałe.

- [ ] **Krok 10: Uruchom cały plik testów**

Uruchom: `py -3 -m unittest test_input_guard -v`
Oczekiwane: 13 testów `ok`.

---

## Zadanie 4: Wpięcie strażnika — pauza w silniku i przy każdym kliknięciu

**Pliki:**
- Zmień: `claude_auto_continue.py` (`DEFAULT_CONFIG`, `load_config`, `MonitorWorker.__init__`,
  `_read_usage_panel`, `_process_commands`, `STATE_COLOR`, `STATE_LABEL`, `_render_state`,
  `_update_caption`)
- Zmień: `strings.py` (nowe teksty)
- Zmień: `session_automation.py` (`ClaudeUI.click`, `type_into`, nowe `press`,
  `SessionEngine.tick`, `publish`)
- Zmień: `chatgpt_automation.py` (`ChatGPTEngine.tick` — nie czytaj menu w pauzie),
  `chatgpt_auto_continue.py` (`_read_usage_menu`, `_close_menu`)
- Zmień: `test_input_guard.py` (testy wpięcia)

**Interfejsy:**
- Konsumuje: `InputGuard`, `InputPaused` z Zadania 3.
- Udostępnia: `MonitorWorker.guard` (`InputGuard`), `MonitorWorker.pause_reason`
  (`None | str`), `ClaudeUI.press(keys, wait=0.1)`, stan `"PAUSED"` w zdarzeniu `state`
  (klucz `pause_reason`).

- [ ] **Krok 1: Testy wpięcia**

```python
class GuardWiringTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.worker.state = self.worker.MONITORING
        self.engine = self.worker.engine
        self.engine.refresh = lambda: (_ for _ in ()).throw(AssertionError("scanned while paused"))

    def test_tick_skips_the_scan_and_publishes_the_reason(self):
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        self.engine.tick()
        self.assertEqual(self.worker.pause_reason, "user")
        states = [data for kind, data in list(self.worker.out.queue) if kind == "state"]
        self.assertEqual(states[-1]["state"], "PAUSED")
        self.assertEqual(states[-1]["pause_reason"], "user")

    def test_defaults_and_clamping(self):
        self.assertTrue(app.DEFAULT_CONFIG["pause_on_foreign_input"])
        self.assertEqual(app.DEFAULT_CONFIG["foreign_input_quiet_s"], 30)
        cfg = app.load_config(os.devnull)
        self.assertEqual(cfg["computer_use_hold_s"], 120)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"foreign_input_quiet_s": 9999, "computer_use_hold_s": 1}, f)
        cfg = app.load_config(f.name)
        self.assertEqual(cfg["foreign_input_quiet_s"], 300)
        self.assertEqual(cfg["computer_use_hold_s"], 30)

    def test_click_refuses_while_someone_holds_the_mouse(self):
        from session_automation import ClaudeUI
        ui = ClaudeUI(self.worker)
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        with self.assertRaises(InputPaused):
            ui.click(object())

    def test_texts_exist_in_both_languages(self):
        for lang in ("en", "pl"):
            for key in ("state_paused", "pause_user", "pause_claude_cu", "pause_chatgpt_cu",
                        "log_paused", "log_pause_over", "pause_on_foreign_input",
                        "pause_on_foreign_input_help", "lbl_quiet"):
                self.assertTrue(app.tr(lang, key))
```

- [ ] **Krok 2: Uruchom — mają nie przejść**

Uruchom: `py -3 -m unittest test_input_guard.GuardWiringTests -v`
Oczekiwane: 4 testy `FAIL`/`ERROR` (brak kluczy konfiguracji, brak `pause_reason`).

- [ ] **Krok 3: Konfiguracja i strażnik w workerze**

W `DEFAULT_CONFIG` dopisz `"pause_on_foreign_input": True`, `"foreign_input_quiet_s": 30`,
`"computer_use_hold_s": 120`. W `load_config` dodaj do listy obcinania
`("foreign_input_quiet_s", 5, 300)` i `("computer_use_hold_s", 30, 900)`.
W `MonitorWorker.__init__`: `self.guard = InputGuard(self)` i `self.pause_reason = None`
(import `from input_guard import InputGuard, InputPaused`).

- [ ] **Krok 4: Pauza w `SessionEngine.tick` i `publish`**

Na początku `tick()` (przed `check_permissions()`):

```python
        reason = self.worker.guard.pause_reason()
        if reason:
            if self.worker.pause_reason != reason:
                self.worker.pause_reason = reason
                self.worker.log("log_paused", "warn", why=self.worker.t("pause_" + reason))
            self.publish()
            return
        if self.worker.pause_reason:
            self.worker.pause_reason = None
            self.worker.log("log_pause_over")
```

W `publish()` przy wyliczaniu `shown`:

```python
        shown = (self.worker.state if self.worker.state in ("IDLE", "STARTING") else
                 "PAUSED" if self.worker.pause_reason else
                 "ARMED" if nearest else "MONITORING")
        self.worker.emit("state", dict(state=shown, reset_at=self.worker.reset_at,
                                       send_at=self.worker.send_at,
                                       pause_reason=self.worker.pause_reason,
                                       start_until=getattr(self.worker, "start_until", None)))
```

W `ChatGPTEngine.tick` warunek na `check_usage()`:
`if scanning and not self.last_scan_error and not self.worker.pause_reason:`.

- [ ] **Krok 5: Pauza i znaczniki własnego wejścia w akcjach**

`ClaudeUI.click` — po sprawdzeniu oczekujących poleceń:

```python
        if self.worker.guard.user_busy():
            raise InputPaused("Someone is using the mouse; action deferred")
```

…a po `node.control.Click(simulateMove=False)` → `self.worker.guard.mark_own_input()`.

Nowa metoda:

```python
    def press(self, keys, wait=0.1):
        """Send keys to the focused control and record them as our own input."""
        from claude_auto_continue import auto
        if self.worker.guard.user_busy():
            raise InputPaused("Someone is using the mouse; keys were not sent")
        auto.SendKeys(keys, waitTime=wait)
        self.worker.guard.mark_own_input()
```

Zamień na `self.press(...)` wszystkie `auto.SendKeys` w `ClaudeUI.resume`
(`"{Enter}"`), `ChatGPTUI.resume`, `ChatGPTUI.withdraw` (`"{Ctrl}a{Delete}"`).
W `type_into` po `pattern.SetValue(text)` i po każdej porcji `auto.SendKeys(...)`
dodaj `self.worker.guard.mark_own_input()`, a do warunku przerwania w pętli porcji
dodaj `or self.worker.guard.user_busy()`.
W `MonitorWorker._read_usage_panel` i `ChatGPTWorker._read_usage_menu`: `return {}, False`
(odpowiednio `[], False`) gdy `self.guard.user_busy()`; po kliknięciu, po `{Esc}`
i po `SetCursorPos` → `self.guard.mark_own_input()`. To samo w `_close_menu`.

- [ ] **Krok 6: Ręczne polecenia nie czekają na spokój myszy**

W `MonitorWorker._process_commands`, w gałęziach `"send_now"` i `"arm_manual"`, przed
działaniem: `self.guard.ignore_user_for(120)`.

- [ ] **Krok 7: Teksty w `strings.py` (en + pl)**

```python
    "state_paused": "Paused — {why}",
    "pause_user": "someone is using the mouse",
    "pause_claude_cu": "Claude is using the computer",
    "pause_chatgpt_cu": "ChatGPT is using the computer",
    "cap_paused": "Waiting until the mouse and keyboard are free again.",
    "log_paused": "Paused: {why}. I won’t click or type until it is over.",
    "log_pause_over": "The mouse is free again — watching on.",
    "pause_on_foreign_input": "Pause while someone else uses the mouse",
    "lbl_quiet": "Calm needed before acting",
    "pause_on_foreign_input_help": (...),
```

Polskie odpowiedniki: `"Wstrzymane — {why}"`, `"ktoś używa myszy"`,
`"Claude używa komputera"`, `"ChatGPT używa komputera"`,
`"Czekam, aż mysz i klawiatura znów będą wolne."`,
`"Wstrzymane: {why}. Nie klikam i nie piszę, dopóki to nie minie."`,
`"Mysz znów wolna — czuwam dalej."`, `"Wstrzymuj, gdy ktoś inny używa myszy"`,
`"Wymagany spokój przed akcją"` oraz tekst pomocy opisujący trzy sygnały i czas spokoju.

- [ ] **Krok 8: Stan `PAUSED` w oknie**

`STATE_COLOR["PAUSED"] = "amber"`, `STATE_LABEL["PAUSED"] = "state_paused"`.
W `_render_state`, dla `st == "PAUSED"`:

```python
        reason = self.state_info.get("pause_reason") or "user"
        text = self._T("state_paused", why=self._T("pause_" + reason))
```

W `_update_caption` dla tego stanu użyj `cap_paused`.

- [ ] **Krok 9: Uruchom testy wpięcia i cały zestaw**

Uruchom: `py -3 -m unittest test_input_guard -v`
Oczekiwane: 17 testów `ok`.
Uruchom: `py -3 -m unittest test_sessions test_permissions test_chatgpt test_codex_log test_launcher test_detection test_quota_log test_chatgpt_limits test_send_after_reset`
Oczekiwane: `OK` (192 testy nadal przechodzą).
Uruchom: `py -3 test_app_ui.py`
Oczekiwane: `all tests passed`.

---

## Zadanie 5: Kontekst z licznika Claude + poprawka `METER_CONTEXT`

**Pliki:**
- Utwórz: `handover.py` (na razie same czytniki), `test_handover.py`
- Zmień: `session_automation.py` (`METER_CONTEXT`, `usage_meter`, `signals`)

**Interfejsy:**
- Udostępnia:
  - `handover.context_from_meter(name) -> dict(used, window, pct) | None`
  - `handover.last_message_text(pane) -> str` (Claude: zlepiony tekst ostatniego
    `article` w „chat messages”)
  - `signals()` zwraca dodatkowo `context` (ten sam słownik albo `None`) i `last_text`.
- Konsumuje: `Node`, `Pane` z `session_automation`.

- [ ] **Krok 1: Testy czytnika kontekstu**

```python
"""Tests for the context reading, the threshold and the handover flow."""
import datetime as dt
import os
import queue
import tempfile
import unittest

import claude_auto_continue as app
import handover
from session_automation import signals, usage_meter
from test_sessions import node, pane, message


class MeterContextTests(unittest.TestCase):
    def test_tokens_window_and_percent(self):
        c = handover.context_from_meter(
            "Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit, Resets in 3 hr 35 min")
        self.assertEqual(c, dict(used=195_800, window=1_000_000, pct=20))

    def test_older_percent_only_meter(self):
        self.assertEqual(handover.context_from_meter("Usage: context 20%, plan 45%"),
                         dict(used=None, window=None, pct=20))

    def test_meter_without_context_gives_nothing(self):
        self.assertIsNone(handover.context_from_meter("Usage, Weekly · all models: 19%"))

    def test_plan_percentages_ignore_the_context_part(self):
        p = pane()
        node("Usage: Context 950k / 1M (95%), 12% of 5-hour limit", "ButtonControl", p.root)
        self.assertEqual(usage_meter(p, lambda _t: None)["pct"], 12)

    def test_signals_carry_context_and_last_message_text(self):
        p = pane()
        node("Usage: Context 712k / 1M (71%), 30% of 5-hour limit", "ButtonControl", p.root)
        message(p, 1, ("HANDOVER READY 20260926-120000: C:\\p\\.handover\\HANDOVER-20260926-120000.md",
                       "TextControl"))
        observed = signals(p, lambda _texts: (None, None))
        self.assertEqual(observed["context"]["used"], 712_000)
        self.assertIn("HANDOVER READY 20260926-120000", observed["last_text"])
```

- [ ] **Krok 2: Uruchom — mają nie przejść**

Uruchom: `py -3 -m unittest test_handover -v`
Oczekiwane: `ModuleNotFoundError: No module named 'handover'`.

- [ ] **Krok 3: Napisz czytniki w `handover.py`**

```python
"""Handing a full conversation over to a fresh one.

The program reads how much context a conversation has used, asks the agent to write a
handover file for its successor, waits for the file and the agreed marker line, then
opens a new conversation in the same project and tells it to carry on. Nothing here
touches a window: the flow talks to the app through the adapter's stop/send/new_chat.
"""
import datetime as dt
import os
import re

CONTEXT_RE = re.compile(
    r"(?:context|kontekst)\s*:?\s*(?P<used>\d+(?:[.,]\d+)?)\s*(?P<uu>[kKmM])?"
    r"(?:\s*/\s*(?P<win>\d+(?:[.,]\d+)?)\s*(?P<wu>[kKmM])?)?"
    r"(?:\s*\(\s*(?P<pct>\d{1,3})\s*%\s*\))?", re.I)
CONTEXT_PCT_RE = re.compile(r"(?:context|kontekst)\s*:?\s*(?P<pct>\d{1,3})\s*%", re.I)
UNITS = {"": 1, "k": 1_000, "m": 1_000_000}


def _tokens(number, unit):
    return int(round(float(number.replace(",", ".")) * UNITS[(unit or "").lower()]))


def context_from_meter(name):
    """dict(used, window, pct) from the bottom-bar meter's name, or None.

    "Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit" -> 195800 of 1000000, 20%.
    The older meter only says "context 20%": then used and window stay None.
    """
    name = name or ""
    pct_only = CONTEXT_PCT_RE.search(name)
    if pct_only:
        return dict(used=None, window=None, pct=int(pct_only["pct"]))
    match = CONTEXT_RE.search(name)
    if not match:
        return None
    used = _tokens(match["used"], match["uu"])
    window = _tokens(match["win"], match["wu"]) if match["win"] else None
    pct = int(match["pct"]) if match["pct"] else (
        round(100 * used / window) if window else None)
    return dict(used=used, window=window, pct=pct)
```

- [ ] **Krok 4: Popraw `METER_CONTEXT` i dodaj kontekst do `signals`**

W `session_automation.py`:

```python
# The context part of the meter ("Context 195.8k / 1M (20%)") is not a plan limit:
# its own percentage must never be read as a used-up plan window.
METER_CONTEXT = re.compile(r"context\s*:?\s*[\d.,]+\s*[km]?"
                           r"(?:\s*/\s*[\d.,]+\s*[km]?)?(?:\s*\(\s*\d{1,3}\s*%\s*\))?|"
                           r"context\s*:?\s*\d{1,3}\s*%", re.I)
```

`usage_meter` zwraca dodatkowo kontekst:

```python
    return dict(pct=max(percentages) if percentages else None, reset=parse(meter.name),
                context=context_from_meter(meter.name))
```

(import `from handover import context_from_meter` na górze pliku). W `signals()` dopisz
do zwracanego słownika `context=...` i `last_text=...`:

```python
    meter = next((n for n in pane.ui_nodes() if n.type == "ButtonControl"
                  and USAGE_METER.match(n.name)), None)
    articles = [n for n in pane.root.walk() if n.role == "article" and n.inside("chat messages")]
    last_text = " ".join(n.name for n in (articles[-1].walk() if articles else ())
                         if n.name.strip())
    ...
                context=context_from_meter(meter.name) if meter is not None else None,
                last_text=last_text,
```

- [ ] **Krok 5: Uruchom testy**

Uruchom: `py -3 -m unittest test_handover -v`
Oczekiwane: 5 testów `ok`.
Uruchom: `py -3 -m unittest test_sessions test_permissions test_chatgpt test_usage_meter`
Oczekiwane: `OK`.

---

## Zadanie 6: Kontekst rozmowy Codexa z logów

**Pliki:**
- Zmień: `codex_log.py`, `chatgpt_automation.py` (`signals`)
- Zmień: `test_handover.py` (nowa klasa testów)

**Interfejsy:**
- Udostępnia: `codex_log.CodexLog.context_for(title) -> dict(used, window, pct) | None`
  (ten sam kształt co `handover.context_from_meter`), `chatgpt_automation.signals()`
  z kluczami `context` (`None` — uzupełnia go silnik) i `last_text`.

- [ ] **Krok 1: Testy**

```python
class CodexContextTests(unittest.TestCase):
    def build(self, thread="Praca nad planem", used=180_000, window=258_400, info=True):
        home = tempfile.mkdtemp()
        sid = "01a0a32e-fb69-76d1-b01f-0398c2aa611a"
        with open(os.path.join(home, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id":"%s","thread_name":"%s","updated_at":"2026-09-26T10:00:00.0Z"}\n'
                    % (sid, thread))
        folder = os.path.join(home, "sessions", "2026", "09", "26")
        os.makedirs(folder)
        payload = ('{"timestamp":"2026-09-26T10:05:00.000Z","type":"event_msg","payload":'
                   '{"type":"token_count","info":%s}}')
        body = ('{"last_token_usage":{"input_tokens":%d,"output_tokens":0,"total_tokens":%d},'
                '"model_context_window":%d}' % (used, used, window)) if info else "null"
        with open(os.path.join(folder, "rollout-2026-09-26T10-00-00-%s.jsonl" % sid),
                  "w", encoding="utf-8") as f:
            f.write(payload % body + "\n")
        return home

    def test_context_of_a_named_conversation(self):
        from codex_log import CodexLog
        log = CodexLog(os.path.join(self.build(), "sessions"))
        self.assertEqual(log.context_for("Praca nad planem"),
                         dict(used=180_000, window=258_400, pct=70))

    def test_unknown_title_gives_nothing(self):
        from codex_log import CodexLog
        log = CodexLog(os.path.join(self.build(), "sessions"))
        self.assertIsNone(log.context_for("Inna rozmowa"))

    def test_record_without_info_is_skipped(self):
        from codex_log import CodexLog
        log = CodexLog(os.path.join(self.build(info=False), "sessions"))
        self.assertIsNone(log.context_for("Praca nad planem"))
```

- [ ] **Krok 2: Uruchom — mają nie przejść**

Uruchom: `py -3 -m unittest test_handover.CodexContextTests -v`
Oczekiwane: `AttributeError: 'CodexLog' object has no attribute 'context_for'`.

- [ ] **Krok 3: Zaimplementuj `context_for`**

W `codex_log.py` (`self.root` to katalog `sessions`, indeks leży obok niego):

```python
    def context_for(self, title):
        """How much context the conversation with this title has used, or None.

        session_index.jsonl maps a conversation's name to its session id; the id ends
        the name of its rollout file, whose last token_count record carries the used
        tokens and the model's context window.
        """
        sid = self._session_id(title)
        if not sid:
            return None
        path = next((p for p, _ in self._recent_logs() if p.endswith(sid + ".jsonl")), None)
        if path is None:
            return None
        used, window = self._last_token_count(path)
        if not used or not window:
            return None
        return dict(used=used, window=window, pct=round(100 * used / window))
```

`_session_id(title)` — czyta `os.path.join(os.path.dirname(self.root), "session_index.jsonl")`,
bierze wiersze o `thread_name == title`, wygrywa najnowszy `updated_at`; błędy `OSError`
i `ValueError` połykane, brak dopasowania → `None`.
`_last_token_count(path)` — ogon pliku, linie od końca, pierwszy wpis
`payload.type == "token_count"` z niepustym `info` → `(info["last_token_usage"]["total_tokens"],
info["model_context_window"])`, w razie braku `(None, None)`.

- [ ] **Krok 4: `signals` ChatGPT zwraca `context` i `last_text`**

W `chatgpt_automation.signals()` dopisz do słownika `context=None` oraz
`last_text=" ".join(n.name for n in words if n.name.strip())` (pełny ostatni zwrot, nie
tylko ostatni liść — `last` zostaje bez zmian dla dzisiejszych testów).

- [ ] **Krok 5: Uruchom testy**

Uruchom: `py -3 -m unittest test_handover test_codex_log test_chatgpt -v`
Oczekiwane: wszystko `ok`.

---

## Zadanie 7: Próg kontekstu — rozbiór, obcinanie, konfiguracja

**Pliki:**
- Zmień: `handover.py`, `claude_auto_continue.py` (`DEFAULT_CONFIG`, `load_config`),
  `chatgpt_auto_continue.py` (domyślny próg `"70%"`)
- Zmień: `test_handover.py`

**Interfejsy:**
- Udostępnia: `handover.parse_threshold(text) -> ("tokens", int) | ("pct", int) | None`,
  `handover.over_threshold(context, threshold) -> bool`, klucze konfiguracji
  `handover_enabled`, `handover_threshold`, `plan_folder`, `handover_request_text`,
  `handover_continue_text`, `handover_timeout_min`.

- [ ] **Krok 1: Testy**

```python
class ThresholdTests(unittest.TestCase):
    def test_all_accepted_spellings(self):
        self.assertEqual(handover.parse_threshold("700k"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold("0.7M"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold("700000"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold(" 70 % "), ("pct", 70))

    def test_unreadable_text(self):
        for text in ("", "dużo", "70%%", "k", None):
            self.assertIsNone(handover.parse_threshold(text))

    def test_clamped_to_a_safe_range(self):
        self.assertEqual(handover.parse_threshold("5k"), ("tokens", 20_000))
        self.assertEqual(handover.parse_threshold("1%"), ("pct", 20))
        self.assertEqual(handover.parse_threshold("99%"), ("pct", 95))

    def test_comparison_needs_comparable_numbers(self):
        tokens, pct = ("tokens", 700_000), ("pct", 70)
        self.assertTrue(handover.over_threshold(dict(used=712_000, window=1_000_000, pct=71), tokens))
        self.assertFalse(handover.over_threshold(dict(used=300_000, window=1_000_000, pct=30), tokens))
        self.assertTrue(handover.over_threshold(dict(used=None, window=None, pct=71), pct))
        self.assertFalse(handover.over_threshold(dict(used=None, window=None, pct=71), tokens))
        self.assertFalse(handover.over_threshold(None, pct))

    def test_config_defaults(self):
        self.assertFalse(app.DEFAULT_CONFIG["handover_enabled"])
        self.assertEqual(app.DEFAULT_CONFIG["handover_threshold"], "700k")
        self.assertEqual(app.DEFAULT_CONFIG["plan_folder"], "")
        import chatgpt_auto_continue
        self.assertEqual(chatgpt_auto_continue.DEFAULT_CONFIG["handover_threshold"], "70%")
```

- [ ] **Krok 2: Uruchom — mają nie przejść**

Uruchom: `py -3 -m unittest test_handover.ThresholdTests -v`
Oczekiwane: `AttributeError: module 'handover' has no attribute 'parse_threshold'`.

- [ ] **Krok 3: Implementacja**

```python
THRESHOLD_RE = re.compile(r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[kKmM%]?)\s*$")
MIN_TOKENS, MIN_PCT, MAX_PCT = 20_000, 20, 95


def parse_threshold(text):
    """("tokens", 700000) for 700k / 0.7M / 700000, ("pct", 70) for 70%, else None.

    Clamped: a threshold a fresh conversation would already exceed would hand the work
    over again and again.
    """
    match = THRESHOLD_RE.match(text or "")
    if not match:
        return None
    number = float(match["num"].replace(",", "."))
    unit = match["unit"].lower()
    if unit == "%":
        return "pct", int(min(MAX_PCT, max(MIN_PCT, round(number))))
    return "tokens", max(MIN_TOKENS, _tokens(match["num"], unit))


def over_threshold(context, threshold):
    """True when the reading reaches the threshold. An unknown or incomparable
    reading is never over it."""
    if not context or not threshold:
        return False
    kind, value = threshold
    if kind == "tokens":
        return bool(context.get("used")) and context["used"] >= value
    pct = context.get("pct")
    if pct is None and context.get("used") and context.get("window"):
        pct = 100 * context["used"] / context["window"]
    return pct is not None and pct >= value
```

W `claude_auto_continue.DEFAULT_CONFIG` dopisz sześć kluczy handoveru (wartości z
„Ograniczeń globalnych”), w `load_config` obcinanie `("handover_timeout_min", 5, 240)`.
W `chatgpt_auto_continue.py` dodaj po imporcie:

```python
DEFAULT_CONFIG = dict(app.DEFAULT_CONFIG, handover_threshold="70%")
```

…i użyj go w `ChatGPTApp._load_config` przez `app.load_config(CONFIG_PATH, DEFAULT_CONFIG)`
(dodaj w `load_config` opcjonalny drugi parametr `defaults=None`, domyślnie
`DEFAULT_CONFIG`; `save_config` bez zmian).

- [ ] **Krok 4: Uruchom testy**

Uruchom: `py -3 -m unittest test_handover test_chatgpt test_sessions -v`
Oczekiwane: wszystko `ok`.

---

## Zadanie 8: `handover.py` — automat przekazania

**Pliki:**
- Zmień: `handover.py`, `strings.py`
- Zmień: `test_handover.py`

**Interfejsy:**
- Udostępnia:
  - `HANDOVER_REL = ".handover/HANDOVER-{id}.md"`
  - `new_id(now) -> str` (`"%Y%m%d-%H%M%S"`)
  - `sentinel_path(text, handover_id) -> str | None`
  - `file_ok(path, handover_id, since) -> bool`
  - `project_root(path) -> str | None`
  - `request_message(cfg, t, context_label, handover_id) -> str`
  - `continue_message(cfg, t, path, plan_folder) -> str`
  - `@dataclass HandoverState(id="", phase="idle", stops=0, reminders=0, work_s=0.0,
    path="", started=None, new_key="", context="")`
  - `class HandoverFlow(engine)` z `state_for(key)`, `active_key()`,
    `due(key, state, observed)`, `resume_text(key, session_state, observed, now)`,
    `step(key, session_state, observed, now, blocked)`
- Konsumuje: `engine.ui` (`stop`, `send`, `new_chat`, `resolve`), `engine.worker.cfg`,
  `engine.note`, `engine.worker.log`, `engine.worker.t`, `engine.worker.emit`.

- [ ] **Krok 1: Testy znacznika, pliku i katalogu projektu**

```python
class SentinelTests(unittest.TestCase):
    ID = "20260926-120000"
    PATH = "C:\\proj\\.handover\\HANDOVER-20260926-120000.md"

    def test_plain_line(self):
        text = "Gotowe. HANDOVER READY %s: %s" % (self.ID, self.PATH)
        self.assertEqual(handover.sentinel_path(text, self.ID), self.PATH)

    def test_path_in_backticks_and_split_nodes(self):
        text = "HANDOVER READY %s : `%s`" % (self.ID, self.PATH)
        self.assertEqual(handover.sentinel_path(text, self.ID), self.PATH)

    def test_other_id_is_refused(self):
        text = "HANDOVER READY 20200101-000000: %s" % self.PATH
        self.assertIsNone(handover.sentinel_path(text, self.ID))

    def test_no_marker(self):
        self.assertIsNone(handover.sentinel_path("prawie gotowe", self.ID))

    def test_file_must_exist_be_fresh_and_correctly_named(self):
        folder = os.path.join(tempfile.mkdtemp(), ".handover")
        os.makedirs(folder)
        good = os.path.join(folder, "HANDOVER-%s.md" % self.ID)
        since = dt.datetime.now() - dt.timedelta(minutes=1)
        self.assertFalse(handover.file_ok(good, self.ID, since))          # missing
        open(good, "w", encoding="utf-8").close()
        self.assertFalse(handover.file_ok(good, self.ID, since))          # empty
        with open(good, "w", encoding="utf-8") as f:
            f.write("stan pracy")
        self.assertTrue(handover.file_ok(good, self.ID, since))
        wrong = os.path.join(folder, "notes.md")
        with open(wrong, "w", encoding="utf-8") as f:
            f.write("stan pracy")
        self.assertFalse(handover.file_ok(wrong, self.ID, since))
        self.assertFalse(handover.file_ok(good, self.ID, dt.datetime.now() + dt.timedelta(hours=1)))

    def test_project_root_is_the_folder_holding_dot_handover(self):
        self.assertEqual(handover.project_root(self.PATH), "C:\\proj")
        self.assertIsNone(handover.project_root("C:\\proj\\notes\\HANDOVER-x.md"))
```

- [ ] **Krok 2: Uruchom — mają nie przejść, potem zaimplementuj**

Uruchom: `py -3 -m unittest test_handover.SentinelTests -v` → `AttributeError`.

```python
HANDOVER_DIR = ".handover"
HANDOVER_REL = HANDOVER_DIR + "/HANDOVER-{id}.md"
SENTINEL_RE = re.compile(r"HANDOVER\s+READY\s+(?P<id>\d{8}-\d{6})\s*:\s*(?P<path>\S[^\n]*?\.md)", re.I)
STRIP = "`\"'<>*  "


def new_id(now=None):
    return (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")


def sentinel_path(text, handover_id):
    """The path from the agreed marker line, or None. Only our own id counts."""
    for match in SENTINEL_RE.finditer(text or ""):
        if match["id"] == handover_id:
            return match["path"].strip(STRIP).strip()
    return None


def file_ok(path, handover_id, since):
    """The handover file exists, is not empty, was written after we asked and carries
    the name we asked for."""
    name = os.path.basename(path or "")
    if name.casefold() != ("HANDOVER-%s.md" % handover_id).casefold():
        return False
    if os.path.basename(os.path.dirname(path)).casefold() != HANDOVER_DIR:
        return False
    try:
        stat = os.stat(path)
    except OSError:
        return False
    fresh = dt.datetime.fromtimestamp(stat.st_mtime) >= since - dt.timedelta(minutes=1)
    return stat.st_size > 0 and fresh


def project_root(path):
    """The folder that holds .handover, i.e. the project the agent works in."""
    folder = os.path.dirname(path or "")
    if os.path.basename(folder).casefold() != HANDOVER_DIR:
        return None
    return os.path.dirname(folder) or None
```

Uruchom: `py -3 -m unittest test_handover.SentinelTests -v` → 6 testów `ok`.

- [ ] **Krok 3: Testy treści wiadomości**

```python
class MessageTests(unittest.TestCase):
    def worker_t(self, lang="pl"):
        return lambda key, **kw: app.tr(lang, key, **kw)

    def test_request_keeps_the_technical_tail_after_an_edited_body(self):
        cfg = dict(app.DEFAULT_CONFIG, handover_request_text="Zrób handover po swojemu.")
        text = handover.request_message(cfg, self.worker_t(), "712k / 1M", "20260926-120000")
        self.assertTrue(text.startswith("Zrób handover po swojemu."))
        self.assertIn(".handover/HANDOVER-20260926-120000.md", text)
        self.assertIn("HANDOVER READY 20260926-120000", text)
        self.assertNotIn("\n", text)

    def test_request_default_body_mentions_the_context(self):
        text = handover.request_message(dict(app.DEFAULT_CONFIG), self.worker_t(), "712k / 1M", "x")
        self.assertIn("712k / 1M", text)

    def test_continue_with_and_without_a_plan_folder(self):
        cfg = dict(app.DEFAULT_CONFIG)
        with_plan = handover.continue_message(cfg, self.worker_t(), "C:\\p\\.handover\\H.md", "C:\\p\\docs\\plan")
        self.assertIn("C:\\p\\docs\\plan", with_plan)
        self.assertIn("graphify", with_plan)
        without = handover.continue_message(cfg, self.worker_t(), "C:\\p\\.handover\\H.md", "")
        self.assertNotIn("plan", without.split("Handover")[0].casefold())
        self.assertIn("C:\\p\\.handover\\H.md", without)
```

- [ ] **Krok 4: Teksty handoveru w `strings.py` i funkcje treści**

Nowe klucze (en + pl): `handover_request_default` (z `{context}`),
`handover_request_tail` (z `{path}`, `{id}`), `handover_request_again`
(„dokończ handover” — wysyłane po resecie limitu), `handover_continue_default`,
`handover_continue_tail` (z `{path}`), `handover_continue_plan` (z `{plan}`),
`handover_continue_noplan`. Treści dosłownie ze specyfikacji, rozdział 4.6.

```python
def _one_line(*parts):
    return " ".join(" ".join(p for p in parts if p).split())


def request_message(cfg, t, context_label, handover_id):
    body = (cfg.get("handover_request_text") or "").strip() or t("handover_request_default",
                                                                 context=context_label)
    return _one_line(body, t("handover_request_tail",
                             path=HANDOVER_REL.format(id=handover_id), id=handover_id))


def continue_message(cfg, t, path, plan_folder):
    body = (cfg.get("handover_continue_text") or "").strip() or t("handover_continue_default")
    plan = t("handover_continue_plan", plan=plan_folder) if plan_folder else t("handover_continue_noplan")
    return _one_line(body, t("handover_continue_tail", path=path), plan)
```

Uruchom: `py -3 -m unittest test_handover.MessageTests -v` → 3 testy `ok`.

- [ ] **Krok 5: Testy automatu na atrapie adaptera**

```python
class FlowUI:
    """Adapter stand-in: records what the flow asked the window to do."""
    def __init__(self, new_key="code:Nowy czat"):
        self.calls, self.new_key, self.fail_new = [], new_key, None

    def stop(self, pane):
        self.calls.append(("stop", pane.key))
        return True

    def send(self, pane, message):
        self.calls.append(("send", pane.key, message))

    def new_chat(self, key, project, message):
        self.calls.append(("new_chat", key, project, message))
        if self.fail_new:
            raise RuntimeError(self.fail_new)
        return self.new_key


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(
            app.DEFAULT_CONFIG, handover_enabled=True, handover_threshold="700k"))
        self.worker.log = lambda *a, **kw: None
        self.engine = self.worker.engine
        self.ui = FlowUI()
        self.engine.ui = self.ui
        self.flow = self.engine.handover
        self.pane = pane("Alpha")
        self.state = SessionState()
        self.now = dt.datetime(2026, 9, 26, 12, 0, 0)

    def observed(self, **kw):
        base = dict(limit=False, reset=None, error=None, permanent=False, retry=None,
                    question=None, permission=None, busy=False, last_text="",
                    context=dict(used=712_000, window=1_000_000, pct=71))
        return base | kw

    def run_step(self, **kw):
        self.flow.step("code:Alpha", self.state, self.observed(**kw), self.now, self.pane, blocked=False)
```

Testy (każdy jeden `assert` na zachowanie):
1. `test_busy_chat_is_stopped_first` — `busy=True` → `("stop", "code:Alpha")`, faza `stopping`.
2. `test_idle_chat_gets_the_request` — faza `requested`, wysłana wiadomość zawiera
   `.handover/HANDOVER-`, `state_for(...).id` niepuste.
3. `test_below_threshold_does_nothing` — kontekst 300k → brak wywołań.
4. `test_option_off_does_nothing` — `handover_enabled=False` → brak wywołań.
5. `test_draft_question_permission_or_limit_block_the_start` — cztery przypadki, brak wywołań.
6. `test_already_handed_over_chat_is_left_alone` — `last_text` ze znacznikiem
   `HANDOVER READY` → brak wywołań.
7. `test_only_one_handover_at_a_time` — drugi klucz w `requested` → brak wywołań.
8. `test_marker_and_file_open_the_new_chat` — plik utworzony w `tmp/.handover`,
   `last_text` ze znacznikiem → `("new_chat", ...)` z katalogiem projektu i treścią
   zawierającą ścieżkę; faza `handed`, `new_key` w `state_for`.
9. `test_marker_without_a_file_reminds_once_then_needs_attention` — faza `reminded`,
   potem `attention`, na końcu `emit("beep")`.
10. `test_failed_new_chat_needs_attention_and_sends_nothing_more` —
    `ui.fail_new = "projekt niepotwierdzony"` → faza `attention`, brak dalszych wysyłek.
11. `test_timeout_counts_only_unblocked_scans` — `blocked=True` przez 40 minut nie
    kończy handoveru, po czym `blocked=False` i przekroczony limit → `attention`.
12. `test_resume_text_after_a_limit_reset` — w fazie `requested`
    `resume_text` = tekst „dokończ handover”; w fazie `idle` przy kontekście nad progiem
    `resume_text` = prośba o handover i faza staje się `requested`.
13. `test_three_failed_stops_need_attention` — `busy=True` w trzech kolejnych skanach
    z cofniętym czasem → `attention`.

- [ ] **Krok 6: Uruchom — mają nie przejść, potem napisz `HandoverFlow`**

Uruchom: `py -3 -m unittest test_handover.FlowTests -v` → `AttributeError: 'SessionEngine' object has no attribute 'handover'`.

Automat (fazy i przejścia dokładnie jak w specyfikacji 4.4). Szkielet:

```python
@dataclass
class HandoverState:
    id: str = ""
    phase: str = "idle"
    stops: int = 0
    reminders: int = 0
    work_s: float = 0.0
    path: str = ""
    started: object = None
    new_key: str = ""
    context: str = ""


class HandoverFlow:
    def __init__(self, engine):
        self.engine = engine
        self.states = {}
        self.last_seen = None      # datetime of the previous step, for the work clock

    # --- helpers -------------------------------------------------------
    def state_for(self, key):
        return self.states.setdefault(key, HandoverState())

    def active_key(self):
        return next((k for k, s in self.states.items()
                     if s.phase in ("stopping", "requested", "reminded", "ready")), None)

    def threshold(self):
        return parse_threshold(self.engine.worker.cfg.get("handover_threshold"))

    def context_label(self, context):
        ...   # "712k / 1M" or "71%"

    def due(self, key, observed):
        cfg = self.engine.worker.cfg
        return bool(cfg.get("handover_enabled") and cfg.get("auto_send")
                    and over_threshold(observed.get("context"), self.threshold())
                    and not observed.get("question") and not observed.get("permission")
                    and not observed.get("limit")
                    and not sentinel_any(observed.get("last_text"))
                    and self.active_key() in (None, key))
```

`step()` realizuje tabelę faz; każda wysyłka idzie przez `self.engine.ui.send(pane, text)`
i jest opisywana w liście rozmów przez `self.engine.note(key, session_state, <klucz>)`.
`attention` ustawia `session_state.phase = "exhausted"`, woła `emit("beep")` i notuje
`"handover_attention"`. Sukces ustawia `session_state.phase = "verifying"` dla nowego
klucza i notuje `"handover_done"` dla starego (etykieta „Przekazane → …”).
`sentinel_any(text)` — czy w tekście jest jakikolwiek znacznik `HANDOVER READY` (ochrona
z 4.3).

Nowe klucze tekstów: `handover_stopping`, `handover_requested`, `handover_reminded`,
`handover_attention`, `handover_done` (z `{title}`), `handover_over_threshold`
(wpis do logu z `{context}`), `handover_file` (`{path}`), `handover_new_chat` (`{title}`).

- [ ] **Krok 7: Uruchom testy automatu**

Uruchom: `py -3 -m unittest test_handover -v`
Oczekiwane: wszystkie testy `ok` (co najmniej 30).

---

## Zadanie 9: Adapter okien — `stop`, `send`, `new_chat`, wiadomość w `resume`

**Pliki:**
- Zmień: `session_automation.py` (`ClaudeUI`), `chatgpt_automation.py` (`ChatGPTUI`)
- Zmień: `test_handover.py` (klasa `AdapterTests`), `test_sessions.py` (wywołania `resume`)

**Interfejsy:**
- Udostępnia:
  - `ClaudeUI.stop(pane) -> bool` — klika przycisk zatrzymania w tym panelu.
  - `ClaudeUI.send(pane, message) -> None` — wpisuje i wysyła (ChatGPT dokłada
    `confirm_sent`).
  - `ClaudeUI.resume(key, prefer_retry=False, api_error=False, message=None)` — z
    `message` nigdy nie klika „Try again” i wysyła podany tekst.
  - `ClaudeUI.new_chat(key, project_root, message) -> str` — tworzy nową rozmowę w tym
    samym projekcie, wysyła `message` i zwraca klucz nowej rozmowy; przy braku
    potwierdzenia projektu podnosi `RuntimeError`.
  - Stałe: `STOP_LABELS`, `NEW_CHAT_LABELS`, `TITLE_WAIT_S = 120`.

- [ ] **Krok 1: Testy adaptera na sztucznym drzewie**

Testy: `stop` klika jedyny przycisk zatrzymania i zwraca `False`, gdy go nie ma;
`send` odmawia przy istniejącym szkicu; `resume(message=...)` nie klika „Try again”
nawet z `prefer_retry=True`; `new_chat` podnosi `RuntimeError`, gdy projekt nowej
rozmowy nie pasuje do katalogu projektu, i zwraca nowy klucz, gdy pasuje. Atrapa UIA:
`types.SimpleNamespace(IsEnabled=True, IsOffscreen=False, Click=..., HasKeyboardFocus=True,
GetValuePattern=...)` jak w `test_sessions.claude_question`.

- [ ] **Krok 2: Uruchom — mają nie przejść, potem zaimplementuj**

`STOP_LABELS = {"stop", "stop response", "stop generating", "zatrzymaj"}` (Claude) i
`BUSY_SUBMIT` (ChatGPT). `NEW_CHAT_LABELS` — dosłowne nazwy z Zadania 2.
`new_chat` krok po kroku zgodnie ze specyfikacją 4.7: klik w pole wiadomości starego
panelu, zapamiętanie kluczy paneli, klik „New”, odnalezienie panelu o nowym kluczu,
potwierdzenie projektu (`project_label(pane)` porównane z `os.path.basename(project_root)`
bez zważania na wielkość liter; przy niezgodności próba wyboru z listy projektów),
`send(pane, message)`, a potem dopytywanie o tytuł przez `TITLE_WAIT_S`.

- [ ] **Krok 3: Uruchom testy adaptera i regresję**

Uruchom: `py -3 -m unittest test_handover test_sessions test_chatgpt test_permissions -v`
Oczekiwane: wszystko `ok`.

---

## Zadanie 10: Wpięcie handoveru w silnik

**Pliki:**
- Zmień: `session_automation.py` (`SessionEngine.__init__`, `scan_one`, `step`, `reset`),
  `chatgpt_automation.py` (`ChatGPTEngine.scan_one` — kontekst z logów + wywołanie flow)
- Zmień: `test_handover.py` (klasa `EngineWiringTests`)

**Interfejsy:**
- Udostępnia: `SessionEngine.handover` (`HandoverFlow`), `SessionEngine.after_observe(key,
  state, observed, now, pane)` — wspólne domknięcie skanu wołane przez oba silniki.
- Konsumuje: `HandoverFlow.step`, `HandoverFlow.resume_text`.

- [ ] **Krok 1: Testy wpięcia**

1. `test_scan_one_runs_the_flow_after_step` — sztuczny panel z licznikiem nad progiem
   → `FlowUI` dostaje `send` z prośbą o handover.
2. `test_blocked_session_only_pauses_the_clock` — sesja w fazie `waiting` → flow
   dostaje `blocked=True`, nic nie wysyła.
3. `test_resume_after_reset_sends_the_handover_request` — `SessionState(phase="waiting",
   reason="limit", reset=…)` i kontekst nad progiem → `ui.resume` wywołane z
   `message=` zawierającym `.handover/`.
4. `test_new_key_replaces_the_old_one_in_selected_scope` — `watch_scope="selected"`,
   `selected_chats=["code:Alpha"]` → po przekazaniu lista to `["code:Nowy czat"]`.
5. `test_chatgpt_engine_fills_the_context_from_codex_logs` — `ChatGPTEngine` z atrapą
   `CodexLog.context_for` → `observed["context"]` uzupełniony przed `step`.

- [ ] **Krok 2: Uruchom — mają nie przejść, potem zaimplementuj**

W `SessionEngine.__init__`: `self.handover = HandoverFlow(self)`; w `reset()`:
`self.handover.states.clear()`.
Na końcu `SessionEngine.scan_one` zamień `self.step(...)` na:

```python
        self.step(key, state, observed, now)
        self.after_observe(key, state, observed, now, pane)
```

```python
    def after_observe(self, key, state, observed, now, pane):
        """Shared tail of a scan: the handover flow, which never acts on a conversation
        the scheduler is already busy with."""
        self.handover.step(key, state, observed, now, pane,
                           blocked=state.phase != "watching")
```

W `SessionEngine.step`, w miejscu wysyłki:

```python
        message = self.handover.resume_text(key, state, observed, now)
        try:
            result = self.ui.resume(key, cfg.get("prefer_try_again", False) and not message,
                                    state.reason == "api", message=message)
```

W `ChatGPTEngine.scan_one` przed `self.step(...)`:

```python
        observed["context"] = self.quota.context_for(pane.title)
```

…oraz na końcu to samo `self.after_observe(key, state, observed, now, pane)`.
Podmianę klucza w `selected_chats` robi flow po udanym `new_chat`:

```python
        chats = list(cfg.get("selected_chats", []))
        if key in chats:
            chats[chats.index(key)] = new_key
            cfg["selected_chats"] = chats
```

- [ ] **Krok 3: Uruchom cały zestaw**

Uruchom: `py -3 -m unittest test_handover test_input_guard test_sessions test_permissions test_chatgpt test_codex_log test_launcher test_detection test_quota_log test_chatgpt_limits test_send_after_reset`
Oczekiwane: `OK`, liczba testów ≥ 240.

---

## Zadanie 11: Ustawienia w oknie

**Pliki:**
- Zmień: `claude_auto_continue.py` (`App.FEATURES`, `_build_ui`, `_retext`, `_push_config`,
  `_help_icon` — nowe klucze), `strings.py`
- Zmień: `test_app_ui.py` (nowe sprawdzenia), `test_handover.py` (`SettingsTests`)

**Interfejsy:**
- Udostępnia: `App.var_threshold`, `App.var_plan`, `App.var_quiet`, `App.txt_handover_request`,
  `App.txt_handover_continue`, `App.FEATURES` z `"pause_on_foreign_input"` i
  `"handover_enabled"`.

- [ ] **Krok 1: Testy**

`SettingsTests` (bez Tk): obie funkcje działają w obu programach, więc test sprawdza, że
`"pause_on_foreign_input"` i `"handover_enabled"` są zarówno w `app.App.FEATURES`, jak i
w `chatgpt_auto_continue.ChatGPTApp.FEATURES` (tam obok `"retry_api_errors"`). Dodatkowo: teksty
`handover_enabled`, `handover_enabled_help`, `lbl_threshold`, `lbl_plan`, `btn_plan_pick`,
`btn_handover_texts`, `hint_threshold` istnieją w obu językach.
W `test_app_ui.py` (uruchamia prawdziwe okno Tk) dopisz: po wpisaniu `"70%"` w
`var_threshold` i `_push_config()` konfiguracja ma `handover_threshold == "70%"`;
po wpisaniu `"dużo"` konfiguracja zachowuje poprzednią wartość, a `lbl_threshold_hint`
pokazuje podpowiedź.

- [ ] **Krok 2: Uruchom — mają nie przejść, potem zbuduj widgety**

W `App.FEATURES` dodaj `"pause_on_foreign_input"` i `"handover_enabled"` (mechanizm
`feature_vars`/`feature_checks`/`feature_help` sam zrobi checkboxy z ikoną „?”).
W `_build_ui`, w `panel_in` (zakładka Ustawienia) po `row_start` dodaj:
- `row_quiet` — etykieta `lbl_quiet` + `ttk.Spinbox(from_=5, to=300, textvariable=self.var_quiet)`
  + `lbl_seconds`;
- `row_threshold` — etykieta `lbl_threshold` + `ttk.Entry(width=8, textvariable=self.var_threshold)`
  + `lbl_threshold_hint` (kolor `muted`, tekst `hint_threshold`);
- `row_plan` — etykieta `lbl_plan` + `ttk.Entry(textvariable=self.var_plan)` +
  `ttk.Button(text=btn_plan_pick, command=self._pick_plan_folder)`;
- `row_hand_texts` — `ttk.Button(text=btn_handover_texts, command=self._edit_handover_texts)`.

`_pick_plan_folder` → `tkinter.filedialog.askdirectory()`, wynik do `var_plan` +
`_push_config()`. `_edit_handover_texts` → `tk.Toplevel` z dwoma polami `tk.Text`
(prośba i instrukcja), przyciskiem „Przywróć domyślne” (czyści pole) i „Zapisz”
(`_push_config`). W `_push_config` dopisz do `payload`:

```python
            "foreign_input_quiet_s": quiet,
            "handover_threshold": threshold,
            "plan_folder": self.var_plan.get().strip(),
            "handover_request_text": self.txt_handover_request.get("1.0", "end-1c").strip(),
            "handover_continue_text": self.txt_handover_continue.get("1.0", "end-1c").strip(),
```

gdzie `threshold` to `self.var_threshold.get()` przyjęte tylko wtedy, gdy
`handover.parse_threshold(...)` zwraca wartość; w przeciwnym razie zostaje
`self.cfg["handover_threshold"]`, a `lbl_threshold_hint` dostaje kolor `red` i tekst
`hint_threshold`. W `_retext` dopisz wszystkie nowe etykiety.

- [ ] **Krok 3: Uruchom testy okna**

Uruchom: `py -3 test_app_ui.py` → `all tests passed`.
Uruchom: `py -3 -m unittest test_handover test_input_guard` → `OK`.

---

## Zadanie 12: README i sprawdzenie na żywo

**Pliki:**
- Zmień: `README.md`
- Zmień: `docs/superpowers/specs/2026-09-25-handover-i-pauza-design.md` (rozdział 12,
  jeśli sprawdzenie zmieni ustalenia)

- [ ] **Krok 1: README**

Dopisz do „How it works — in short” punkt o pauzie, nową sekcję **„Hand the work over
when the context fills up”** (próg, folder planu, co dostaje nowy czat, co znaczy
„Wymaga uwagi”) oraz wiersze w tabeli Settings dla wszystkich nowych opcji. W sekcji
„Important before leaving it overnight” dopisz, że program nie wchodzi w okno, gdy
Claude lub ChatGPT sterują komputerem.

- [ ] **Krok 2: Pauza na żywo**

Uruchom `py -3 claude_auto_continue.py`, kliknij „Start watching”, potem w tej sesji
wywołaj narzędzie computer-use (zrzut ekranu). W oknie programu ma pojawić się
„Wstrzymane — Claude używa komputera”, a w logu jeden wpis. Po zakończeniu —
„Mysz znów wolna”. Zapisz obie linie z logu.

- [ ] **Krok 3: Handover na żywo w projekcie testowym**

Do sprawdzenia użyj progu `20%` (niżej nie da się ustawić: próg jest obcinany do 20%)
w świeżej sesji Claude Code otwartej w katalogu testowym
(`%TEMP%\handover-test`) i poproś ją o kilka dużych odczytów, aż kontekst przekroczy
20%. Program ma: kliknąć Stop, wysłać prośbę, poczekać na plik
`.handover\HANDOVER-<id>.md`, otworzyć nową sesję w tym samym katalogu i wysłać
instrukcję. Sprawdź kolejno: plik istnieje i ma treść, nowa sesja jest w tym samym
projekcie, stara ma opis „Przekazane → …”, nowa jest obserwowana.

- [ ] **Krok 4: Kontrola pełnym zestawem testów**

Uruchom: `py -3 -m unittest test_handover test_input_guard test_sessions test_permissions test_chatgpt test_codex_log test_launcher test_detection test_quota_log test_chatgpt_limits test_send_after_reset`
Oczekiwane: `OK`.
Uruchom: `py -3 test_app_ui.py` i `py -3 test_usage_meter.py` → `all tests passed`.

- [ ] **Krok 5: Podsumowanie dla użytkownika**

Wypisz, co leży niezacommitowane (praca użytkownika nad `auto_permissions` osobno, moja
nad handoverem i pauzą osobno) i zaproponuj podział na dwa commity.

---

## Samoprzegląd planu

**Pokrycie specyfikacji:** 3.1 → Zadanie 3; 3.2 → Zadanie 4 (kroki 4–6); 3.3 → Zadanie 4
(kroki 7–8); 4.1 → Zadania 5 i 6; 4.2 → Zadanie 7; 4.3, 4.4 → Zadanie 8; 4.5 → Zadanie 8
(kroki 1–2); 4.6 → Zadanie 8 (kroki 3–4); 4.7 → Zadanie 9; 4.8 → Zadanie 10; 5 →
Zadania 1, 3, 8 (nowe pliki) i 4, 9, 10 (zmiany); 6 → Zadanie 11; 7 → rozproszone po
zadaniach 4, 8, 9 (zasady fail-closed mają własne testy w Zadaniu 8, kroki 5–6); 8 →
testy w każdym zadaniu; 9 → Zadanie 2; 10 → kolejność zadań; 11 → nic do zrobienia.

**Zgodność nazw:** `context_from_meter`, `parse_threshold`, `over_threshold`,
`sentinel_path`, `file_ok`, `project_root`, `request_message`, `continue_message`,
`HandoverState`, `HandoverFlow.step/resume_text/state_for/active_key`, `InputGuard.
mark_own_input/foreign_input_age_s/user_busy/ignore_user_for/pause_reason`,
`ClaudeComputerUse.active`, `overlay_visible`, `overlay_phrases`, `ClaudeUI.stop/send/
press/new_chat`, `CodexLog.context_for`, `SessionEngine.after_observe` — używane w
zadaniach 3–11 w tej samej postaci.

**Ryzyko nazw kontrolek:** Zadanie 2 ustala je przed Zadaniami 4 i 9; dopóki nie są
znane, `new_chat` i wykrywanie nakładki nie są pisane.
