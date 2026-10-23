"""events.py - event commands (/addevent, /events, /deleteevent, /editevent, /sendreminder)
and the automatic day-before reminders sent to all registered groups."""
import logging
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from buttons import with_website

logger = logging.getLogger(__name__)

# Conversation steps. ConversationHandler remembers which step each user is on and sends
# their next message to the matching function below.
NAME, DATE, POSTER = range(3)  # changed: POSTER step added
E_CHOOSE_EVENT, E_CHOOSE_FIELD, E_NEW_NAME, E_NEW_DATE, E_NEW_POSTER = range(10, 15)  # changed: poster

MAX_YEARS_AHEAD = 10  # rejects typos like 20260
MAX_TITLE_LENGTH = 100
BUTTONS_PER_ROW = 5

SAUDI_TZ = ZoneInfo("Asia/Riyadh")

# Automatic reminders go out at this local time, the day before each event.
REMINDER_TIME = time(hour=9, minute=0, tzinfo=SAUDI_TZ)


def _today():
    """Today's date in Saudi time, whatever timezone the computer running the bot uses.
    (date.today() would use the server's own timezone, which can be a day behind after midnight.)"""
    return datetime.now(SAUDI_TZ).date()


# Validation rules shared by /addevent and /editevent.

def _check_title(title):
    """Returns an error message, or None if the title is OK."""
    if not title:
        return "The name can't be empty. Please send the event name."
    if len(title) > MAX_TITLE_LENGTH:
        return f"That name is too long ({len(title)} characters). Max is {MAX_TITLE_LENGTH}. Try again."
    return None


def _parse_date(text):
    """Checks a date typed as YYYY-MM-DD. Returns (date, None) if it's valid, or (None, error message)."""
    text = text.strip()
    today = _today()
    # Exactly a 4-digit year, 2-digit month and 2-digit day (so '2026-1-5' or '5/1/2026' are rejected).
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return None, f"Please send the date in the format YYYY-MM-DD, e.g. {today.isoformat()}."
    try:
        parsed = date.fromisoformat(text)  # raises ValueError for 2026-02-30, month 13, etc.
    except ValueError:
        return None, f"{text} isn't a real date. Check the month and day, and use the format YYYY-MM-DD."
    if parsed < today:
        return None, f"{text} has already passed. Please send today's date ({today.isoformat()}) or later."
    if parsed.year > today.year + MAX_YEARS_AHEAD:
        return None, f"{parsed.year} is too far ahead. Please send a year up to {today.year + MAX_YEARS_AHEAD}."
    return parsed, None


def _event_text(event, today=None, prefix="📌"):
    """'📌 Title\n📅 Monday, 27 October 2026 · in 6 days' (the '· in N days' part only with today)."""
    event_date = date.fromisoformat(event["date"])
    when = f" · {_when(event_date, today)}" if today else ""
    return f"{prefix} {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}{when}"


def calendar_url(event):
    """A link that opens Google Calendar with this event already filled in, as an all-day event.
    Events only have a date (no time), so it is an all-day entry."""
    start = date.fromisoformat(event["date"])
    end = start + timedelta(days=1)  # Google's end date for all-day events is exclusive
    params = {
        "action": "TEMPLATE",
        "text": event["title"],
        "dates": f"{start:%Y%m%d}/{end:%Y%m%d}",
        "details": "Added from the KFUPM Computer Club Telegram bot.",
    }
    # quote (not the default quote_plus) so spaces become %20; titles may hold Arabic, emoji, & or #.
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


def calendar_markup(event):
    """A keyboard with one 'Add to Google Calendar' link button for this event."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("📆 Add to Google Calendar", url=calendar_url(event))]])


async def _send_event(bot, chat_id, text, event, reply_markup=None):
    """Added for posters: sends the poster photo with text as its caption, or just the text
    if the event has no poster. Works for private chats and groups alike."""
    if event.get("poster"):
        await bot.send_photo(chat_id=chat_id, photo=event["poster"], caption=text, reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


# ---------- Add event conversation ----------

@admin.admin_only
async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: the /addevent command."""
    context.user_data["new_event"] = {}  # per-user scratch space for the answers
    await update.message.reply_text(
        "📝 Let's add an event.\nSend me the event name (or /cancel to stop).",
        reply_markup=with_website(),
    )
    return NAME


async def add_event_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await update.message.reply_text(error, reply_markup=with_website())
        return NAME

    context.user_data["new_event"]["title"] = title
    await update.message.reply_text(
        f"Got it: “{title}”.\nNow send the date in the format YYYY-MM-DD (e.g. {_today().isoformat()}),"
        " or /cancel to stop.",  # changed: the date step now also offers /cancel
        reply_markup=with_website(),
    )
    return DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event_date, error = _parse_date(update.message.text)
    if error:
        await update.message.reply_text(error, reply_markup=with_website())
        return DATE

    # changed: the date is kept until the poster step; saving happens in _save_new_event
    context.user_data["new_event"]["date"] = event_date.isoformat()
    await update.message.reply_text(
        "🖼 Now send the event poster as a photo (not as a file).\n"
        "Send /skip if there is no poster, or /cancel to stop.",
        reply_markup=with_website(),
    )
    return POSTER


async def add_event_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo arrived: keep the largest size's file_id and save the event."""
    context.user_data["new_event"]["poster"] = update.message.photo[-1].file_id
    return await _save_new_event(update, context)


async def add_event_skip_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/skip: save the event without a poster."""
    context.user_data["new_event"]["poster"] = None
    return await _save_new_event(update, context)


async def add_event_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send the poster as a photo, or /skip if there is none.", reply_markup=with_website())
    return POSTER


async def _save_new_event(update, context):
    event = context.user_data.pop("new_event")
    event["id"] = database.add_event(event["title"], event["date"], event["poster"])
    logger.info("Event #%s '%s' added for %s (poster: %s).",
                event["id"], event["title"], event["date"], bool(event["poster"]))

    # Success message ends with a /help shortcut (Telegram makes /commands tappable) so the user
    # can go straight to the next command. It is sent with the poster when there is one.
    await _send_event(
        context.bot, update.effective_chat.id,
        f"✅ Event registered!\n\n{_event_text(event)}\n\nUse /help to see the commands.",
        event,
        reply_markup=with_website(),
    )
    return ConversationHandler.END


async def add_event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel at any step leaves the conversation without saving."""
    context.user_data.pop("new_event", None)
    await update.message.reply_text("❌ Cancelled. Nothing was saved.\nUse /help to see the commands.", reply_markup=with_website())
    return ConversationHandler.END


# ---------- /events ----------

def _when(event_date, today):
    """Short hint like 'today', 'tomorrow' or 'in 5 days'."""
    days = (event_date - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _format_events(events, today):
    """Numbered list text shared by /events, /editevent and /sendreminder."""
    lines = [f"📅 Upcoming events ({len(events)}):\n"]
    for i, event in enumerate(events, start=1):
        event_date = date.fromisoformat(event["date"])
        lines.append(
            f"{i}. {event['title']}\n"
            f"    🗓 {event_date.strftime('%a, %d %b %Y')} · {_when(event_date, today)}"
        )
    return "\n".join(lines)


def _numbered_buttons(events, prefix):
    """Rows of numbered buttons; each carries '<prefix>:<event id>'."""  # added: shared by several commands
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"{prefix}:{event['id']}")
        for i, event in enumerate(events, start=1)
    ]
    return [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/events - shows upcoming events, soonest first, with a button per event to see its
    full info (poster, title, date). Open to everyone."""
    today = _today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events yet.\nUse /help to see the commands.", reply_markup=with_website())
        return
    await update.message.reply_text(  # changed: numbered buttons added
        _format_events(events, today) + "\n\n👀 Tap a number to see the full event info:",
        reply_markup=with_website(InlineKeyboardMarkup(_numbered_buttons(events, "viewevent"))),
    )


async def view_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number under /events was pressed: send that event's poster, title and date."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(int(query.data.split(":")[1]))
    if event is None:
        await query.message.reply_text("⚠️ That event no longer exists. Send /events for the current list.", reply_markup=with_website())
        return
    text = _event_text(event, _today())
    if not event.get("poster"):
        text += "\n🖼 No poster yet."
    await _send_event(
        context.bot, query.message.chat_id, text, event, reply_markup=with_website(calendar_markup(event))
    )


# ---------- /deleteevent ----------

@admin.admin_only
async def delete_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deleteevent - shows one button per upcoming event, labelled with the event's name."""
    events = database.get_upcoming_events(_today().isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to delete.\nUse /help to see the commands.", reply_markup=with_website())
        return

    # Each button's callback_data carries the event id, e.g. "delevent:7", so the press
    # can be matched to the right row. One button per row, since names can be long.
    buttons = [
        [InlineKeyboardButton(event["title"], callback_data=f"delevent:{event['id']}")]
        for event in events
    ]
    await update.message.reply_text(
        "🗑 Tap the event you want to delete:",
        reply_markup=with_website(InlineKeyboardMarkup(buttons)),
    )


@admin.admin_only
async def delete_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when an event button is pressed. Deletes that event and replaces the list with a result."""
    query = update.callback_query
    await query.answer()  # stops the button's loading spinner
    event_id = int(query.data.split(":")[1])

    event = database.get_event(event_id)
    if event is None or database.delete_event(event_id) == 0:
        await query.edit_message_text("⚠️ That event was already deleted.\nUse /help to see the commands.", reply_markup=with_website())
        return

    logger.info("Event #%s '%s' deleted.", event_id, event["title"])
    event_date = date.fromisoformat(event["date"])
    await query.edit_message_text(  # editing also removes the buttons
        f"🗑 Event deleted!\n\n📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"
        "\n\nUse /help to see the commands.",
        reply_markup=with_website(),
    )


# ---------- /editevent ----------

def _event_line(event):
    """'📌 Title\n📅 Monday, 27 October 2026' for result messages."""
    event_date = date.fromisoformat(event["date"])
    return f"📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"


FIELD_NAMES = ("name", "date", "poster")


def _field_buttons(exclude=None, with_done=False):
    """Name/Date/Poster buttons (minus the one just changed) and optionally 'No change'."""  # added
    row = [
        InlineKeyboardButton(field.capitalize(), callback_data=f"editfield:{field}")
        for field in FIELD_NAMES
        if field != exclude
    ]
    if with_done:
        row.append(InlineKeyboardButton("✅ No change", callback_data="editdone"))
    return InlineKeyboardMarkup([row])


async def _show(query, text, reply_markup=None):
    """Replaces the message the button was on. A photo message can't become text, so there the
    buttons are removed and a new message is sent instead. The website button is always added."""  # added
    reply_markup = with_website(reply_markup)
    if query.message.photo:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await query.edit_message_text(text, reply_markup=reply_markup)


async def _ask_more_changes(update, context, event, changed, result):
    """After a successful change: show the result and offer the other two fields or 'No change'."""  # added
    await _send_event(
        context.bot, update.effective_chat.id,
        f"{result}\n\nWant to change anything else? (or /cancel)",
        event,
        reply_markup=with_website(_field_buttons(exclude=changed, with_done=True)),
    )
    return E_CHOOSE_FIELD


@admin.admin_only
async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/editevent - shows the upcoming list with one numbered button per event."""
    today = _today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to edit.\nUse /help to see the commands.", reply_markup=with_website())
        return ConversationHandler.END

    context.user_data.pop("edit_event", None)
    await update.message.reply_text(
        _format_events(events, today) + "\n\n✏️ Tap the number of the event to edit (or /cancel):",
        reply_markup=with_website(InlineKeyboardMarkup(_numbered_buttons(events, "editevent"))),
    )
    return E_CHOOSE_EVENT


async def edit_event_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A numbered button was pressed: ask whether to change the name, the date or the poster."""
    query = update.callback_query
    await query.answer()
    event_id = int(query.data.split(":")[1])
    event = database.get_event(event_id)
    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.", reply_markup=with_website())
        return ConversationHandler.END

    context.user_data["edit_event"] = event_id
    await query.edit_message_text(
        f"✏️ Editing:\n{_event_line(event)}\n\nWhat do you want to change? (or /cancel)",
        reply_markup=with_website(_field_buttons()),
    )
    return E_CHOOSE_FIELD


async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Name, Date or Poster button."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data["edit_event"])
    if event is None:
        await _show(query, "⚠️ That event no longer exists.\nUse /help to see the commands.")
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    if query.data == "editfield:name":
        await _show(query, f"Send the new name for “{event['title']}” (or /cancel).")
        return E_NEW_NAME

    if query.data == "editfield:poster":  # added
        current = "This event has a poster already; the new one will replace it." if event.get("poster") \
            else "This event has no poster yet."
        await _show(query, f"🖼 {current}\nSend the new poster as a photo (not as a file), or /cancel.")
        return E_NEW_POSTER

    event_date = date.fromisoformat(event["date"])
    await _show(
        query,
        f"📅 Current date: {event_date.isoformat()} ({event_date.strftime('%A')})\n\n"
        "Send the new date in the format YYYY-MM-DD (or /cancel).",
    )
    return E_NEW_DATE


async def edit_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await update.message.reply_text(error, reply_markup=with_website())
        return E_NEW_NAME

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        await update.message.reply_text("⚠️ That event no longer exists.\nUse /help to see the commands.", reply_markup=with_website())
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    database.update_event_title(event_id, title)
    logger.info("Event #%s renamed '%s' -> '%s'.", event_id, event["title"], title)
    # changed: after a change, offer the other fields instead of ending
    result = f"✅ Name updated!\n\n📌 {event['title']} → {title}"
    event["title"] = title
    return await _ask_more_changes(update, context, event, "name", result)


async def edit_new_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """New date typed as YYYY-MM-DD: validate it and save."""
    new_date, error = _parse_date(update.message.text)
    if error:
        await update.message.reply_text(error, reply_markup=with_website())
        return E_NEW_DATE

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        await update.message.reply_text("⚠️ That event no longer exists.\nUse /help to see the commands.", reply_markup=with_website())
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    old_date = date.fromisoformat(event["date"])
    database.update_event_date(event_id, new_date.isoformat())
    logger.info("Event #%s date %s -> %s.", event_id, old_date, new_date)
    # changed: after a change, offer the other fields instead of ending
    result = f"✅ Date updated!\n\n📌 {event['title']}\n📅 {old_date.isoformat()} → {new_date.isoformat()}"
    event["date"] = new_date.isoformat()
    return await _ask_more_changes(update, context, event, "date", result)


async def edit_new_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A photo arrived for the Poster step: save its file_id."""  # added
    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        await update.message.reply_text("⚠️ That event no longer exists.\nUse /help to see the commands.", reply_markup=with_website())
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    event["poster"] = update.message.photo[-1].file_id
    database.update_event_poster(event_id, event["poster"])
    logger.info("Event #%s poster updated.", event_id)
    # changed: after a change, offer the other fields instead of ending
    return await _ask_more_changes(update, context, event, "poster", f"✅ Poster updated!\n\n{_event_text(event)}")


async def edit_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send the poster as a photo, or /cancel.", reply_markup=with_website())
    return E_NEW_POSTER


async def edit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'No change' button: finish editing."""  # added
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data.get("edit_event"))
    summary = f"\n\n{_event_line(event)}" if event else ""
    await _show(query, f"✅ Editing finished.{summary}\n\nUse /help to see the commands.")
    context.user_data.pop("edit_event", None)
    return ConversationHandler.END


async def edit_tap_a_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text typed while the bot is waiting for a button press."""
    await update.message.reply_text("Please tap one of the buttons above, or send /cancel.", reply_markup=with_website())
    return None  # stay on the same step


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("edit_event", None)
    await update.message.reply_text("❌ Edit cancelled. Changes already saved are kept.\nUse /help to see the commands.", reply_markup=with_website())
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
    await _show(update.callback_query, "⌛ This menu has expired. Send /editevent to start again.")  # changed: photo-safe


# ---------- Reminders ----------

async def _broadcast(bot, text, event):
    """Sends one event (poster + text, or text only) to every registered group.
    Returns (sent, failed) counts."""  # changed: takes the event so the poster goes too
    sent = failed = 0
    for group in database.get_groups():
        try:
            await _send_event(bot, group["group_id"], text, event, reply_markup=calendar_markup(event))
            sent += 1
        except TelegramError as error:
            failed += 1
            logger.warning("Could not send to group %s (%s): %s", group["title"], group["group_id"], error)
    return sent, failed


def _reminder_text(event, today):
    event_date = date.fromisoformat(event["date"])
    when = "today" if event_date == today else "tomorrow"
    return f"🔔 Reminder: {event['title']} is {when}!\n📅 {event_date.strftime('%A, %d %B %Y')}"


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


@admin.admin_only
async def send_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sendreminder - shows the upcoming list with a button per event (plus 'All events')."""
    today = _today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to send.\nUse /help to see the commands.", reply_markup=with_website())
        return
    if not database.get_groups():
        await update.message.reply_text("No groups registered yet. Add the bot to a group first.", reply_markup=with_website())
        return

    rows = _numbered_buttons(events, "remind")
    rows.append([InlineKeyboardButton("📋 All events", callback_data="remind:all")])
    await update.message.reply_text(
        _format_events(events, today) + "\n\n🔔 Tap the number of the event to remind all groups about:",
        reply_markup=with_website(InlineKeyboardMarkup(rows)),
    )


@admin.admin_only
async def send_reminder_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number (or 'All events') was pressed: broadcast that reminder and show the result."""
    query = update.callback_query
    await query.answer()
    today = _today()
    choice = query.data.split(":")[1]

    # changed: every reminder now carries the event's full info (poster, title, date).
    # "All events" sends one message per event.
    if choice == "all":
        events = database.get_upcoming_events(today.isoformat())
        if not events:
            await query.edit_message_text("📅 No upcoming events to send.\nUse /help to see the commands.", reply_markup=with_website())
            return
        what = f"the {len(events)} upcoming events"
    else:
        event = database.get_event(int(choice))
        if event is None:
            await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.", reply_markup=with_website())
            return
        events = [event]
        what = f"“{event['title']}”"

    sent = failed = 0
    for event in events:
        s, f = await _broadcast(context.bot, _event_text(event, today, prefix="🔔 Reminder:"), event)
        sent, failed = max(sent, s), max(failed, f)  # counts are per group, not per message
    result = f"✅ Reminder about {what} sent to {sent} group(s)."
    if failed:
        result += f"\n⚠️ Could not send to {failed} group(s); see the bot log."
    await query.edit_message_text(result + "\nUse /help to see the commands.", reply_markup=with_website())  # replaces the number buttons with just the website button


def get_handlers():
    add_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("addevent", add_event_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_name)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            POSTER: [  # added: poster step (photo, /skip, or a hint for anything else)
                MessageHandler(filters.PHOTO, add_event_poster),
                CommandHandler("skip", add_event_skip_poster),
                MessageHandler(~filters.COMMAND, add_event_not_a_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_event_cancel)],
        # Sending /addevent while a previous add-event is still waiting now cancels it and starts over.
        allow_reentry=True,
    )
    # added: /editevent conversation
    buttons_only = [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_tap_a_button)]
    edit_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("editevent", edit_event_start)],
        states={
            E_CHOOSE_EVENT: [CallbackQueryHandler(edit_event_chosen, pattern=r"^editevent:\d+$"), *buttons_only],
            E_CHOOSE_FIELD: [
                CallbackQueryHandler(edit_field_chosen, pattern=r"^editfield:(name|date|poster)$"),
                CallbackQueryHandler(edit_done, pattern=r"^editdone$"),  # added: 'No change'
                *buttons_only,
            ],
            E_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_name)],
            E_NEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_date)],
            E_NEW_POSTER: [  # added
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
        CommandHandler("events", list_events),  # added: /events listing
        CallbackQueryHandler(view_event_button, pattern=r"^viewevent:\d+$"),  # added: its detail buttons
        CommandHandler("deleteevent", delete_event_start),  # added: /deleteevent
        CommandHandler("sendreminder", send_reminder_command),  # added: /sendreminder
        CallbackQueryHandler(send_reminder_button, pattern=r"^remind:(\d+|all)$"),  # its buttons
        CallbackQueryHandler(delete_event_button, pattern=r"^delevent:\d+$"),  # its event buttons
    ]