"""Quick self-check for the bot. Run it from anywhere:

    python tests/smoke_test.py

It does NOT talk to Telegram and never touches your real bot.db (it uses a temporary database).
It checks that every file loads, every command handler can be built, and the core logic works.
GitHub runs it automatically on every pull request.
"""
import asyncio
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
for name in ("database", "brand", "buttons", "admin", "events", "announcements", "main"):
    try:
        modules[name] = __import__(name)
        check(f"import {name}", True)
    except Exception as error:  # noqa: BLE001 - we want to report any failure
        check(f"import {name}  ({type(error).__name__}: {error})", False)

if failed:
    print("\nSome files do not load, so the remaining checks were skipped.")
    sys.exit(1)

database, buttons, admin, events, announcements, brand = (
    modules[n] for n in ("database", "buttons", "admin", "events", "announcements", "brand")
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

rows = buttons.with_website(events.event_markup({"title": "T", "date": soon})).inline_keyboard
check("the button goes under other buttons", len(rows) == 2 and rows[1][0].text == buttons.BUTTON_TEXT)
check("link buttons are Club Blue", all(row[0].style == buttons.BLUE for row in rows))

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

linked = {"title": "Intro & <Q&A>", "date": soon, "url": "https://forms.gle/abc"}
rows = events.event_markup(linked).inline_keyboard
check("an event's link becomes the first button", rows[0][0].url == "https://forms.gle/abc" and len(rows) == 2)
check("an event without a link has only the calendar button", len(events.event_markup({"title": "T", "date": soon}).inline_keyboard) == 1)

tomorrow = {"title": "Intro & <Q&A>", "date": (today + timedelta(days=1)).isoformat()}
reminder = events._reminder_text(tomorrow, today)
check("the reminder has its label", "<code>REMINDER · TOMORROW</code>" in reminder)
check("the reminder escapes the event name", "<b>Intro &amp; &lt;Q&amp;A&gt;</b>" in reminder)
check("the reminder ends with the club footer", reminder.endswith(brand.FOOTER))
check("a same-day reminder says today", "REMINDER · TODAY" in events._reminder_text({**tomorrow, "date": today.isoformat()}, today))
card = events.announcement_text({**tomorrow, "description": "<b>Bring</b> a laptop."})
check("the event card has no countdown (it would go stale)", "<code>UPCOMING EVENT</code>\n" in card)
check("the event card shows the description with its formatting", "<b>Bring</b> a laptop." in card)
check("the event card has no 'save the date' filler", "Save the date" not in card)
check("the reminder leaves the description out",
      "Bring" not in events._reminder_text({**tomorrow, "description": "Bring"}, today))
check("the saved-event message has no calendar button",
      events.event_markup(linked, with_calendar=False).inline_keyboard == ((events.event_markup(linked).inline_keyboard[0][0],),))

# ------------------------------------------------------------------ brand
section("4b. Brand rules")
check("links without https:// are fixed", brand.clean_url("forms.gle/abc") == "https://forms.gle/abc")
check("links keep their path", brand.clean_url("https://cckfupm.vercel.app/events?x=1") == "https://cckfupm.vercel.app/events?x=1")
check("non-links are rejected", brand.clean_url("not a link") is None and brand.clean_url("hello") is None)
check("visible length ignores tags", brand.visible_length("<b>a &amp; b</b>") == 5)
texts = [events._reminder_text(tomorrow, today), events.announcement_text(linked), admin.HELP_TEXT]
check("no em dashes anywhere", not any("\u2014" in text for text in texts))

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

report = announcements.delivery_report("✅ Announcement sent!", 3, ["Club: Bot lacks permission to send."])
check("the report counts groups", "Sent to: 3 groups\nFailed: 1 group" in report)
check("a report with nothing sent says so", announcements.delivery_report("✅ Announcement sent!", 0, ["x"]).startswith("❌"))

check("a link on the last line becomes a button",
      announcements.split_link("Meet us!\nhttps://forms.gle/abc") == ("Meet us!", "https://forms.gle/abc"))
check("'link:' lines work too", announcements.split_link("Hi\nLink: forms.gle/abc") == ("Hi", "https://forms.gle/abc"))
check("a link inside a sentence stays", announcements.split_link("Go to https://x.com now")[1] is None)
check("a last word that isn't a link stays", announcements.split_link("See you\ntomorrow") == ("See you\ntomorrow", None))
check("&amp; in a link is decoded", announcements.split_link("Hi\nhttps://x.com/?a=1&amp;b=2")[1] == "https://x.com/?a=1&b=2")
try:
    announcements.split_link("Hi\nlink: nope")
    check("a broken 'link:' line is rejected", False)
except ValueError:
    check("a broken 'link:' line is rejected", True)

# ------------------------------------------------------------------ 6. admin-only
section("6. Admin-only access")
replies = []
reply_keyboards = []


async def record_reply(text, **kwargs):
    replies.append(text)
    reply_keyboards.append(kwargs.get("reply_markup"))


def fake_update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
        message=SimpleNamespace(reply_text=record_reply),
        effective_message=SimpleNamespace(reply_text=record_reply),
    )


asyncio.run(admin.help_command(fake_update(2), None))
check("/help ignores non-admins", replies == [])
asyncio.run(admin.help_command(fake_update(1), None))
check("/help lists every command for admins", len(replies) == 1 and "/events" in replies[0] and "/addadmin" in replies[0])
asyncio.run(admin.start_command(fake_update(2), None))
check("/start shows anyone their user ID", "<code>2</code>" in replies[-1])
replies.clear()
asyncio.run(events.list_events(fake_update(2), None))
check("/events is admin-only", replies == ["⛔ This command is for administrators only."])

# ------------------------------------------------------------------ 6b. adding an event
section("6b. Adding an event, step by step (fake Telegram)")


def fake_step(text=None, photo=None, caption=None, text_html=None):
    message = SimpleNamespace(text=text, text_html=text_html or text, caption=caption,
                              caption_html=caption, photo=photo, reply_text=record_reply)
    return SimpleNamespace(effective_user=SimpleNamespace(id=1), callback_query=None,
                           message=message, effective_message=message, effective_chat=SimpleNamespace(id=1))


saved = []


class SaveBot:
    async def send_message(self, **kwargs):
        saved.append(kwargs)

    async def send_photo(self, **kwargs):
        saved.append(kwargs)


step_context = SimpleNamespace(user_data={}, bot=SaveBot())
check("step 1 asks for the name", asyncio.run(events.add_event_start(fake_step("/addevent"), step_context)) == events.NAME)
check("step 2 asks for the date", asyncio.run(events.add_event_name(fake_step("Git Night"), step_context)) == events.DATE)
check("step 3 asks for the description",
      asyncio.run(events.add_event_date(fake_step(soon), step_context)) == events.DESCRIPTION)
too_long = fake_step("x" * (events.MAX_DESCRIPTION_LENGTH + 1))
check("a description that won't fit under a poster is refused",
      asyncio.run(events.add_event_description(too_long, step_context)) == events.DESCRIPTION)
check("step 4 asks for the poster and link together",
      asyncio.run(events.add_event_description(fake_step("Learn git", text_html="Learn <b>git</b>"), step_context))
      == events.POSTER_AND_LINK and "Poster and link" in replies[-1])
check("a caption that isn't a link is refused",
      asyncio.run(events.add_event_poster(fake_step(photo=[SimpleNamespace(file_id="P")], caption="hello"), step_context))
      == events.POSTER_AND_LINK)
asyncio.run(events.add_event_poster(fake_step(photo=[SimpleNamespace(file_id="P")], caption="forms.gle/xyz"), step_context))
git_night = next(e for e in database.get_upcoming_events(today.isoformat()) if e["title"] == "Git Night")
check("a photo with a link caption saves both, plus the formatted description",
      git_night["poster"] == "P" and git_night["url"] == "https://forms.gle/xyz" and git_night["description"] == "Learn <b>git</b>")
check("the saved message shows the link button but no calendar button",
      [row[0].text for row in saved[-1]["reply_markup"].inline_keyboard] == ["🔗 Event details"])

# ------------------------------------------------------------------ 7. what groups receive
section("7. Posts sent to groups (fake Telegram, nothing is really sent)")
sent = []


class FakeBot:
    async def send_message(self, **kwargs):
        sent.append(kwargs)

    async def send_photo(self, **kwargs):
        sent.append(kwargs)


def fake_message(text):
    return SimpleNamespace(text=text, text_html=text, caption=None, photo=None, video=None,
                           reply_text=record_reply)


def fake_command(text, user_id=1):
    message = fake_message(text)
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), callback_query=None,
                           message=message, effective_message=message)


context = SimpleNamespace(bot=FakeBot())
asyncio.run(announcements.announce(fake_command("/announce <b>Big</b> news\nhttps://forms.gle/abc"), context))
post_ = sent[-1]
rows = post_["reply_markup"].inline_keyboard
check("an announcement is posted in HTML, as written, with the club footer",
      post_["parse_mode"] == "HTML" and post_["text"].startswith("<b>Big</b> news")
      and post_["text"].endswith(brand.FOOTER))
check("its link and the website button are under it",
      rows[0][0].url == "https://forms.gle/abc" and rows[-1][0].text == buttons.BUTTON_TEXT)
check("the admin gets the report", replies[-1].startswith("✅ Announcement sent!\nSent to: 1 group"))

replies.clear()
asyncio.run(announcements.announce(fake_command("/announce event"), context))
check("'/announce event' lists the events to pick from", "Upcoming events" in replies[-1])

linked_id = database.add_event("Linked Night", soon, "POSTER_ID", "https://forms.gle/abc", "<i>Come</i> build.")
successful, failures = asyncio.run(announcements.send_event_to_groups(context.bot, database.get_event(linked_id)))
rows = sent[-1]["reply_markup"].inline_keyboard
check("'/announce event' posts the poster with the name and description",
      successful == 1 and sent[-1]["photo"] == "POSTER_ID"
      and "Linked Night" in sent[-1]["caption"] and "<i>Come</i> build." in sent[-1]["caption"])
check("'/announce event' adds the link, calendar and website buttons",
      [row[0].text for row in rows] == ["🔗 Event details", "📆 Add to Google Calendar", buttons.BUTTON_TEXT])

website_url = buttons.website_url()
check("replies to commands have no website button", not any(
    button.url == website_url
    for keyboard in reply_keyboards if keyboard
    for row in keyboard.inline_keyboard for button in row
))

gone = database.add_scheduled_message("Gone", "2000-01-01T00:00:00+00:00", event_id=999)
item = next(m for m in database.get_due_messages(datetime.now().astimezone().isoformat()) if m["id"] == gone)
successful, failures = asyncio.run(announcements.send_scheduled(context.bot, item))
check("a scheduled event that was deleted is reported, not sent", successful == 0 and "deleted" in failures[0])

# ------------------------------------------------------------------ result
print()
if failed:
    print(f"{len(failed)} check(s) FAILED:")
    for name in failed:
        print(f"  - {name}")
    sys.exit(1)
print(f"All {passed} checks passed.")
