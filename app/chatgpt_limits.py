"""Text parsing for ChatGPT's usage limits (pure functions, no UI Automation).

The texts come from the ChatGPT desktop app (Codex mode), in English or Polish:

* The notice that ends a conversation rejected by the limit:
    "You've hit your usage limit. Upgrade your plan or add credits to continue,
     or try again at 9:33 AM."   /   "Osiągnięto limit użycia. Spróbuj ponownie później."
  ChatGPT prints the time only while the limit is still active; once it has
  cleared the same notice turns into "... try again later".
* The rows under "Usage remaining" / "Pozostały limit" in the profile menu
  (bottom-left user button):  "5h" "0%" "9:33 AM"  /  "5 godz." "0%" "09:33"
  and "Weekly" "69%" "Sep 28"  /  "Co tydzień" "69%" "28 wrz". The percentage is
  what is LEFT; the reset is a time when it is less than 24 h away, else a date.
* Codex's own messages in its local logs:
    "... or try again at Sep 10th, 2026 12:11 AM."
"""
import datetime as dt
import re

# Headline of the notice. Model-specific limits use the same headline ("...
# usage limit for GPT-X"); Polish puts the model first ("GPT-X ma już
# wyczerpany limit użycia"). An icon glyph may come first.
NOTICE = re.compile(
    r"^[\W_]*(?:You(?:'|’)ve hit your usage limit\b|Osiągnięto limit użycia\b|"
    r"\S.{0,60}? ma już wyczerpany limit użycia\b)", re.I)
TRY_AGAIN = re.compile(r"(?:try again|spróbuj ponownie)(?P<rest>.*)$", re.I | re.S)

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12,
          "sty": 1, "lut": 2, "kwi": 4, "maj": 5, "cze": 6, "lip": 7, "sie": 8,
          "wrz": 9, "paź": 10, "paz": 10, "lis": 11, "gru": 12}
_MONTH = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sty|lut|kwi|maj|cze|lip|sie|wrz|paź|paz|lis|gru)[a-ząćęłńóśźż]*\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th|\.)?"
_YEAR = r"(?:,?\s*(?P<year>20\d\d)\b)?"
DATE_MONTH_FIRST = re.compile(_MONTH + r"\s+" + _DAY + _YEAR, re.I)     # "Sep 10th, 2026"
DATE_DAY_FIRST = re.compile(r"\b" + _DAY + r"\s+" + _MONTH + _YEAR, re.I)  # "10 wrz 2026"
DATE_DOTTED = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d\d)\b")    # "10.09.2026"
DATE_SLASHED = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>20\d\d)\b")    # "9/10/2026"
# Times always use a colon (both locales); "10.09" is a Polish date, not 10:09.
CLOCK = re.compile(r"\b(?P<hh>\d{1,2}):(?P<mm>\d{2})(?:\s*(?P<ampm>[ap])\.?\s?m\b\.?)?", re.I)
NOW_WORDS = re.compile(r"^\s*(?:now|teraz)\s*$", re.I)
PERCENT = re.compile(r"^\s*(\d{1,3})\s*%\s*$")

# A clock time shown on screen is at most a few minutes in the past (rounding,
# a slow scan); anything earlier than that already means tomorrow.
CLOCK_SLACK = dt.timedelta(minutes=3)


def _clock(match):
    h, m = int(match["hh"]), int(match["mm"])
    ampm = (match["ampm"] or "").lower()
    if ampm == "p" and h < 12:
        h += 12
    elif ampm == "a" and h == 12:
        h = 0
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _date(text):
    """(month, day, year or None) of the first date in the text, else (None,) * 3."""
    for pattern in (DATE_DOTTED, DATE_SLASHED):
        m = pattern.search(text)
        if m:
            return int(m["month"]), int(m["day"]), int(m["year"])
    for pattern in (DATE_MONTH_FIRST, DATE_DAY_FIRST):
        m = pattern.search(text)
        if m:
            month = MONTHS.get(m["mon"].lower()[:3])
            if month:
                return month, int(m["day"]), int(m["year"]) if m["year"] else None
    return None, None, None


def parse_moment(text, now):
    """A reset moment written as a time ("9:33 AM", "09:33"), a date ("Sep 28",
    "28 wrz") or both ("Sep 10th, 2026 12:11 AM", "10 wrz o 00:11"). A bare time is
    the next such time; a bare date starts at midnight. Naive local datetime or None."""
    if NOW_WORDS.match(text):
        return now
    month, day, year = _date(text)
    clock_match = CLOCK.search(text)
    clock = _clock(clock_match) if clock_match else None
    if month:
        try:
            moment = dt.datetime(year or now.year, month, day, *(clock or (0, 0)))
        except ValueError:
            return None
        if year is None and moment < now - dt.timedelta(days=180):
            moment = moment.replace(year=moment.year + 1)   # "Jan 2" read in late December
        return moment
    if clock:
        moment = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if moment < now - CLOCK_SLACK:
            moment += dt.timedelta(days=1)
        return moment
    return None


def is_notice(text):
    return bool(NOTICE.match(text or ""))


def notice_reset(text, now):
    """When a limit notice says the limit clears ("try again at 9:33 AM"), else None
    ("try again later": the time is unknown, or the limit has already cleared)."""
    m = TRY_AGAIN.search(text or "")
    if not m:
        return None
    rest = m["rest"].strip()
    if re.match(r"(?:later|później)\b", rest, re.I):
        return None
    return parse_moment(rest[:80], now)


# ------------------------------------------------------------- profile menu rows

def window_minutes(label):
    """Length of a limit window from its menu label ("5h", "5 godz.", "Weekly",
    "Co tydzień", "2 Weeks", "Co 2 tygodnie"), or None when unknown."""
    text = (label or "").strip().casefold()
    patterns = ((r"(\d+)\s*(?:h|godz\.?)", 60), (r"(\d+)\s*(?:m|min)", 1), (r"(\d+)\s*d\.?", 1440),
                (r"(\d+)\s+weeks?", 10080), (r"co\s+(\d+)\s+tygod\w*", 10080),
                (r"(\d+)\s+months?", 43200), (r"(\d+)\s+years?", 525600))
    for pattern, unit in patterns:
        m = re.fullmatch(pattern, text)
        if m:
            return int(m.group(1)) * unit
    if text in ("weekly", "co tydzień"):
        return 10080
    if text in ("monthly", "miesięcznie", "co miesiąc"):
        return 43200
    if text in ("annual", "roczny", "co roku"):
        return 525600
    return None


MODEL_SECTION = re.compile(r"^\s*(?:Limit\s+(?P<pl>.+?)|(?P<en>.+?)\s+limit)\s*:\s*$", re.I)


def parse_usage_rows(texts, now):
    """Rows of the expanded "Usage remaining" section, from its texts in reading
    order: [label, "N%", reset?] per limit window, optionally preceded by a model
    section label ("GPT-X limit:"). Returns [dict(label, minutes, left, reset, model)]."""
    rows, label, model, current = [], None, None, None
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        pct = PERCENT.match(text)
        if pct:
            current = dict(label=label or "", minutes=window_minutes(label), left=min(100, int(pct.group(1))),
                           reset=None, model=model)
            rows.append(current)
            label = None
            continue
        if current is not None and current["reset"] is None and label is None:
            moment = parse_moment(text, now)
            if moment is not None:
                current["reset"] = moment
                continue
        section = MODEL_SECTION.match(text)
        if section:
            model, label, current = (section["pl"] or section["en"]).strip(), None, None
            continue
        label, current = text, None
    return rows


def summarize(rows):
    """UI rows in Claude Auto-Resume's shape: '5h' (the shortest window) and
    'weekly' (the longest), each {pct: percent USED, left, reset}."""
    core = [r for r in rows if r["model"] is None and r["minutes"]]
    out = {}
    if core:
        shortest = min(core, key=lambda r: r["minutes"])
        longest = max(core, key=lambda r: r["minutes"])
        for name, row in (("5h", shortest), ("weekly", longest)):
            if name == "weekly" and row is shortest:
                continue
            out[name] = dict(pct=100 - row["left"], left=row["left"],
                             **({"reset": row["reset"]} if row["reset"] else {}))
    return out


def blocked_until(rows):
    """(blocked, reset): whether any window is used up, and the latest reset among
    the used-up windows (None when a used-up window shows no reset)."""
    spent = [r for r in rows if r["left"] <= 0]
    if not spent:
        return False, None
    if any(r["reset"] is None for r in spent):
        return True, None
    return True, max(r["reset"] for r in spent)
