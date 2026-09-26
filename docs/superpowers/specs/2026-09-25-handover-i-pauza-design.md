# Handover przy pełnym kontekście i pauza, gdy ktoś inny steruje myszą

Specyfikacja dwóch funkcji Auto-Resume. Data: 2026-09-25.

## 1. Cel

Program pilnuje dziś limitu 5-godzinnego i wznawia przerwaną rozmowę. Dochodzą dwie
rzeczy:

- **Funkcja A — pauza.** Program nie rusza myszą ani klawiaturą, kiedy steruje nimi
  ktoś inny: agent przez computer-use albo człowiek. Wraca do pracy, gdy kontrola nad
  myszą znowu jest wolna.
- **Funkcja B — handover.** Gdy kontekst czatu przekroczy ustawiony próg, program
  przerywa pracę, każe agentowi napisać przekazanie dla następcy, otwiera nowy czat w
  tym samym projekcie i każe mu kontynuować pracę albo wziąć następną sekcję planu.

Funkcja A powstaje pierwsza, bo klikanie w funkcji B też musi ją respektować.

Obie funkcje działają w Claude Auto-Resume i w ChatGPT Auto-Resume (tryb Codex). Oba
programy mają osobne pliki konfiguracji, więc każdy może prowadzić inny projekt i mieć
inny próg.

## 2. Punkt wyjścia

Dzisiejszy układ, na którym obie funkcje się opierają:

- `session_automation.py` — czytanie okna Claude przez UI Automation (`ClaudeUI`),
  rozpoznawanie sygnałów w rozmowie (`signals`) i harmonogram dla każdej rozmowy
  osobno (`SessionEngine`, `SessionState`).
- `chatgpt_automation.py` — ten sam harmonogram dla okna ChatGPT.
- `input_lock.py` — nazwany muteks: dwa Auto-Resume nigdy nie piszą jednocześnie.
- `quota_log.py`, `codex_log.py` — czas resetu z lokalnych logów Claude Code i Codexa.
- `claude_auto_continue.py` — konfiguracja, tłumaczenia, wątek monitora i całe okno Tk.

Zasady, które zostają nienaruszone: żadne współrzędne ani obiekty UIA nie przetrwają
skanu, każda akcja odnajduje swoją rozmowę na nowo, a niejednoznaczny tytuł, cudzy
szkic i nieznana kontrolka oznaczają rezygnację z działania.

## 3. Funkcja A — pauza, gdy ktoś inny steruje myszą

### 3.1 Nowy moduł `input_guard.py`

Jedno zadanie: odpowiedzieć, czy wolno teraz użyć myszy i klawiatury, a jeśli nie, to
z jakiego powodu. Trzy sygnały:

1. **Człowiek.** `GetLastInputInfo` podaje czas ostatniego ruchu myszy lub klawisza,
   niezależnie od źródła. Program zapisuje czas swoich własnych kliknięć i wpisów
   (`mark_own_input`), więc wejście późniejsze niż własne, z zapasem 300 ms, uznaje za
   cudze. Oba Auto-Resume dzielą jeden znacznik własnego wejścia (mała wspólna pamięć
   nazwana, obok muteksu z `input_lock.py`), żeby kliknięcia jednego nie wstrzymywały
   drugiego.
2. **Claude używa computer-use.** Strażnik czyta końcówki plików `*.jsonl` w
   `~/.claude/projects` (albo `CLAUDE_CONFIG_DIR`), tylko tych zmienionych w ostatnich
   pięciu minutach, i szuka ostatniego wpisu `tool_use` o nazwie
   `mcp__computer-use__*`. Pauza trwa, dopóki tura z tym wywołaniem się nie skończyła i
   od wywołania minęło mniej niż `computer_use_hold_s`. Wpis `stop_reason: end_turn`
   po tym wywołaniu kończy pauzę od razu.
3. **ChatGPT używa computer-use.** Strażnik szuka widocznego okna z napisem
   `strings.usingComputer` z `~/.codex/computer-use/config.json` (domyślnie
   „ChatGPT is using your computer”; plik trzyma wersję w języku aplikacji). Przegląda
   tylko okna procesów `chatgpt.exe` i `codex.exe` mniejsze niż główne okno aplikacji.

`pause_reason()` zwraca `None` albo jeden z powodów: `"user"`, `"claude_cu"`,
`"chatgpt_cu"`. Sygnał 2 i 3 są sprawdzane co najwyżej raz na dwie sekundy i wynik jest
pamiętany, żeby skanowanie logów nie obciążało dysku.

Wznowienie wymaga `foreign_input_quiet_s` sekund ciszy od ostatniego cudzego wejścia
albo od zniknięcia sygnału 2 lub 3.

### 3.2 Punkty wpięcia

- `SessionEngine.tick()` — na wejściu pyta strażnika. Przy pauzie nie skanuje, oddaje
  stan `PAUSED` z powodem i sprawdza ponownie po ~3 sekundach. Czytanie okna nie
  szkodzi niczyjej pracy, ale pominięcie skanu jest prostsze i tańsze, a i tak nie ma
  co zrobić z odczytem.
- `ClaudeUI.click()` i `ClaudeUI.type_into()` — tuż przed każdym kliknięciem i każdą
  porcją tekstu sprawdzają wyłącznie sygnał 1 (jedno tanie wywołanie systemowe). Gdy
  ktoś właśnie złapał mysz, akcja kończy się wyjątkiem `InputPaused` przed wysłaniem
  czegokolwiek. To ta sama droga, którą dziś obsługiwane jest oczekujące polecenie Stop.
- `MonitorWorker._read_usage_panel()` (Claude) i `_read_usage_menu()` (ChatGPT) —
  tak samo, przed kliknięciem licznika i przyciskiem profilu.

`InputPaused` nie zużywa próby: `SessionEngine.step()` przy tym wyjątku przywraca
poprzednią wartość `state.attempts` i pozostawia fazę bez zmian, więc kolejny skan
spróbuje jeszcze raz.

Wysyłki zaplanowane na czas pauzy (np. minuta po resecie limitu) po prostu czekają,
aż pauza się skończy.

Ręczne „Wznów teraz…” pomija sygnał 1, bo to użytkownik właśnie kliknął w okno
programu. Sygnały 2 i 3 obowiązują również wtedy.

### 3.3 Widok w oknie

Nowy stan `PAUSED`: bursztynowa lampka i napis „Wstrzymane — Claude używa komputera”,
„Wstrzymane — ChatGPT używa komputera” albo „Wstrzymane — ktoś używa myszy”. W logu
jeden wpis na początku pauzy i jeden na końcu, nie na każdym skanie.

## 4. Funkcja B — handover przy pełnym kontekście

### 4.1 Odczyt kontekstu

**Claude.** Z nazwy przycisku licznika w pasku sesji, tą samą drogą, którą program
czyta dziś limit 5-godzinny. Nazwa ma postać:

```
Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit, Resets in 3 hr 35 min
```

Odczytujemy zużyte tokeny (195 800), rozmiar okna (1 000 000) i procent (20). Starszy
format bez liczb („context 20%”) daje tylko procent. Licznik jest szukany w obrębie
panelu danej rozmowy, więc w widoku podzielonym każda sesja ma własny odczyt.

Przy okazji poprawka w `session_automation.METER_CONTEXT`: dziś wzorzec usuwa z nazwy
licznika tylko „Context 195.8k” i zostawia „/ 1M (20%)”, więc procent kontekstu trafia
do puli procentów planu. Po zmianie wzorzec usuwa cały fragment o kontekście wraz z
rozmiarem okna i procentem w nawiasie. Bez tego kontekst blisko 100% mógłby zostać
wzięty za wyczerpany limit planu.

**ChatGPT (Codex).** Jeśli otwarta rozmowa pokazuje kontekst w oknie, bierzemy go
stamtąd (do sprawdzenia, rozdział 9). W przeciwnym razie z logów Codexa:

1. `~/.codex/session_index.jsonl` (albo `CODEX_HOME`) ma wiersze
   `{"id", "thread_name", "updated_at"}`. `thread_name` to tytuł czatu. Przy kilku
   wierszach o tym samym tytule wygrywa najnowszy `updated_at`; gdy nie ma żadnego,
   kontekstu nie znamy.
2. Plik sesji to ten z ostatnio zmienianych `rollout-*.jsonl`, którego nazwa kończy się
   na `<id>.jsonl`.
3. Z końcówki pliku bierzemy ostatni wpis `payload.type == "token_count"` z niepustym
   `info`: `info.last_token_usage.total_tokens` to zużycie, a
   `info.model_context_window` to rozmiar okna (u nas 258 400).

Jak w `quota_log.py`: czytamy tylko końcówki świeżych plików, tylko te wpisy, i nic z
nich nie zapisujemy.

### 4.2 Próg

Pole tekstowe w zakładce Ustawienia, edytowalne w każdej chwili, zapisywane do
konfiguracji i działające od następnego skanu.

- Przyjmowane zapisy: `700k`, `0.7M`, `700000`, `70%`. Procent liczy się od okna danej
  rozmowy, więc jedna wartość pasuje do Claude'a i do Codexa.
- Wartości początkowe: `700k` w Claude, `70%` w ChatGPT.
- Zakres po obcięciu: od 20 000 tokenów w górę, procenty od 20 do 95. Niski próg
  przekraczałby się w świeżym czacie i program przekazywałby pracę w kółko.
- Tekstu, którego nie da się odczytać, program nie zapisuje: pokazuje podpowiedź
  „np. 700k albo 70%” i pracuje na poprzedniej wartości.
- Próg w tokenach przy mniejszym oknie modelu (np. 200k) nigdy się nie spełni. To
  świadome: taki czat zwija kontekst sam.

### 4.3 Warunki startu

Handover rusza, gdy **wszystkie** są spełnione:

- `handover_enabled` i `auto_send` są włączone;
- rozmowa jest w zakresie obserwacji i nie ma fazy `exhausted`;
- kontekst jest znany i osiągnął próg;
- rozmowa nie czeka na reset limitu ani na ponowienie po błędzie API;
- nie ma otwartej karty z pytaniem;
- w polu wiadomości nie ma szkicu;
- ostatnia wiadomość rozmowy nie jest znacznikiem `HANDOVER READY` (ochrona przed
  drugim handoverem tej samej rozmowy, również po restarcie programu);
- w tym programie nie trwa inny handover.

Gdy próg zostanie przekroczony, a rozmowa czeka na reset limitu, to po resecie program
wysyła prośbę o handover zamiast wiadomości użytkownika.

### 4.4 Automat stanów

Stan handoveru jest trzymany osobno od `SessionState`, w `HandoverState` (klucz
rozmowy, id, faza, licznik przerwań, licznik przypomnień, czas pracy, ścieżka pliku).

| Faza | Co się dzieje | Wyjście |
|---|---|---|
| `idle` | brak handoveru | warunki z 4.3 spełnione → `stopping` (rozmowa pracuje) albo `requested` (rozmowa stoi) |
| `stopping` | kliknięty Stop | rozmowa przestała pracować → wysyłka prośby → `requested`; po 60 s ponowny Stop, najwyżej trzy razy → `attention` |
| `requested` | prośba wysłana | rozmowa stoi i znacznik oraz plik są w porządku → `ready`; rozmowa stoi bez znacznika → przypomnienie → `reminded` |
| `reminded` | jedno przypomnienie | jak wyżej; gdy rozmowa znów stanie bez znacznika → `attention` |
| `ready` | handover potwierdzony | udane otwarcie nowego czatu i wysyłka → `handed`; cokolwiek niepotwierdzone → `attention` |
| `handed` | praca przekazana | koniec; rozmowa ma opis „Przekazane → <tytuł>” |
| `attention` | potrzebna reakcja człowieka | koniec; sygnał dźwiękowy, opis „Wymaga uwagi” |

„Rozmowa stoi” znaczy w tej tabeli: nie ma przycisku Stop, czyli agent nie pracuje.

Kolejność w jednym skanie: najpierw dzisiejsza obsługa limitu i błędów API
(`SessionEngine.step`), potem automat handoveru. Stykają się w dwóch miejscach, oba po
stronie wiadomości wznawiającej, którą wysyła `step()` przez `state.resume_message`:

- handover jest w fazie `requested` albo `reminded`, a rozmowę zablokował limit →
  wznowienie wysyła tekst „dokończ handover”, nie wiadomość użytkownika;
- handoveru nie ma, ale kontekst jest już ponad progiem → wznowienie wysyła prośbę o
  handover, a automat przechodzi od razu do fazy `requested`. Dzięki temu po resecie
  limitu nie leci najpierw „continue”, żeby minutę później zostać przerwane Stopem.

Licznik `handover_timeout_min` (30 minut) tyka tylko w tych skanach, w których rozmowa
nie czeka na reset limitu ani na ponowienie po błędzie. Po przekroczeniu: `attention`.

Kliknięcia i wysyłki handoveru nie zużywają `state.attempts`; mają własne liczniki
(najwyżej trzy Stopy, najwyżej jedno przypomnienie).

### 4.5 Znacznik i plik

Identyfikator handoveru: `YYYYMMDD-HHMMSS` z chwili startu.

Program prosi o plik dokładnie pod nazwą `.handover/HANDOVER-<id>.md` w katalogu
projektu i o zakończenie odpowiedzi jedną linią zwykłego tekstu:

```
HANDOVER READY <id>: <pełna ścieżka pliku>
```

Rozpoznanie: teksty wszystkich węzłów ostatniej wiadomości są zlepiane w jeden ciąg i
przeszukiwane wzorcem `HANDOVER\s+READY\s+(?P<id>\d{8}-\d{6})\s*:\s*(?P<path>\S.*?\.md)`
bez zważania na wielkość liter. Zgadzać się musi identyfikator wydany tej rozmowie.
Ze ścieżki zdejmowane są otaczające znaki `` ` ``, `"`, `'`, `<`, `>`.

Plik jest przyjęty, gdy istnieje, ma niezerowy rozmiar, jego czas modyfikacji nie jest
starszy niż minuta przed wysłaniem prośby, a ścieżka kończy się na
`.handover/HANDOVER-<id>.md` (ukośnik w dowolnej postaci, wielkość liter bez znaczenia).
Inna nazwa albo inny katalog to powód do przypomnienia, nie do otwarcia nowego czatu.

**Katalog projektu** to katalog nadrzędny wobec `.handover`. Stąd program zna pełną
ścieżkę projektu starej rozmowy — pewniej niż z nazwy widocznej w oknie.

### 4.6 Wiadomości

Obie wiadomości mają część edytowalną (pola `handover_request_text` i
`handover_continue_text`) i stały dopisek techniczny, który program dokleja sam:
ścieżkę pliku, identyfikator i wzór linii ze znacznikiem, a w drugiej wiadomości
ścieżkę handoveru i folder planu. Edycja treści nie może zepsuć uzgodnienia.

Puste pole oznacza tekst domyślny z tłumaczeń, w języku interfejsu. Własny tekst
zostaje przy zmianie języka — tak jak dziś działa pole „Wiadomość do wysłania”.

Wiadomość jest wysyłana jako jedna, więc nowe linie zwijają się w odstępy, dokładnie
jak w dzisiejszym polu wiadomości.

**Domyślna prośba o handover** (część edytowalna, po polsku; wersja angielska w
tłumaczeniach):

> [Auto-Resume] Twój kontekst jest prawie pełny ({context}). Ta rozmowa kończy się
> tutaj, a pracę przejmie nowy agent. Przerwałem Cię celowo: najpierw upewnij się, że
> nic nie zostało zrobione w połowie. Napisz handover dla następcy: cel, etap planu, na
> którym jesteś, co zrobione, co w toku i w jakim dokładnie stanie (pliki,
> niezacommitowane zmiany, uruchomione procesy), kolejne kroki, podjęte decyzje i
> pułapki, jak sprawdzić efekt. Nie zaczynaj nowej pracy.

**Domyślna instrukcja dla nowego czatu:**

> [Auto-Resume] Przejmujesz pracę po innym agencie, któremu skończył się kontekst.
> Najpierw przeczytaj handover. Potem poznaj projekt skillem graphify — jeśli
> `graphify-out/` już istnieje, zaktualizuj go, nie buduj od zera — i sam sprawdź stan
> pracy, zamiast wierzyć notatkom na słowo. Dalej: jeśli zadanie z handoveru nie jest
> skończone, skończ je. Jeśli jest skończone, otwórz folder z planem, wybierz następną
> niezrobioną sekcję i zacznij ją. Gdy wszystkie sekcje są zrobione, zakończ pracę i
> napisz krótkie podsumowanie tego, co zostało dla człowieka. Po skończeniu sekcji
> odnotuj to w planie, żeby następny agent wiedział, co już zrobione. Pracuj
> samodzielnie, nikogo nie ma przy klawiaturze.

Gdy `plan_folder` jest puste, dopisek pomija zdania o planie: agent ma dokończyć
zadanie z handoveru i zatrzymać się.

### 4.7 Nowy czat

**Claude** (procedura zmierzona w oknie, rozdział 12):

1. Program klika w pole wiadomości starej rozmowy, żeby jej panel był aktywny. Pole
   jest wcześniej sprawdzone pod kątem szkicu, więc kliknięcie nic nie psuje.
2. Zapisuje klucze wszystkich otwartych paneli i klika **New** w pasku bocznym.
3. Rozpoznaje ekran nowej sesji: nie ma jeszcze tytułu ani klucza, ma pole `Prompt`
   i wiersz projektu, w którym po przycisku projektu stoi pole wyboru gałęzi.
4. Odczytuje przycisk projektu i porównuje jego nazwę, bez zważania na wielkość liter,
   z **nazwą katalogu** projektu z 4.5. Przy niezgodności klika ten przycisk i w
   otwartym menu wybiera pozycję o tej nazwie, po czym odczytuje przycisk ponownie.
   Pole gałęzi i „worktree” zostają tak, jak je zostawił użytkownik.
5. Bez zgodnej nazwy projektu nie wysyła nic: faza `attention`. Handover jest już
   zapisany, więc pracę można przejąć ręcznie.
6. Wpisuje instrukcję i wysyła ją dzisiejszą drogą, ze sprawdzeniem, czy tekst wyszedł
   z pola. Klucz nowej rozmowy poznaje, gdy aplikacja nada jej tytuł.

**ChatGPT (Codex):** tak samo, tylko projekt jest odczytywany z przycisku „Zmień
projekt: <nazwa>” / „Change project: <name>” na ekranie nowego czatu. Wybór miejsca
uruchomienia i gałęzi zostaje bez zmian. Wysyłkę potwierdza dzisiejsze
`ChatGPTUI.confirm_sent`.

### 4.8 Księgowanie po przekazaniu

- Nowa rozmowa dostaje świeży `SessionState` w fazie `verifying`, więc dzisiejszy
  mechanizm potwierdzi, że naprawdę ruszyła.
- Tytuł nowej rozmowy nadaje aplikacja po pierwszej wiadomości. Program dopytuje o
  niego przez dwie minuty. W trybie „tylko zaznaczone rozmowy” podmienia w liczonej w
  pamięci liście stary klucz na nowy; w trybie paneli nic nie trzeba robić, bo nowa
  sesja jest po prostu otwarta. Zaznaczenia nadal nie są zapisywane między
  uruchomieniami.
- Stara rozmowa dostaje fazę `handed` i opis „Przekazane → <tytuł>”; program już na
  nią nie działa.
- Kontekst nowej rozmowy jest pilnowany tak samo, więc łańcuch może iść dalej.
- W logu zostaje ślad każdego kroku: przekroczenie progu z liczbami, wysłanie prośby,
  przyjęcie pliku ze ścieżką, otwarcie nowego czatu z tytułem, wysłanie instrukcji.

## 5. Podział kodu

**Nowe pliki:**

- `input_guard.py` (~120 linii) — strażnik z rozdziału 3: czas cudzego wejścia,
  wspólny znacznik własnego wejścia obu programów, przegląd logów Claude Code,
  wykrywanie nakładki ChatGPT.
- `handover.py` (~250 linii) — rozbiór progu, odczyt kontekstu z tekstu licznika,
  rozpoznanie znacznika, budowa obu wiadomości, `HandoverState` i automat z 4.4. Bez
  wywołań UI Automation: z oknami rozmawia przez metody adaptera (`stop`, `send`,
  `new_chat`, `resolve`), więc testuje się na zwykłych danych.
- `strings.py` — przeniesione tabele tłumaczeń (patrz niżej).
- `test_handover.py`, `test_input_guard.py`.

**Zmiany:**

- `session_automation.py` — `signals()` zwraca dodatkowo `context` (zużycie, okno,
  procent) i `last_text` (zlepiony tekst ostatniej wiadomości); poprawka
  `METER_CONTEXT` z 4.1; `ClaudeUI` dostaje `stop()`, `send()` (wyjęte z `resume()`,
  która zaczyna z niego korzystać) i `new_chat()`; `SessionEngine.tick()` pyta
  strażnika, a `scan_one()` oddaje sterowanie automatowi handoveru po `step()`.
- `chatgpt_automation.py` — `signals()` uzupełnione o to samo, `ChatGPTUI.stop()`,
  `send()` i `new_chat()` dla ekranu nowego czatu Codexa.
- `codex_log.py` — funkcja czytająca kontekst rozmowy po jej tytule (4.1).
- `claude_auto_continue.py` — nowe klucze konfiguracji z obcinaniem zakresów, nowe
  widgety w zakładce Ustawienia, stan `PAUSED`, nowe opisy w liście rozmów.
- `chatgpt_auto_continue.py` — inne wartości domyślne progu i tekstów.
- `README.md` — opis obu funkcji i nowych ustawień.

**Porządki.** `claude_auto_continue.py` ma 1988 linii, z czego ćwierć to tabele
tłumaczeń, które urosną o kilkadziesiąt haseł w dwóch językach. `STRINGS` przenosimy do
`strings.py` bez zmiany treści i logiki; `claude_auto_continue.py` i
`chatgpt_auto_continue.py` biorą tabelę stamtąd. Zakres cięcia kończy się na tym
przeniesieniu.

## 6. Ustawienia

W zakładce Ustawienia, każde z ikoną **?** i wyjaśnieniem, jak pozostałe opcje:

| Opcja | Domyślnie | Klucz w pliku |
|---|---|---|
| Wstrzymuj, gdy ktoś inny używa myszy | włączone | `pause_on_foreign_input` |
| Czas spokoju (s) | 30 | `foreign_input_quiet_s` (5–300) |
| Przekazuj pracę nowemu czatowi | wyłączone | `handover_enabled` |
| Próg kontekstu | `700k` / `70%` w ChatGPT | `handover_threshold` |
| Folder z planem (przycisk „Wybierz…”) | puste | `plan_folder` |
| Edytuj wiadomości handoveru… | puste = tekst domyślny | `handover_request_text`, `handover_continue_text` |

Tylko w pliku konfiguracji: `computer_use_hold_s` (120, zakres 30–900) i
`handover_timeout_min` (30, zakres 5–240). Nowe klucze są zapisywane; `RUNTIME_ONLY`
zostaje bez zmian, więc zakres obserwacji i zaznaczenia nadal nie przechodzą między
uruchomieniami.

## 7. Zasady bezpieczeństwa i błędy

Cztery pierwsze zasady obowiązują dziś, cztery kolejne dochodzą:

1. Nie pisze po cudzym szkicu.
2. Nie pisze, gdy okno aplikacji nie jest na wierzchu.
3. Pomija rozmowy o niejednoznacznym tytule.
4. Przy nieznanym czasie resetu czeka, zamiast próbować na oślep.
5. Nie wysyła instrukcji do nowego czatu bez potwierdzonego projektu.
6. Nie otwiera nowego czatu bez znacznika w rozmowie i pliku na dysku.
7. Prowadzi jeden handover naraz.
8. Nie rusza myszą, gdy strażnik mówi „zajęte”.

Gdy czegoś nie da się odczytać:

- **Kontekst nieczytelny** — handover nie rusza, w logu jeden wpis. Czuwanie nad
  limitem działa dalej.
- **Logi Claude Code nieczytelne albo brak nakładki ChatGPT** — sygnały 2 i 3 uznane za
  nieaktywne, jedno ostrzeżenie w logu. Człowieka nadal pilnuje sygnał 1. Tak samo
  działa dziś odczyt czasu resetu: nieczytelny log nie może zatrzymać czuwania.

**„Wymaga uwagi”** z sygnałem dźwiękowym: brak znacznika po przypomnieniu, brak pliku
na dysku, niepotwierdzony projekt nowego czatu, przekroczenie 30 minut, trzy nieudane
próby zatrzymania rozmowy.

## 8. Testy

`unittest` na sztucznych drzewach `Node` i `FixtureUI`, tak jak dzisiejsze
`test_sessions.py`.

`test_handover.py`:

- rozbiór progu: `700k`, `0.7M`, `700000`, `70%`, tekst nieczytelny, obcinanie zakresu;
- odczyt kontekstu z obu formatów licznika i przy braku liczb;
- poprawka `METER_CONTEXT`: procent kontekstu nie trafia do procentów planu;
- kontekst Codexa z przykładowego `session_index.jsonl` i pliku sesji w katalogu
  tymczasowym, w tym brak wpisu o takim tytule i wpis z pustym `info`;
- znacznik: poprawny, z cudzym id, rozbity na kilka węzłów tekstowych, w cudzej
  wiadomości, w bloku kodu, ze ścieżką w odwrotnych apostrofach;
- plik: brak, pusty, starszy niż prośba, zła nazwa;
- automat: przekroczenie progu przy rozmowie pracującej i stojącej, przypomnienie,
  `attention` po drugim braku znacznika, limit w środku handoveru (wysyłany tekst
  „dokończ handover”), niepotwierdzony projekt (nic nie wysłane), jeden handover naraz,
  brak drugiego handoveru rozmowy zakończonej znacznikiem, licznik 30 minut
  nietykający w czasie czekania na reset.

`test_input_guard.py`:

- cudze wejście kontra własne przy sztucznym zegarze;
- wspólny znacznik dwóch programów;
- wpis `mcp__computer-use__` w trwającej turze, zwolnienie po `end_turn` i po
  `computer_use_hold_s`;
- nakładka ChatGPT obecna i nieobecna, napis czytany z `config.json`;
- pominięty skan nie zużywa próby i nie zmienia fazy;
- „Wznów teraz…” pomija sygnał 1, ale nie 2 i 3.

Uzupełnienia w `test_sessions.py`: `signals()` zwraca kontekst i tekst ostatniej
wiadomości; wznowienie po limicie używa tekstu handoveru, gdy handover trwa.

Na żywo, w duchu `test_live_claude.py`: jedna sesja computer-use przy włączonym
programie oraz jeden handover od początku do końca w małym projekcie testowym.

## 9. Do sprawdzenia na początku implementacji

Cztery rzeczy, których dziś nie wiemy na pewno. Żadna nie zmienia projektu, każda
dotyczy szczegółu odczytu okna:

1. Czy Claude Desktop pokazuje własną nakładkę przy computer-use. Jeśli tak, wykrywamy
   ją tak samo jak nakładkę ChatGPT (sygnał 3 obejmie oba).
2. Jak wygląda wskaźnik projektu na ekranie nowej sesji Claude i jak wybiera się z
   listy inny projekt.
3. Czy okno Codexa pokazuje kontekst rozmowy; jeśli nie, zostaje odczyt z logów.
4. Nazwy przycisków „nowy czat” w obu aplikacjach w obu wersjach językowych.

## 10. Kolejność prac

1. Przeniesienie `STRINGS` do `strings.py`; dzisiejsze testy przechodzą bez zmian.
2. `input_guard.py` z testami, wpięcie w `tick`, `click`, `type_into` i oba odczyty
   liczników; sprawdzenie na żywo.
3. Odczyt kontekstu i progu dla obu aplikacji, poprawka `METER_CONTEXT`, testy.
4. Automat handoveru po stronie starej rozmowy (Stop, prośba, znacznik, plik), testy.
5. Nowy czat z potwierdzeniem projektu i instrukcją, przejęcie obserwacji, testy.
6. Ustawienia, teksty pomocy, stan `PAUSED` w oknie, README.
7. Sprawdzenie całości na żywo.

## 11. Świadomie poza zakresem

- Osobny folder planu dla każdej rozmowy. Jeden projekt naraz, a oba programy mają
  osobne ustawienia.
- Handover w zwykłych czatach Claude i w czatach ChatGPT poza trybem Codex: nie mają
  projektu ani plików.
- Kopiowanie treści handoveru jako tekstu między czatami.
- Łagodniejsze przerywanie przez dopisanie wiadomości do trwającej tury. Przerywamy
  Stopem; kolejkowanie zostaje na później.
- Osobny plik z historią łańcucha przekazań: wystarczy log i pliki w `.handover`.
- Przekazywanie pracy między aplikacjami, np. z Claude do ChatGPT.

## 12. Ustalenia z okien

Zmierzone 2026-09-26 sondą `tools/probe_ui.py` na Claude Desktop 2.9939.2.0. Nazwy
kontrolek podane dosłownie; kod dopasowuje je bez zważania na wielkość liter.

### Claude — ekran nowej sesji

- Przycisk w pasku bocznym: `ButtonControl 'New'`.
- Ekran nowej sesji **nie ma jeszcze klucza rozmowy**: brakuje przycisku
  „…, rename session”, więc `discover()` nie zwraca dla niego panelu. Rozpoznajemy go po
  polu `EditControl 'Prompt'` i po wierszu projektu.
- Wiersz projektu, w kolejności: `ButtonControl 'Local'` (gdzie uruchomić),
  `ButtonControl '<projekt>'`, `ComboBoxControl '<gałąź>'`, `CheckBoxControl 'worktree'`
  („Work in an isolated copy of the repository”), `ButtonControl 'Add another folder'`,
  a dalej grupa z `EditControl 'Prompt'` i `ButtonControl 'Send'`.
- **Reguła na przycisk projektu:** ten `ButtonControl`, którego następnym rodzeństwem
  jest pole wyboru gałęzi (`ComboBoxControl`). Identyfikatory `AutomationId` są
  generowane (`_r_rq_`) i nie nadają się na punkt zaczepienia.
- Kliknięcie przycisku otwiera `MenuControl '<projekt>'` z pozycjami
  `RadioButtonControl 'No folder'`, grupą `GroupControl 'Recent'` i jedną pozycją
  `RadioButtonControl` na każdy ostatnio używany folder, a na końcu
  `MenuItemControl 'Open folder…'`. **Pozycje menu to nazwy folderów**, np. `auto-resume`.
- Uwaga: nagłówek otwartej sesji pokazuje **nazwę repozytorium**
  (`claude-desktop-auto-resume`), a nie nazwę folderu (`auto-resume`). Dlatego projekt
  potwierdzamy wyłącznie na ekranie nowej sesji, po nazwie katalogu.
- W otwartej sesji przycisk projektu to rodzeństwo przycisku „…, rename session”,
  po „More options for …”, obok `Remote Control`, `Terminal`, `Changes`, `Browser`,
  `View options`.
- Licznik pustej sesji: `Usage: Context 0, Weekly · all models: 51%, Resets Mon 6:00 PM`
  — „Context 0” bez rozmiaru okna, czytnik kontekstu musi to przyjąć.

### ChatGPT (Codex)

- Przycisk projektu: `ButtonControl 'Zmień projekt: <folder>'` (nazwa folderu jest w
  nazwie kontrolki), obok `ButtonControl 'Pracuj bez projektu'`,
  `ButtonControl 'Wybierz, gdzie uruchomić czat'`, `ButtonControl 'Przełącz gałąź'`,
  a kompozytor to `EditControl 'Zleć cokolwiek'`.
- **Niewiadoma:** nazwa przycisku „nowy czat” w pasku bocznym. Okno ChatGPT było w
  trakcie pomiaru zamknięte. Kod przyjmuje zestaw prawdopodobnych nazw, a przy braku
  jednoznacznego dopasowania odmawia działania i zgłasza „Wymaga uwagi”.
- Kontekst rozmowy Codexa bierzemy **tylko z logów** (`session_index.jsonl` + ostatni
  `token_count` w pliku sesji). Okno nie jest do tego potrzebne, więc nie ma drugiej
  drogi odczytu.

### Nakładka computer-use

- ChatGPT: napis z `~/.codex/computer-use/config.json`
  (`strings.usingComputer` = „ChatGPT is using your computer”, `escToCancel` = „Esc to
  cancel”).
- Claude: **nakładki nie ma** (sprawdzone na żywo 2026-09-26 przy włączonym
  computer-use). W trakcie sterowania ekranem nie pojawia się żadne nowe okno z
  napisem; `claude.exe` ma stale jedno okno rozciągnięte na cały pulpit
  (5120×1440 przy dwóch monitorach), którego tekst to samo „Claude”. Odpada ono z
  wykrywania po rozmiarze (nakładka może zajmować najwyżej 40% ekranu głównego), więc
  nie powoduje fałszywej pauzy. Computer-use Claude rozpoznaje więc sygnał 2 (log
  sesji) i sygnał 1 (wstrzykiwane wejście) — oba potwierdzone na żywo:

  ```
  [08:37:40] Wstrzymane: ktoś używa myszy. Nie klikam i nie piszę, dopóki to nie minie.
  [08:38:00] Wstrzymane: Claude używa komputera. Nie klikam i nie piszę, dopóki to nie minie.
  ```
