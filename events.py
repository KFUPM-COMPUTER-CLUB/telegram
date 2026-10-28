"""events.py - event commands (/addevent, /events, /deleteevent, /editevent)
and the automatic day-before reminders sent to all registered groups.

Messages here are HTML (see brand.py), so anything typed by a user goes through esc()."""
import logging
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import admin
import database
from brand import clean_url, esc, formatted_text, headline, kicker, post, visible_length
from buttons import GREEN, RED, action_button, link_button, with_website

logger = logging.getLogger(__name__)

HTML = ParseMode.HTML

# Conversation steps. ConversationHandler remembers which step each user is on and sends
# their next message to the matching function below.
NAME, DATE, DESCRIPTION, POSTER_AND_LINK = range(4)
(E_CHOOSE_EVENT, E_CHOOSE_FIELD, E_NEW_NAME, E_NEW_DATE, E_NEW_DESCRIPTION,
 E_NEW_LINK, E_NEW_POSTER) = range(10, 17)

MAX_YEARS_AHEAD = 10  # rejects typos like 20260
MAX_TITLE_LENGTH = 100
# A poster's caption is limited to 1024 characters; this leaves room for the name, date and footer.
MAX_DESCRIPTION_LENGTH = 700
BUTTONS_PER_ROW = 5

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

# Automatic reminders go out at this local time, the day before each event.
REMINDER_TIME = time(hour=9, minute=0, tzinfo=SAUDI_TZ)

BAD_LINK = "That doesn't look like a link. Send something like https://forms.gle/abc123, or /skip."


def _today():
    """Today's date in Saudi time, whatever timezone the computer running the bot uses.
    (date.today() would use the server's own timezone, which can be a day behind after midnight.)"""
    return datetime.now(SAUDI_TZ).date()


# Validation rules shared by /addevent and /editevent.

def _check_title(title):
    """Returns an error message, or None if the title is OK."""
    if not title:
        return "The name can't be empty. Send the event name."
    if len(title) > MAX_TITLE_LENGTH:
        return f"That name is {len(title)} characters. Keep it under {MAX_TITLE_LENGTH} and try again."
    return None


def _parse_date(text):
    """Checks a date typed as YYYY-MM-DD. Returns (date, None) if it's valid, or (None, error message)."""
    text = text.strip()
    today = _today()
    # Exactly a 4-digit year, 2-digit month and 2-digit day (so '2026-1-5' or '5/1/2026' are rejected).
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return None, f"Send the date as YYYY-MM-DD, e.g. {today.isoformat()}."
    try:
        parsed = date.fromisoformat(text)  # raises ValueError for 2026-02-30, month 13, etc.
    except ValueError:
        return None, f"{text} isn't a real date. Check the month and day (YYYY-MM-DD)."
    if parsed < today:
        return None, f"{text} has already passed. Send today ({today.isoformat()}) or later."
    if parsed.year > today.year + MAX_YEARS_AHEAD:
        return None, f"{parsed.year} is too far ahead. Send a year up to {today.year + MAX_YEARS_AHEAD}."
    return parsed, None


# ---------- How an event looks ----------

def _when(event_date, today):
    """Short hint like 'today', 'tomorrow' or 'in 5 days'."""
    days = (event_date - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _date_label(event):
    """'TUESDAY · 27 OCT 2026' in monospace."""
    event_date = date.fromisoformat(event["date"])
    return kicker(event_date.strftime("%A"), event_date.strftime("%d %b %Y"))


def event_card(event, label, message=None, with_description=True):
    """An event in the club's style:

        LABEL
        Python Workshop          (bold)
        TUESDAY · 27 OCT 2026

        description (if it has one)

        message
    """
    description = event.get("description") if with_description else None
    blocks = [label, f"{headline(event['title'])}\n{_date_label(event)}", description, message]
    return "\n\n".join(block for block in blocks if block)


def calendar_url(event):
    """A link that opens Google Calendar with this event already filled in, as an all-day event.
    Events only have a date (no time), so it is an all-day entry."""
    start = date.fromisoformat(event["date"])
    end = start + timedelta(days=1)  # Google's end date for all-day events is exclusive
    details = "Added from the KFUPM Computer Club Telegram bot."
    if event.get("url"):
        details += f"\n{event['url']}"
    params = {
        "action": "TEMPLATE",
        "text": event["title"],
        "dates": f"{start:%Y%m%d}/{end:%Y%m%d}",
        "details": details,
    }
    # quote (not the default quote_plus) so spaces become %20; titles may hold Arabic, emoji, & or #.
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


def event_markup(event, with_calendar=True):
    """The event's buttons: its link (if it has one) and 'Add to Google Calendar'.
    Returns None if there are no buttons."""
    rows = []
    if event.get("url"):
        rows.append([link_button("🔗 Event details", event["url"])])
    if with_calendar:
        rows.append([link_button("📆 Add to Google Calendar", calendar_url(event))])
    return InlineKeyboardMarkup(rows) if rows else None


def announcement_text(event):
    """The event card posted to groups by '/announce event'. No 'in 5 days': a sent message
    never changes, so it would soon be wrong."""
    return post(event_card(event, kicker("Upcoming event")))


def _reminder_text(event, today):
    """The reminder posted to groups the day before (or the same day, if the bot was off)."""
    when = "today" if date.fromisoformat(event["date"]) == today else "tomorrow"
    return post(event_card(event, "🔔 " + kicker("Reminder", when), with_description=False))


async def _send_event(bot, chat_id, text, event, reply_markup=None):
    """Sends the poster photo with text as its caption, or just the text if the event has
    no poster. text is HTML. Works for private chats and groups alike."""
    if event.get("poster"):
        await bot.send_photo(chat_id=chat_id, photo=event["poster"], caption=text,
                             parse_mode=HTML, reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=HTML, reply_markup=reply_markup)


async def _reply(update, text, reply_markup=None):
    await update.message.reply_text(text, parse_mode=HTML, reply_markup=reply_markup)


# ---------- Add event conversation ----------

@admin.admin_only
async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: the /addevent command."""
    context.user_data["new_event"] = {}  # per-user scratch space for the answers
    await _reply(update, f"{kicker('New event', 'step 1 of 4')}\n\n📝 What's the event called?\n\n/cancel to stop.")
    return NAME


async def add_event_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await _reply(update, esc(error))
        return NAME

    context.user_data["new_event"]["title"] = title
    await _reply(
        update,
        f"{kicker('New event', 'step 2 of 4')}\n\n📅 When is {headline(title)}?\n"
        f"Send the date as YYYY-MM-DD, e.g. {_today().isoformat()}.\n\n/cancel to stop.",
    )
    return DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event_date, error = _parse_date(update.message.text)
    if error:
        await _reply(update, esc(error))
        return DATE

    # the date is kept until the last step; saving happens in _save_new_event
    context.user_data["new_event"]["date"] = event_date.isoformat()
    await _reply(
        update,
        f"{kicker('New event', 'step 3 of 4')}\n\n✍️ Describe the event. This is what people read "
        "when you announce it. Bold, italics and links are kept.\n\n/cancel to stop.",
    )
    return DESCRIPTION


def _check_description(message):
    """Returns (description as HTML, None) if it's OK, or (None, error message)."""
    description = formatted_text(message)
    if not description:
        return None, "The description can't be empty. Send a few words about the event."
    length = visible_length(description)
    if length > MAX_DESCRIPTION_LENGTH:
        return None, (f"That's {length} characters. Keep it under {MAX_DESCRIPTION_LENGTH} "
                      "so it fits under the poster, and send it again.")
    return description, None


async def add_event_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description, error = _check_description(update.message)
    if error:
        await _reply(update, esc(error))
        return DESCRIPTION

    context.user_data["new_event"]["description"] = description
    await _reply(
        update,
        f"{kicker('New event', 'step 4 of 4')}\n\n🖼 Last step: the poster and the link. Both are optional.\n\n"
        "• <b>Poster and link:</b> send the poster as a photo, with the link as its caption.\n"
        "• <b>Only a poster:</b> send the photo.\n"
        "• <b>Only a link:</b> send the link.\n"
        "• <b>Neither:</b> send /skip.\n\n"
        "The link becomes a button under the event.",
    )
    return POSTER_AND_LINK


async def add_event_description_not_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Send the description as a text message. The poster comes next.")
    return DESCRIPTION


async def add_event_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo arrived, maybe with the link as its caption: save the event."""
    url = None
    if update.message.caption:
        url = clean_url(update.message.caption)
        if url is None:
            await _reply(update, "The caption should be just the link, e.g. https://forms.gle/abc123. "
                                 "Send the photo again with a working link, or without a caption.")
            return POSTER_AND_LINK

    context.user_data["new_event"].update(poster=update.message.photo[-1].file_id, url=url)
    return await _save_new_event(update, context)


async def add_event_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text arrived: it should be a link (an event without a poster)."""
    url = clean_url(update.message.text)
    if url is None:
        await _reply(update, esc(BAD_LINK))
        return POSTER_AND_LINK

    context.user_data["new_event"].update(poster=None, url=url)
    return await _save_new_event(update, context)


async def add_event_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/skip: save the event without a poster or a link."""
    context.user_data["new_event"].update(poster=None, url=None)
    return await _save_new_event(update, context)


async def add_event_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Send the poster as a photo (not as a file), the link as text, or /skip.")
    return POSTER_AND_LINK


async def _save_new_event(update, context):
    event = context.user_data.pop("new_event")
    event["id"] = database.add_event(event["title"], event["date"], event["poster"], event["url"], event["description"])
    logger.info("Event #%s '%s' added for %s (poster: %s, link: %s).",
                event["id"], event["title"], event["date"], bool(event["poster"]), bool(event["url"]))

    await _send_event(
        context.bot, update.effective_chat.id,
        event_card(
            event, "✅ " + kicker("Event saved"),
            "Groups get a reminder the day before.\n"
            "Want everyone to know now? Send /announce event.",
        ),
        event,
        reply_markup=event_markup(event, with_calendar=False),
    )
    return ConversationHandler.END


async def add_event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel at any step leaves the conversation without saving."""
    context.user_data.pop("new_event", None)
    await _reply(update, "❌ Cancelled. Nothing was saved.\nUse /help to see the commands.")
    return ConversationHandler.END


# ---------- /events ----------

def _format_events(events, today):
    """Numbered list text shared by /events, /editevent and '/announce event'."""
    lines = [f"📅 {headline('Upcoming events')} ({len(events)})"]
    for i, event in enumerate(events, start=1):
        event_date = date.fromisoformat(event["date"])
        when = kicker(event_date.strftime("%a %d %b"), _when(event_date, today))
        link = " 🔗" if event.get("url") else ""
        lines.append(f"\n<b>{i}.</b> {esc(event['title'])}{link}\n{when}")
    return "\n".join(lines)


def _numbered_buttons(events, prefix):
    """Rows of numbered buttons; each carries '<prefix>:<event id>'."""  # added: shared by several commands
    buttons = [
        action_button(str(i), f"{prefix}:{event['id']}")
        for i, event in enumerate(events, start=1)
    ]
    return [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]


def event_list(prefix, prompt):
    """(text, keyboard) listing the upcoming events with a numbered button each,
    or (None, None) if there are none. Used here and by announcements.py."""
    today = _today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        return None, None
    text = _format_events(events, today) + f"\n\n{prompt}"
    return text, InlineKeyboardMarkup(_numbered_buttons(events, prefix))


NO_EVENTS = "📅 No upcoming events yet. Add one with /addevent."


@admin.admin_only
async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/events - shows upcoming events, soonest first, with a button per event to see its
    full info (poster, title, date, link)."""
    text, keyboard = event_list("viewevent", "👀 Tap a number to open the event.")
    if text is None:
        await _reply(update, NO_EVENTS)
        return
    await _reply(update, text, keyboard)


@admin.admin_only
async def view_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number under /events was pressed: send that event's poster, title, date and buttons."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(int(query.data.split(":")[1]))
    if event is None:
        await query.message.reply_text("⚠️ That event no longer exists. Send /events for the current list.")
        return
    when = _when(date.fromisoformat(event["date"]), _today())
    text = event_card(event, kicker("Event", when), None if event.get("poster") else "<i>No poster yet.</i>")
    await _send_event(context.bot, query.message.chat_id, text, event, reply_markup=event_markup(event))


# ---------- /deleteevent ----------

@admin.admin_only
async def delete_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deleteevent - shows one red button per upcoming event, labelled with the event's name."""
    events = database.get_upcoming_events(_today().isoformat())
    if not events:
        await _reply(update, "📅 No upcoming events to delete.")
        return

    # Each button's callback_data carries the event id, e.g. "delevent:7", so the press
    # can be matched to the right row. One button per row, since names can be long.
    buttons = [
        [action_button(f"🗑 {event['title']}", f"delevent:{event['id']}", style=RED)]
        for event in events
    ]
    await _reply(update, "Tap the event to delete. This can't be undone.", InlineKeyboardMarkup(buttons))


@admin.admin_only
async def delete_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when an event button is pressed. Deletes that event and replaces the list with a result."""
    query = update.callback_query
    await query.answer()  # stops the button's loading spinner
    event_id = int(query.data.split(":")[1])

    event = database.get_event(event_id)
    if event is None or database.delete_event(event_id) == 0:
        await query.edit_message_text("⚠️ That event was already deleted.")
        return

    logger.info("Event #%s '%s' deleted.", event_id, event["title"])
    await query.edit_message_text(  # editing also removes the buttons
        event_card(event, "🗑 " + kicker("Event deleted")),
        parse_mode=HTML,
    )


# ---------- /editevent ----------

FIELD_NAMES = ("name", "date", "description", "link", "poster")


def _field_buttons(exclude=None, with_done=False):
    """Name/Date/Description/Link/Poster buttons (minus the one just changed) and optionally 'No change'."""
    fields = [
        action_button(field.capitalize(), f"editfield:{field}")
        for field in FIELD_NAMES
        if field != exclude
    ]
    rows = [fields[i : i + 2] for i in range(0, len(fields), 2)]
    if with_done:
        rows.append([action_button("✅ No change", "editdone", style=GREEN)])
    return InlineKeyboardMarkup([row for row in rows if row])


async def _show(query, text, reply_markup=None):
    """Replaces the message the button was on. A photo message can't become text, so there the
    buttons are removed and a new message is sent instead. text is HTML."""
    if query.message.photo:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(text, parse_mode=HTML, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, parse_mode=HTML, reply_markup=reply_markup)


async def _ask_more_changes(update, context, event, changed, result):
    """After a successful change: show the result and offer the other fields or 'No change'."""
    await _send_event(
        context.bot, update.effective_chat.id,
        event_card(event, "✅ " + kicker(result), "Change anything else? (or /cancel)"),
        event,
        reply_markup=_field_buttons(exclude=changed, with_done=True),
    )
    return E_CHOOSE_FIELD


async def _event_gone(update, context):
    await update.effective_message.reply_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
    context.user_data.pop("edit_event", None)
    return ConversationHandler.END


@admin.admin_only
async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/editevent - shows the upcoming list with one numbered button per event."""
    text, keyboard = event_list("editevent", "✏️ Tap the number of the event to edit (or /cancel).")
    if text is None:
        await _reply(update, "📅 No upcoming events to edit.")
        return ConversationHandler.END

    context.user_data.pop("edit_event", None)
    await _reply(update, text, keyboard)
    return E_CHOOSE_EVENT


async def edit_event_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A numbered button was pressed: ask which field to change."""
    query = update.callback_query
    await query.answer()
    event_id = int(query.data.split(":")[1])
    event = database.get_event(event_id)
    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists.")
        return ConversationHandler.END

    context.user_data["edit_event"] = event_id
    await query.edit_message_text(
        event_card(event, "✏️ " + kicker("Editing"), "What do you want to change? (or /cancel)"),
        parse_mode=HTML,
        reply_markup=_field_buttons(),
    )
    return E_CHOOSE_FIELD


async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Name, Date, Description, Link or Poster button."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data["edit_event"])
    if event is None:
        await _show(query, "⚠️ That event no longer exists.")
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    if query.data == "editfield:name":
        await _show(query, f"Send the new name for {headline(event['title'])} (or /cancel).")
        return E_NEW_NAME

    if query.data == "editfield:description":
        await _show(
            query,
            "✍️ Send the new description. Bold, italics and links are kept. (or /cancel)",
        )
        return E_NEW_DESCRIPTION

    if query.data == "editfield:link":
        if event.get("url"):
            current = f"Current link: {esc(event['url'])}\n\nSend the new link, or /remove to delete it."
        else:
            current = "This event has no link yet. Send one (or /cancel)."
        await _show(query, f"🔗 {current}")
        return E_NEW_LINK

    if query.data == "editfield:poster":
        current = "The new poster replaces the current one." if event.get("poster") \
            else "This event has no poster yet."
        await _show(query, f"🖼 {current}\nSend it as a photo (not as a file), or /cancel.")
        return E_NEW_POSTER

    event_date = date.fromisoformat(event["date"])
    await _show(
        query,
        f"📅 Current date: {event_date.isoformat()} ({event_date.strftime('%A')})\n\n"
        "Send the new date as YYYY-MM-DD (or /cancel).",
    )
    return E_NEW_DATE


async def edit_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await _reply(update, esc(error))
        return E_NEW_NAME

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        return await _event_gone(update, context)

    database.update_event_title(event_id, title)
    logger.info("Event #%s renamed '%s' -> '%s'.", event_id, event["title"], title)
    event["title"] = title
    return await _ask_more_changes(update, context, event, "name", "Name updated")


async def edit_new_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """New date typed as YYYY-MM-DD: validate it and save."""
    new_date, error = _parse_date(update.message.text)
    if error:
        await _reply(update, esc(error))
        return E_NEW_DATE

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        return await _event_gone(update, context)

    database.update_event_date(event_id, new_date.isoformat())
    logger.info("Event #%s date %s -> %s.", event_id, event["date"], new_date)
    event["date"] = new_date.isoformat()
    return await _ask_more_changes(update, context, event, "date", "Date updated")


async def edit_new_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description, error = _check_description(update.message)
    if error:
        await _reply(update, esc(error))
        return E_NEW_DESCRIPTION

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        return await _event_gone(update, context)

    database.update_event_description(event_id, description)
    logger.info("Event #%s description updated.", event_id)
    event["description"] = description
    return await _ask_more_changes(update, context, event, "description", "Description updated")


async def edit_new_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A link (or /remove) arrived for the Link step."""
    removing = update.message.text.startswith("/")
    url = None if removing else clean_url(update.message.text)
    if not removing and url is None:
        await _reply(update, "That doesn't look like a link. Send something like https://forms.gle/abc123, or /cancel.")
        return E_NEW_LINK

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        return await _event_gone(update, context)

    database.update_event_url(event_id, url)
    logger.info("Event #%s link %s.", event_id, "removed" if removing else "updated")
    event["url"] = url
    return await _ask_more_changes(update, context, event, "link", "Link removed" if removing else "Link updated")


async def edit_new_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo arrived for the Poster step: save its file_id."""
    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        return await _event_gone(update, context)

    event["poster"] = update.message.photo[-1].file_id
    database.update_event_poster(event_id, event["poster"])
    logger.info("Event #%s poster updated.", event_id)
    return await _ask_more_changes(update, context, event, "poster", "Poster updated")


async def edit_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Send the poster as a photo, or /cancel.")
    return E_NEW_POSTER


async def edit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'No change' button: finish editing."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data.get("edit_event"))
    text = event_card(event, "✅ " + kicker("All done")) if event else "✅ Editing finished."
    await _show(query, text)
    context.user_data.pop("edit_event", None)
    return ConversationHandler.END


async def edit_tap_a_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text typed while the bot is waiting for a button press."""
    await _reply(update, "Tap one of the buttons above, or send /cancel.")
    return None  # stay on the same step


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("edit_event", None)
    await _reply(update, "❌ Edit cancelled. Changes already saved are kept.")
    return ConversationHandler.END


async def edit_other_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any other command during an edit ends the edit, then the command itself runs normally.
    The update is put back on the bot's queue; by then the conversation is over, so the
    command's own handler (e.g. /help) picks it up."""
    context.user_data.pop("edit_event", None)
    await context.application.update_queue.put(update)
    return ConversationHandler.END


async def edit_menu_expired(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """An edit button pressed after its conversation ended (old message, or another user)."""
    await update.callback_query.answer()
    await _show(update.callback_query, "⌛ This menu has expired. Send /editevent to start again.")


# ---------- Reminders ----------

async def _broadcast(bot, text, event):
    """Sends one event (poster + text, or text only) to every registered group, with the
    event's buttons and the website button. Returns (sent, failed) counts."""
    sent = failed = 0
    for group in database.get_groups():
        try:
            await _send_event(bot, group["group_id"], text, event, reply_markup=with_website(event_markup(event)))
            sent += 1
        except TelegramError as error:
            failed += 1
            logger.warning("Could not send to group %s (%s): %s", group["title"], group["group_id"], error)
    return sent, failed


async def send_due_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Job: reminds all groups about events happening tomorrow (or today, if the bot was
    off yesterday). Each (event, date) is reminded once; reminders_sent remembers it."""
    today = _today()
    due_dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    for event in database.get_events_needing_reminder(due_dates):
        sent, failed = await _broadcast(context.bot, _reminder_text(event, today), event)
        if sent:  # if every send failed (or no groups yet), leave it due so it's retried
            database.mark_reminder_sent(event["id"], event["date"])
        logger.info("Reminder for event #%s '%s': sent to %s groups, %s failed.",
                    event["id"], event["title"], sent, failed)


def schedule_reminders(app):
    """Call once from main.py after the Application is built."""
    app.job_queue.run_daily(send_due_reminders, time=REMINDER_TIME, name="event_reminders")
    # Also run shortly after every start, so a reminder missed while the bot was off still goes out.
    app.job_queue.run_once(send_due_reminders, when=10, name="event_reminders_catchup")


def get_handlers():
    add_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("addevent", add_event_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_name)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_description),
                MessageHandler(~filters.COMMAND, add_event_description_not_text),
            ],
            POSTER_AND_LINK: [  # a photo (link in its caption), just a link, or /skip
                MessageHandler(filters.PHOTO, add_event_poster),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_link),
                CommandHandler("skip", add_event_skip),
                MessageHandler(~filters.COMMAND, add_event_not_a_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_event_cancel)],
        # Sending /addevent while a previous add-event is still waiting cancels it and starts over.
        allow_reentry=True,
    )
    buttons_only = [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tap_a_button)]
    edit_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("editevent", edit_event_start)],
        states={
            E_CHOOSE_EVENT: [CallbackQueryHandler(edit_event_chosen, pattern=r"^editevent:\d+$"), *buttons_only],
            E_CHOOSE_FIELD: [
                CallbackQueryHandler(edit_field_chosen, pattern=r"^editfield:(name|date|description|link|poster)$"),
                CallbackQueryHandler(edit_done, pattern=r"^editdone$"),  # 'No change'
                *buttons_only,
            ],
            E_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_name)],
            E_NEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_date)],
            E_NEW_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_description)],
            E_NEW_LINK: [  # added: a link, or /remove
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_link),
                CommandHandler("remove", edit_new_link),
            ],
            E_NEW_POSTER: [
                MessageHandler(filters.PHOTO, edit_new_poster),
                MessageHandler(~filters.COMMAND, edit_not_a_photo),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", edit_cancel),
            MessageHandler(filters.COMMAND, edit_other_command),  # must come after /cancel
        ],
        allow_reentry=True,
    )
    return [
        add_event_conversation,
        edit_event_conversation,
        CallbackQueryHandler(edit_menu_expired, pattern=r"^edit(event|field|done)"),  # stale edit buttons
        CommandHandler("events", list_events),
        CallbackQueryHandler(view_event_button, pattern=r"^viewevent:\d+$"),
        CommandHandler("deleteevent", delete_event_start),
        CallbackQueryHandler(delete_event_button, pattern=r"^delevent:\d+$"),
    ]
