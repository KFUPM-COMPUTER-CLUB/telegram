"""Quick self-check for the bot. Run it from anywhere:

    python tests/smoke_test.py

It does NOT talk to Telegram and never touches your real bot.db (it uses a temporary database).
It checks that every file loads, every command handler can be built, and the core logic works.
GitHub runs it automatically on every pull request.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

passed = 0
failed = []


def check(name, condition):
    global passed
    if condition:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}")


def section(title):
    print(f"\n{title}")


# ------------------------------------------------------------------ 1. everything loads
section("1. Every file loads and builds its handlers")
os.environ.pop("WEBSITE_URL", None)
modules = {}
for name in ("database", "buttons", "admin", "events", "announcements", "main"):
    try:
        modules[name] = __import__(name)
        check(f"import {name}", True)
    except Exception as error:  # noqa: BLE001 - we want to report any failure
        check(f"import {name}  ({type(error).__name__}: {error})", False)

if failed:
    print("\nSome files do not load, so the remaining checks were skipped.")
    sys.exit(1)

database, buttons, admin, events, announcements = (
    modules[n] for n in ("database", "buttons", "admin", "events", "announcements")
)

for name in ("events", "announcements", "admin", "buttons"):
    try:
        handlers = modules[name].get_handlers()
        check(f"{name}.get_handlers() builds {len(handlers)} handlers", len(handlers) > 0)
    except Exception as error:  # noqa: BLE001
        check(f"{name}.get_handlers()  ({type(error).__name__}: {error})", False)

# ------------------------------------------------------------------ 2. database
section("2. Database (temporary file, your bot.db is untouched)")
database.DB_PATH = os.path.join(tempfile.mkdtemp(), "smoke.db")
database.create_tables()
database.prepare_scheduled_messages()
database.create_tables()  # running twice must be harmless
database.prepare_scheduled_messages()
check("tables are created (twice is fine)", True)

database.add_admin(1, "Test")
check("admins", database.is_admin(1) and not database.is_admin(2))
database.register_group(-100, "Group")
check("groups", len(database.get_groups()) == 1)

soon = (events._today() + timedelta(days=3)).isoformat()
event_id = database.add_event("Smoke Night", soon, "POSTER_ID")
check("events", database.get_event(event_id)["poster"] == "POSTER_ID")
check("upcoming events", [e["id"] for e in database.get_upcoming_events(events._today().isoformat())] == [event_id])

database.mark_reminder_sent(event_id, soon)
check("a reminder is only sent once", database.get_events_needing_reminder([soon]) == [])

message_id = database.add_scheduled_message("hello", "2000-01-01T00:00:00+00:00")
due = database.get_due_messages(datetime.now().astimezone().isoformat())
check("scheduled message is due", [m["id"] for m in due] == [message_id])
check("a message can only be claimed once",
      database.claim_scheduled_message(message_id) and not database.claim_scheduled_message(message_id))

# ------------------------------------------------------------------ 3. website button
section("3. Website button")
rows = buttons.with_website().inline_keyboard
check("points to the club website by default", rows[0][0].url == "https://cckfupm.vercel.app/")

os.environ["WEBSITE_URL"] = "https://example.com/test"
check("WEBSITE_URL overrides it", buttons.website_url() == "https://example.com/test")
os.environ["WEBSITE_URL"] = "not-a-link"
check("a bad WEBSITE_URL falls back to the default", buttons.website_url() == buttons.DEFAULT_WEBSITE_URL)
os.environ.pop("WEBSITE_URL")

rows = buttons.with_website(events.calendar_markup({"title": "T", "date": soon})).inline_keyboard
check("the button goes under other buttons", len(rows) == 2 and rows[1][0].text == buttons.BUTTON_TEXT)

# ------------------------------------------------------------------ 4. events
section("4. Events")
url = urlparse(events.calendar_url({"title": "Intro & Q&A #1", "date": "2026-12-31"}))
query = parse_qs(url.query)
check("calendar link goes to Google Calendar", url.netloc == "calendar.google.com")
check("calendar link keeps the title intact", query["text"] == ["Intro & Q&A #1"])
check("calendar link covers the whole day (year end)", query["dates"] == ["20261231/20270101"])

today = events._today()
check("today is accepted as an event date", events._parse_date(today.isoformat())[0] == today)
check("a past date is rejected", events._parse_date((today - timedelta(days=1)).isoformat())[0] is None)
check("an impossible date is rejected", events._parse_date("2030-02-30")[0] is None)

# ------------------------------------------------------------------ 5. announcements
section("5. Announcements")
message = lambda text: SimpleNamespace(text=text, caption=None)  # noqa: E731
check("text after the command is found", announcements.command_content(message("/announce Hello there")) == "Hello there")
check("a new line after the command works", announcements.command_content(message("/announce\nHello")) == "Hello")
check("no text gives an empty result", announcements.command_content(message("/announce")) == "")

later = (datetime.now(announcements.SAUDI_TZ) + timedelta(days=2)).strftime(announcements.DATE_FORMAT)
check("dates are stored in UTC and shown in Saudi time",
      announcements.display_date(announcements.parse_date(later)) == later)
try:
    announcements.parse_date("2020-01-01 10:00")
    check("a past announcement date is rejected", False)
except ValueError:
    check("a past announcement date is rejected", True)

# ------------------------------------------------------------------ result
print()
if failed:
    print(f"{len(failed)} check(s) FAILED:")
    for name in failed:
        print(f"  - {name}")
    sys.exit(1)
print(f"All {passed} checks passed.")
