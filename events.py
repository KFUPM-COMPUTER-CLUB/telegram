"""events.py - event commands: /addevent, /events, /deleteevent, /editevent (all listed in /help)."""
import calendar
import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
NAME, YEAR, MONTH, DAY = range(4)
E_CHOOSE_EVENT, E_CHOOSE_FIELD, E_NEW_NAME, E_CHOOSE_PART, E_NEW_VALUE = range(10, 15)

MAX_YEARS_AHEAD = 10  # rejects typos like 20260
MAX_TITLE_LENGTH = 100


def _parse_int(text):
    """Returns the number in text, or None if it isn't a whole number."""
    try:
        return int(text.strip())
    except ValueError:
        return None


# Validation rules shared by /addevent and /editevent. Each returns an error message, or None if OK.

def _check_title(title):
    if not title:
        return "The name can't be empty. Please send the event name."
    if len(title) > MAX_TITLE_LENGTH:
        return f"That name is too long ({len(title)} characters). Max is {MAX_TITLE_LENGTH}. Try again."
    return None


def _check_year(year):
    this_year = date.today().year
    if year is None:
        return f"Please send the year as a number, e.g. {this_year}."
    if year < this_year:
        return f"{year} is in the past. The year must be {this_year} or later."
    if year > this_year + MAX_YEARS_AHEAD:
        return f"{year} is too far ahead. Please send a year up to {this_year + MAX_YEARS_AHEAD}."
    return None


def _check_month(month):
    if month is None or not 1 <= month <= 12:
        return "The month must be a number from 1 to 12. Try again."
    return None


def _check_day(year, month, day):
    days_in_month = calendar.monthrange(year, month)[1]  # handles 30/31-day months and leap years
    if day is None or not 1 <= day <= days_in_month:
        return (
            f"{calendar.month_name[month]} {year} has {days_in_month} days. "
            f"Please send a day from 1 to {days_in_month}."
        )
    return None


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
    await update.message.reply_text(f"Got it: “{title}”.\nNow send the year (e.g. {date.today().year}).")
    return YEAR


async def add_event_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = _parse_int(update.message.text)
    error = _check_year(year)
    if error:
        await update.message.reply_text(error)
        return YEAR

    context.user_data["new_event"]["year"] = year
    await update.message.reply_text("Now send the month as a number (1-12).")
    return MONTH


async def add_event_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    year = context.user_data["new_event"]["year"]
    month = _parse_int(update.message.text)
    error = _check_month(month)
    if error:
        await update.message.reply_text(error)
        return MONTH
    if year == today.year and month < today.month:
        await update.message.reply_text(
            f"Month {month} of {year} has already passed. Please send month {today.month} or later."
        )
        return MONTH

    context.user_data["new_event"]["month"] = month
    days_in_month = calendar.monthrange(year, month)[1]  # handles 30/31-day months and leap years
    await update.message.reply_text(
        f"Now send the day (1-{days_in_month} for {calendar.month_name[month]} {year})."
    )
    return DAY


async def add_event_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    event = context.user_data["new_event"]
    year, month = event["year"], event["month"]
    day = _parse_int(update.message.text)
    error = _check_day(year, month, day)
    if error:
        await update.message.reply_text(error)
        return DAY

    event_date = date(year, month, day)
    if event_date < today:
        await update.message.reply_text(
            f"{event_date.isoformat()} has already passed. Please send today's day ({today.day}) or later."
        )
        return DAY

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
    """Numbered list text shared by /events and /deleteevent."""
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
    """/deleteevent - shows the upcoming list with one numbered button per event."""
    today = date.today()
    events = database.get_upcoming_events(today.isoformat())
    if not events:
        await update.message.reply_text("📅 No upcoming events to delete.\nUse /help to see the commands.")
        return

    # Each button's callback_data carries the event id, e.g. "delevent:7", so the press
    # can be matched to the right row even if the numbering changes later.
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"delevent:{event['id']}")
        for i, event in enumerate(events, start=1)
    ]
    rows = [buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    await update.message.reply_text(
        _format_events(events, today) + "\n\n🗑 Tap the number of the event to delete:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@admin.admin_only
async def delete_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when a numbered button is pressed. Deletes that event and replaces the list with a result."""
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

PART_NAMES = ("year", "month", "day")


def _event_line(event):
    """'📌 Title\n📅 Monday, 27 October 2026' for result messages."""
    event_date = date.fromisoformat(event["date"])
    return f"📌 {event['title']}\n📅 {event_date.strftime('%A, %d %B %Y')}"


def _part_buttons(exclude=None, with_done=False):
    """Year/Month/Day buttons (minus the one just edited) and optionally a 'No change' button."""
    row = [
        InlineKeyboardButton(part.capitalize(), callback_data=f"editpart:{part}")
        for part in PART_NAMES
        if part != exclude
    ]
    if with_done:
        row.append(InlineKeyboardButton("✅ No change", callback_data="editdone"))
    return InlineKeyboardMarkup([row])


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
        f"📅 Current date: {event_date.strftime('%A, %d %B %Y')}\n\nWhich part do you want to change?",
        reply_markup=_part_buttons(),
    )
    return E_CHOOSE_PART


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


async def edit_part_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Year / Month / Day button: ask for the new value."""
    query = update.callback_query
    await query.answer()
    part = query.data.split(":")[1]
    event = database.get_event(context.user_data["edit_event"])
    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists.\nUse /help to see the commands.")
        return ConversationHandler.END

    context.user_data["edit_part"] = part
    current = getattr(date.fromisoformat(event["date"]), part)
    await query.edit_message_text(f"Send the new {part} (currently {current}), or /cancel.")
    return E_NEW_VALUE


async def edit_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """New year/month/day typed: validate it against the rest of the current date and save."""
    event_id = context.user_data["edit_event"]
    part = context.user_data["edit_part"]
    event = database.get_event(event_id)
    old_date = date.fromisoformat(event["date"])
    value = _parse_int(update.message.text)

    # 1) the part on its own
    if part == "year":
        error = _check_year(value)
    elif part == "month":
        error = _check_month(value)
    else:
        error = _check_day(old_date.year, old_date.month, value)
    if error:
        await update.message.reply_text(error)
        return E_NEW_VALUE

    # 2) the whole date it produces (e.g. changing month to 2 while the day is 30)
    try:
        new_date = old_date.replace(**{part: value})
    except ValueError:
        await update.message.reply_text(
            f"That would make the date {old_date.day}/{value if part == 'month' else old_date.month}"
            f"/{value if part == 'year' else old_date.year}, which doesn't exist. "
            f"Change the day first, or pick another {part}."
        )
        return E_NEW_VALUE
    if new_date < date.today():
        await update.message.reply_text(
            f"That would move the event to {new_date.isoformat()}, which has already passed. Try again."
        )
        return E_NEW_VALUE

    database.update_event_date(event_id, new_date.isoformat())
    logger.info("Event #%s date %s -> %s.", event_id, old_date, new_date)
    await update.message.reply_text(
        f"✅ {part.capitalize()} updated!\n📅 New date: {new_date.strftime('%A, %d %B %Y')}"
        "\n\nWant to change anything else?",
        reply_markup=_part_buttons(exclude=part, with_done=True),
    )
    return E_CHOOSE_PART


async def edit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'No change' button: finish."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(context.user_data.get("edit_event"))
    summary = f"\n\n{_event_line(event)}" if event else ""
    await query.edit_message_text(f"✅ Editing finished.{summary}\n\nUse /help to see the commands.")
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


def get_handlers():
    add_event_conversation = ConversationHandler(
        entry_points=[CommandHandler("addevent", add_event_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_name)],
            YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_year)],
            MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_month)],
            DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_day)],
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
            E_CHOOSE_PART: [
                CallbackQueryHandler(edit_part_chosen, pattern=r"^editpart:(year|month|day)$"),
                CallbackQueryHandler(edit_done, pattern=r"^editdone$"),
                *buttons_only,
            ],
            E_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_value)],
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
        CallbackQueryHandler(edit_menu_expired, pattern=r"^edit(event|field|part|done)"),  # stale edit buttons
        CommandHandler("events", list_events),  # added: /events listing
        CommandHandler("deleteevent", delete_event_start),  # added: /deleteevent
        CallbackQueryHandler(delete_event_button, pattern=r"^delevent:\d+$"),  # its numbered buttons
    ]
