"""announcements.py - /announce and scheduled announcements.

An announcement is either:
- text (with an optional photo or video). Bold, italics and other formatting the admin used
  are kept. If the last line is a link, it becomes a button under the announcement.
- an event card: '/announce event' lists the upcoming events and the admin taps one.
  The groups get the poster, name, date and the event's buttons.

Every announcement is posted in the club's style (see brand.py) with the website button.
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
import events
from admin import admin_only
from brand import FOOTER_TEXT, clean_url, esc, formatted_text, post, visible_length
from buttons import link_button, reply_long, with_website

logger = logging.getLogger(__name__)

SAUDI_TZ = timezone(timedelta(hours=3))
DATE_FORMAT = "%Y-%m-%d %H:%M"

# The word that turns /announce and /schedule_announce into an event announcement.
EVENT_KEYWORD = "event"

# The club footer added under every announcement counts towards Telegram's limits.
EXTRA_LENGTH = len(FOOTER_TEXT) + 2  # plus the blank line before it

# A last line that is only a link (optionally written as "link: ..."), e.g.
# "https://forms.gle/abc" or "Link: forms.gle/abc".
LINK_LINE = re.compile(r"(?:link:\s*)?(\S+)", re.IGNORECASE)


def parse_date(date_text):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", date_text):
            raise ValueError

        parsed = datetime.strptime(date_text, DATE_FORMAT)
        local_date = parsed.replace(tzinfo=SAUDI_TZ)
    except ValueError:
        raise ValueError(
            "I couldn't read that date and time. Write it like 2026-11-01 17:00 "
            "(year-month-day, then the time on a 24-hour clock)."
        )

    utc_date = local_date.astimezone(timezone.utc)

    if utc_date <= datetime.now(timezone.utc):
        raise ValueError("That time has already passed. Pick a time in the future.")

    return utc_date.isoformat()


def display_date(utc_text):
    return (
        datetime.fromisoformat(utc_text)
        .astimezone(SAUDI_TZ)
        .strftime(DATE_FORMAT)
    )


def command_content(message, as_html=False):
    """The text after the command. With as_html=True it keeps the admin's formatting
    (bold, italics, links...) as HTML, ready to post."""
    if as_html:
        content = formatted_text(message)
    else:
        content = message.caption if message.caption is not None else message.text

    if not content:
        return ""

    # Split on any whitespace (space OR newline) so "/announce\nHello" works too.
    parts = content.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def split_link(text):
    """Takes the link off the last line, if that line is a link.
    Returns (text without it, link or None). Raises ValueError for a 'link:' line that isn't a link."""
    lines = text.rstrip().split("\n")
    match = LINK_LINE.fullmatch(html.unescape(lines[-1].strip()))
    if not match:
        return text, None

    written_as_link = lines[-1].strip().lower().startswith("link:")
    looks_like_link = re.match(r"https?://", match.group(1), re.IGNORECASE)
    if not (written_as_link or looks_like_link):
        return text, None

    url = clean_url(match.group(1))
    if url is None:
        raise ValueError("The link on the last line doesn't look right. Check it and try again.")
    return "\n".join(lines[:-1]).strip(), url


def extract_media(message):
    if message.photo:
        return "photo", message.photo[-1].file_id

    if message.video:
        return "video", message.video.file_id

    return None, None


def validate_content(text, media_type):
    """text is HTML. Checks it isn't empty and fits Telegram's limits once styled."""
    if not text:
        raise ValueError(
            "Write your message after the command, in the same message. For example:\n"
            "/announce Meeting moved to 7 PM!\n\n"
            "To announce an event, send: /announce event"
        )

    limit = 1024 if media_type else 4096
    length = visible_length(text) + EXTRA_LENGTH

    if length > limit:
        raise ValueError(
            f"The announcement is {length - limit} characters too long "
            f"(the limit is {limit} with the club footer included)."
        )


def styled(text):
    """A text announcement as it's posted: the admin's text, then the club footer."""
    return post(text)


def link_markup(url):
    """The website button, with the announcement's link above it if there is one."""
    rows = [[link_button("🔗 Open link", url)]] if url else []
    return with_website(InlineKeyboardMarkup(rows))


async def send_to_groups(bot, text, media_type=None, media_file_id=None, reply_markup=None):
    """Posts HTML text (with an optional photo or video) to every group.
    Returns (number sent, list of failure reasons)."""
    groups = database.get_groups()
    successful = 0
    failed = []

    for group in groups:
        group_id = group["group_id"]
        group_name = group["title"] or str(group_id)

        try:
            if media_type == "photo":
                await bot.send_photo(
                    chat_id=group_id,
                    photo=media_file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )

            elif media_type == "video":
                await bot.send_video(
                    chat_id=group_id,
                    video=media_file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )

            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )

            successful += 1

        except Forbidden:
            failed.append(
                f"{group_name}: Bot lacks permission to send."
            )

        except TelegramError as error:
            logger.warning(
                "Failed sending to group %s: %s",
                group_id,
                error,
            )
            failed.append(f"{group_name}: {error}")

    return successful, failed


async def send_event_to_groups(bot, event):
    """Posts an event card (poster, name, date, description, link and calendar buttons) to every group."""
    return await send_to_groups(
        bot,
        events.announcement_text(event),
        "photo" if event.get("poster") else None,
        event.get("poster"),
        with_website(events.event_markup(event)),
    )


def groups_count(count):
    return f"{count} group" if count == 1 else f"{count} groups"


def delivery_report(title, successful, failed):
    """'✅ Announcement sent!\nSent to: 3 groups\nFailed: 1 group' plus the reason for each failure."""
    if not successful:
        title = "❌ The announcement could not be sent."

    result = f"{title}\nSent to: {groups_count(successful)}"

    if failed:
        result += (
            f"\nFailed: {groups_count(len(failed))}\n\n"
            "Why it failed:\n" + "\n".join(failed)
        )

    return result


async def reply_report(message, successful, failed):
    result = delivery_report("✅ Announcement sent!", successful, failed)

    await reply_long(message, result)


async def reply_event_picker(message, prefix, prompt):
    """Lists the upcoming events with a numbered button each, for '/announce event'."""
    text, keyboard = events.event_list(prefix, prompt)

    if text is None:
        await message.reply_text(events.NO_EVENTS)
        return

    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@admin_only
async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not database.get_groups():
        await message.reply_text("No registered groups found. Add the bot to a group first.")
        return

    if command_content(message).lower() == EVENT_KEYWORD:
        await reply_event_picker(message, "annevent", "📢 Tap the number of the event to announce.")
        return

    text = command_content(message, as_html=True)
    media_type, media_file_id = extract_media(message)

    try:
        text, url = split_link(text)
        validate_content(text, media_type)
    except ValueError as error:
        await message.reply_text(str(error))
        return

    successful, failed = await send_to_groups(
        context.bot,
        styled(text),
        media_type,
        media_file_id,
        link_markup(url),
    )

    await reply_report(message, successful, failed)


@admin_only
async def announce_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number under '/announce event' was pressed: post that event to every group."""
    query = update.callback_query
    await query.answer()
    event = database.get_event(int(query.data.split(":")[1]))

    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists. Send /announce event again.")
        return

    await query.edit_message_reply_markup(reply_markup=None)  # so it can't be sent twice by accident
    successful, failed = await send_event_to_groups(context.bot, event)
    await reply_report(query.message, successful, failed)


@admin_only
async def schedule_announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    parts = command_content(message, as_html=True).split(maxsplit=2)

    if len(parts) != 3:
        await message.reply_text(
            "Write the date, the time and your message after the command. For example:\n"
            "/schedule_announce 2026-11-01 17:00 Meeting tomorrow!\n\n"
            "To schedule an event, write event instead of a message:\n"
            "/schedule_announce 2026-11-01 17:00 event",
        )
        return

    date_text = f"{parts[0]} {parts[1]}"
    text = parts[2]
    media_type, media_file_id = extract_media(message)

    try:
        send_time = parse_date(date_text)

        if text.lower() == EVENT_KEYWORD:
            # The time travels inside the buttons, as a Unix timestamp.
            timestamp = int(datetime.fromisoformat(send_time).timestamp())
            await reply_event_picker(
                message,
                f"schedevent:{timestamp}",
                f"🗓 Tap the number of the event to announce on {date_text} (Saudi time).",
            )
            return

        text, url = split_link(text)
        validate_content(text, media_type)
    except ValueError as error:
        await message.reply_text(str(error))
        return

    message_id = database.add_scheduled_message(
        text,
        send_time,
        media_type,
        media_file_id,
        url,
    )

    await reply_scheduled(message, message_id, send_time)


async def reply_scheduled(message, message_id, send_time):
    await message.reply_text(
        f"🗓 Announcement scheduled!\n"
        f"ID: {message_id}\n"
        f"Date: {display_date(send_time)} (Saudi time)",
    )


@admin_only
async def schedule_event_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A number under '/schedule_announce ... event' was pressed: schedule that event."""
    query = update.callback_query
    await query.answer()
    _, timestamp, event_id = query.data.split(":")
    send_time = datetime.fromtimestamp(int(timestamp), timezone.utc)
    event = database.get_event(int(event_id))

    if event is None:
        await query.edit_message_text("⚠️ That event no longer exists. Send /schedule_announce again.")
        return

    if send_time <= datetime.now(timezone.utc):
        await query.edit_message_text("⌛ That time has already passed. Send /schedule_announce again.")
        return

    if send_time.astimezone(SAUDI_TZ).date().isoformat() > event["date"]:
        await query.edit_message_text(
            "⚠️ That time is after the event. Pick a time before it with /schedule_announce."
        )
        return

    await query.edit_message_reply_markup(reply_markup=None)  # so it can't be scheduled twice by accident
    message_id = database.add_scheduled_message(
        esc(event["title"]),
        send_time.isoformat(),
        event_id=event["id"],
    )
    await reply_scheduled(query.message, message_id, send_time.isoformat())


def preview(item):
    """A short plain-text preview of a scheduled announcement for /scheduled."""
    if item["event_id"]:
        return f"📅 Event: {html.unescape(item['text'])}"

    text = item["text"]
    if item["is_html"]:
        text = html.unescape(re.sub(r"<[^>]+>", "", text))

    text = text[:80]
    if item["url"]:
        text += f"\n🔗 {item['url']}"
    return text


@admin_only
async def scheduled(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    messages = database.get_scheduled_messages()

    if not messages:
        await update.effective_message.reply_text(
            "No pending announcements.",
        )
        return

    lines = ["🗓 Scheduled announcements:"]

    for item in messages:
        lines.append(
            f"\nID: {item['id']}\n"
            f"Date: {display_date(item['send_time'])}\n"
            f"Text: {preview(item)}"
        )

    result = "\n".join(lines)

    await reply_long(update.effective_message, result)


@admin_only
async def edit_announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    parts = command_content(message, as_html=True).split(maxsplit=3)

    if len(parts) != 4 or not parts[0].isdigit():
        await message.reply_text(
            "Write the ID, the new date and time, and the new message. For example:\n"
            "/edit_announce 3 2026-11-01 18:00 New message\n\n"
            "Send /scheduled to find the ID.",
        )
        return

    message_id = int(parts[0])
    date_text = f"{parts[1]} {parts[2]}"
    new_text = parts[3]

    try:
        send_time = parse_date(date_text)

        pending = next(
            (
                item for item in database.get_scheduled_messages()
                if item["id"] == message_id
            ),
            None,
        )

        if pending is None:
            await message.reply_text(
                "Announcement not found or no longer editable.",
            )
            return

        if pending["event_id"]:
            await message.reply_text(
                "That one announces an event. To change it, cancel it with "
                f"/cancel_announce {message_id} and schedule a new one.",
            )
            return

        new_text, url = split_link(new_text)
        validate_content(new_text, pending["media_type"])

    except ValueError as error:
        await message.reply_text(str(error))
        return

    changed = database.edit_scheduled_message(
        message_id,
        new_text,
        send_time,
        url,
    )

    if changed:
        await message.reply_text("✏️ Announcement updated!")
    else:
        await message.reply_text(
            "Announcement not found or no longer editable.",
        )


@admin_only
async def cancel_announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    parts = command_content(message).split()

    if len(parts) != 1 or not parts[0].isdigit():
        await message.reply_text(
            "Write the ID of the announcement to cancel. For example:\n"
            "/cancel_announce 3\n\n"
            "Send /scheduled to find the ID.",
        )
        return

    message_id = int(parts[0])
    cancelled = database.cancel_scheduled_message(message_id)

    if cancelled:
        await message.reply_text("🗑 Announcement cancelled.")
    else:
        await message.reply_text(
            "Announcement not found or already processed.",
        )


async def send_scheduled(bot, item):
    """Posts one due scheduled announcement. Returns (number sent, list of failure reasons)."""
    if item["event_id"]:
        event = database.get_event(item["event_id"])

        if event is None:
            return 0, [f"The event “{html.unescape(item['text'])}” was deleted before it went out."]

        if event["date"] < events._today().isoformat():
            return 0, [f"The event “{event['title']}” had already happened, so it wasn't announced."]

        return await send_event_to_groups(bot, event)

    # Rows saved by older versions of the bot hold plain text.
    text = item["text"] if item["is_html"] else esc(item["text"])

    return await send_to_groups(
        bot,
        styled(text),
        item["media_type"],
        item["media_file_id"],
        link_markup(item["url"]),
    )


async def process_scheduled_announcements(
    context: ContextTypes.DEFAULT_TYPE,
):
    now = datetime.now(timezone.utc).isoformat()
    due_messages = database.get_due_messages(now)

    for item in due_messages:
        message_id = item["id"]

        if not database.claim_scheduled_message(message_id):
            continue

        try:
            successful, failed = await send_scheduled(context.bot, item)

            status = "sent" if not failed else "failed"

            database.finish_scheduled_message(
                message_id,
                status,
            )

            logger.info(
                "Scheduled announcement %s: %s sent, %s failed",
                message_id,
                successful,
                len(failed),
            )

            for failure in failed:
                logger.warning(
                    "Scheduled announcement %s: %s",
                    message_id,
                    failure,
                )

            if failed:
                report = delivery_report(
                    f"⚠️ Scheduled announcement #{message_id} was only partly sent.",
                    successful,
                    failed,
                )

                for admin in database.get_admins():
                    for start in range(0, len(report), 4096):
                        try:
                            await context.bot.send_message(
                                chat_id=admin["user_id"],
                                text=report[start:start + 4096],
                            )

                        except TelegramError as error:
                            logger.warning(
                                "Could not notify admin %s: %s",
                                admin["user_id"],
                                error,
                            )

        except Exception:
            logger.exception(
                "Unexpected error in scheduled announcement %s",
                message_id,
            )

            database.finish_scheduled_message(
                message_id,
                "failed",
            )


def get_handlers():
    return [
        CommandHandler("announce", announce),
        CommandHandler("schedule_announce", schedule_announce),
        CommandHandler("scheduled", scheduled),
        CommandHandler("edit_announce", edit_announce),
        CommandHandler("cancel_announce", cancel_announce),
        CallbackQueryHandler(announce_event_button, pattern=r"^annevent:\d+$"),
        CallbackQueryHandler(schedule_event_button, pattern=r"^schedevent:\d+:\d+$"),
        MessageHandler(
            filters.PHOTO & filters.CaptionRegex(
                re.compile(r"^/announce(?:@\w+)?(?:\s|$)")
            ),
            announce,
        ),
        MessageHandler(
            filters.VIDEO & filters.CaptionRegex(
                re.compile(r"^/announce(?:@\w+)?(?:\s|$)")
            ),
            announce,
        ),
        MessageHandler(
            (filters.PHOTO | filters.VIDEO)
            & filters.CaptionRegex(
                re.compile(r"^/schedule_announce(?:@\w+)?(?:\s|$)")
            ),
            schedule_announce,
        ),
    ]
