"""events.py - event commands (/addevent, /events, /deleteevent, /editevent, /sendreminder)
and the automatic day-before reminders sent to all registered groups."""
import logging
import re
from datetime import date, time, timedelta
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

logger = logging.getLogger(__name__)

# Conversation steps. ConversationHandler remembers which step each user is on and sends
# their next message to the matching function below.
NAME, DATE = range(2)
E_CHOOSE_EVENT, E_CHOOSE_FIELD, E_NEW_NAME, E_NEW_DATE = range(10, 14)

MAX_YEARS_AHEAD = 10  # rejects typos like 20260
MAX_TITLE_LENGTH = 100

# Automatic reminders go out at this local time, the day before each event.
REMINDER_TIME = time(hour=9, minute=0, tzinfo=ZoneInfo("Asia/Riyadh"))


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
    today = date.today()
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


# ---------- Add event conversation ----------

@admin.admin_only
async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: the /addevent command."""
    context.user_data["new_event"] = {}  # per-user scratch space for the answers
    await update.message.reply_text(
        "📝 Let's add an event.\nSend me the event name (or /cancel to stop)."
    )
    return NAME


async def add_event_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await update.message.reply_text(error)
        return NAME

    context.user_data["new_event"]["title"] = title
    await update.message.reply_text(
        f"Got it: “{title}”.\nNow send the date in the format YYYY-MM-DD (e.g. {date.today().isoformat()})."
    )
    return DATE


async def add_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event_date, error = _parse_date(update.message.text)
    if error:
        await update.message.reply_text(error)
        return DATE

    event = context.user_data["new_event"]
    event_id = database.add_event(event["title"], event_date.isoformat())  # stored as 'YYYY-MM-DD'
    logger.info("Event #%s '%s' added for %s.", event_id, event["title"], event_date)

    # Success message ends with a /help shortcut (Telegram makes /commands tappable) so the user
    # can go straight to the next command.
    await update.message.reply_text(
        f"✅ Event registered!\n\n📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"
        "\n\nUse /help to see the commands."
    )
    context.user_data.pop("new_event", None)  # clean up
    return ConversationHandler.END


async def add_event_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel at any step leaves the conversation without saving."""
    context.user_data.pop("new_event", None)
    await update.message.reply_text("❌ Cancelled. Nothing was saved.\nUse /help to see the commands.")
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


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/events - shows upcoming events, soonest first. Open to everyone."""
    today = date.today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events yet.\nUse /help to see the commands.")
        return
    await update.message.reply_text(_format_events(events, today))


# ---------- /deleteevent ----------

BUTTONS_PER_ROW = 5


@admin.admin_only
async def delete_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deleteevent - shows one button per upcoming event, labelled with the event's name."""
    events = database.get_upcoming_events(date.today().isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to delete.\nUse /help to see the commands.")
        return

    # Each button's callback_data carries the event id, e.g. "delevent:7", so the press
    # can be matched to the right row. One button per row, since names can be long.
    buttons = [
        [InlineKeyboardButton(event["title"], callback_data=f"delevent:{event['id']}")]
        for event in events
    ]
    await update.message.reply_text(
        "🗑 Tap the event you want to delete:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@admin.admin_only
async def delete_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when an event button is pressed. Deletes that event and replaces the list with a result."""
    query = update.callback_query
    await query.answer()  # stops the button's loading spinner
    event_id = int(query.data.split(":")[1])

    event = database.get_event(event_id)
    if event is None or database.delete_event(event_id) == 0:
        await query.edit_message_text("⚠️ That event was already deleted.\nUse /help to see the commands.")
        return

    logger.info("Event #%s '%s' deleted.", event_id, event["title"])
    event_date = date.fromisoformat(event["date"])
    await query.edit_message_text(  # editing also removes the buttons
        f"🗑 Event deleted!\n\n📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"
        "\n\nUse /help to see the commands."
    )


# ---------- /editevent ----------

def _event_line(event):
    """'📌 Title\n📅 Monday, 27 October 2026' for result messages."""
    event_date = date.fromisoformat(event["date"])
    return f"📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"


@admin.admin_only
async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/editevent - shows the upcoming list with one numbered button per event."""
    today = date.today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to edit.\nUse /help to see the commands.")
        return ConversationHandler.END

    context.user_data.pop("edit_event", None)
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"editevent:{event['id']}")
        for i, event in enumerate(events, start=1)
    ]
    rows = [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    await update.message.reply_text(
        _format_events(events, today) + "\n\n✏️ Tap the number of the event to edit:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return E_CHOOSE_EVENT


async def edit_event_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A numbered button was pressed: ask whether to change the name or the date."""
    query = update.callback_query
    await query.answer()
    event_id = int(query.data.split(":")[1])
    event = database.get_event(event_id)
    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
        return ConversationHandler.END

    context.user_data["edit_event"] = event_id
    await query.edit_message_text(
        f"✏️ Editing:\n{_event_line(event)}\n\nWhat do you want to change?",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Name", callback_data="editfield:name"),
                InlineKeyboardButton("Date", callback_data="editfield:date"),
            ]]
        ),
    )
    return E_CHOOSE_FIELD


async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Name or Date button."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data["edit_event"])
    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
        return ConversationHandler.END

    if query.data == "editfield:name":
        await query.edit_message_text(f"Send the new name for “{event['title']}” (or /cancel).")
        return E_NEW_NAME

    event_date = date.fromisoformat(event["date"])
    await query.edit_message_text(
        f"📅 Current date: {event_date.isoformat()} ({event_date.strftime('%A')})\n\n"
        "Send the new date in the format YYYY-MM-DD (or /cancel)."
    )
    return E_NEW_DATE


async def edit_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    error = _check_title(title)
    if error:
        await update.message.reply_text(error)
        return E_NEW_NAME

    event_id = context.user_data["edit_event"]
    old = database.get_event(event_id)
    database.update_event_title(event_id, title)
    logger.info("Event #%s renamed '%s' -> '%s'.", event_id, old["title"], title)
    await update.message.reply_text(
        f"✅ Name updated!\n\n📌 {old['title']} → {title}\n\nUse /help to see the commands."
    )
    context.user_data.pop("edit_event", None)
    return ConversationHandler.END


async def edit_new_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """New date typed as YYYY-MM-DD: validate it and save."""
    new_date, error = _parse_date(update.message.text)
    if error:
        await update.message.reply_text(error)
        return E_NEW_DATE

    event_id = context.user_data["edit_event"]
    event = database.get_event(event_id)
    if event is None:
        await update.message.reply_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
        context.user_data.pop("edit_event", None)
        return ConversationHandler.END

    old_date = date.fromisoformat(event["date"])
    database.update_event_date(event_id, new_date.isoformat())
    logger.info("Event #%s date %s -> %s.", event_id, old_date, new_date)
    await update.message.reply_text(
        f"✅ Date updated!\n\n📌 {event['title']}\n📅 {old_date.isoformat()} → {new_date.isoformat()}"
        "\n\nUse /help to see the commands."
    )
    context.user_data.pop("edit_event", None)
    return ConversationHandler.END


async def edit_tap_a_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text typed while the bot is waiting for a button press."""
    await update.message.reply_text("Please tap one of the buttons above, or send /cancel.")
    return None  # stay on the same step


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("edit_event", None)
    await update.message.reply_text("❌ Edit cancelled. Changes already saved are kept.\nUse /help to see the commands.")
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
    await update.callback_query.edit_message_text("⌛ This menu has expired. Send /editevent to start again.")


# ---------- Reminders ----------

async def _broadcast(bot, text):
    """Sends text to every registered group. Returns (sent, failed) counts."""
    sent = failed = 0
    for group in database.get_groups():
        try:
            await bot.send_message(chat_id=group["group_id"], text=text)
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
    today = date.today()
    due_dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    for event in database.get_events_needing_reminder(due_dates):
        sent, failed = await _broadcast(context.bot, _reminder_text(event, today))
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
    today = date.today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to send.\nUse /help to see the commands.")
        return
    if not database.get_groups():
        await update.message.reply_text("No groups registered yet. Add the bot to a group first.")
        return

    # changed: pick one event (or all) instead of always sending the whole list
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"remind:{event['id']}")
        for i, event in enumerate(events, start=1)
    ]
    rows = [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton("📋 All events", callback_data="remind:all")])
    await update.message.reply_text(
        _format_events(events, today) + "\n\n🔔 Tap the number of the event to remind all groups about:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@admin.admin_only
async def send_reminder_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number (or 'All events') was pressed: broadcast that reminder and show the result."""
    query = update.callback_query
    await query.answer()
    today = date.today()
    choice = query.data.split(":")[1]

    if choice == "all":
        events = database.get_upcoming_events(today.isoformat())
        if not events:
            await query.edit_message_text("📅 No upcoming events to send.\nUse /help to see the commands.")
            return
        text = "🔔 " + _format_events(events, today)
        what = f"the {len(events)} upcoming events"
    else:
        event = database.get_event(int(choice))
        if event is None:
            await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
            return
        event_date = date.fromisoformat(event["date"])
        text = (
            f"🔔 Reminder: {event['title']}\n"
            f"📅 {event_date.strftime('%A, %d %B %Y')} · {_when(event_date, today)}"
        )
        what = f"“{event['title']}”"

    sent, failed = await _broadcast(context.bot, text)
    result = f"✅ Reminder about {what} sent to {sent} group(s)."
    if failed:
        result += f"\n⚠️ Could not send to {failed} group(s); see the bot log."
    await query.edit_message_text(result + "\nUse /help to see the commands.")  # also removes the buttons


def get_handlers():
    add_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("addevent", add_event_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_name)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
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
            E_CHOOSE_FIELD: [CallbackQueryHandler(edit_field_chosen, pattern=r"^editfield:(name|date)$"), *buttons_only],
            E_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_name)],
            E_NEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_date)],
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
        CallbackQueryHandler(edit_menu_expired, pattern=r"^edit(event|field)"),  # stale edit buttons
        CommandHandler("events", list_events),  # added: /events listing
        CommandHandler("deleteevent", delete_event_start),  # added: /deleteevent
        CommandHandler("sendreminder", send_reminder_command),  # added: /sendreminder
        CallbackQueryHandler(send_reminder_button, pattern=r"^remind:(\d+|all)$"),  # its buttons
        CallbackQueryHandler(delete_event_button, pattern=r"^delevent:\d+$"),  # its event buttons
    ]