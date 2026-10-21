import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
from admin import admin_only

logger = logging.getLogger(__name__)

SAUDI_TZ = timezone(timedelta(hours=3))
DATE_FORMAT = "%Y-%m-%d %H:%M"


def parse_date(date_text):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", date_text):
            raise ValueError

        parsed = datetime.strptime(date_text, DATE_FORMAT)
        local_date = parsed.replace(tzinfo=SAUDI_TZ)
    except ValueError:
        raise ValueError("Invalid date. Use YYYY-MM-DD HH:MM.")

    utc_date = local_date.astimezone(timezone.utc)

    if utc_date <= datetime.now(timezone.utc):
        raise ValueError("The date must be in the future.")

    return utc_date.isoformat()


def display_date(utc_text):
    return (
        datetime.fromisoformat(utc_text)
        .astimezone(SAUDI_TZ)
        .strftime(DATE_FORMAT)
    )


def command_content(message):
    content = message.caption if message.caption is not None else message.text

    if not content:
        return ""

    # Split on any whitespace (space OR newline) so "/announce\nHello" works too.
    parts = content.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def extract_media(message):
    if message.photo:
        return "photo", message.photo[-1].file_id

    if message.video:
        return "video", message.video.file_id

    return None, None


def validate_content(text, media_type):
    if not text:
        raise ValueError("Announcement text is required.")

    limit = 1024 if media_type else 4096

    if len(text) > limit:
        raise ValueError(
            f"Announcement text exceeds {limit} characters."
        )


async def send_to_groups(bot, text, media_type=None, media_file_id=None):
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
                )

            elif media_type == "video":
                await bot.send_video(
                    chat_id=group_id,
                    video=media_file_id,
                    caption=text,
                )

            else:
                await bot.send_message(
                    chat_id=group_id,
                    text=text,
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


async def reply_report(message, successful, failed):
    result = (
        f"Announcement complete!\n"
        f"Successful: {successful}\n"
        f"Failed: {len(failed)}"
    )

    if failed:
        result += "\n\nFailures:\n" + "\n".join(failed)

    while result:
        await message.reply_text(result[:4096])
        result = result[4096:]


@admin_only
async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    text = command_content(message)
    media_type, media_file_id = extract_media(message)

    try:
        validate_content(text, media_type)
    except ValueError as error:
        await message.reply_text(str(error))
        return

    if not database.get_groups():
        await message.reply_text("No registered groups found.")
        return

    successful, failed = await send_to_groups(
        context.bot,
        text,
        media_type,
        media_file_id,
    )

    await reply_report(message, successful, failed)


@admin_only
async def schedule_announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    content = command_content(message)
    parts = content.split(maxsplit=2)

    if len(parts) != 3:
        await message.reply_text(
            "Usage:\n"
            "/schedule_announce YYYY-MM-DD HH:MM message\n\n"
            "Example:\n"
            "/schedule_announce 2026-11-01 17:00 Meeting tomorrow!"
        )
        return

    date_text = f"{parts[0]} {parts[1]}"
    text = parts[2]
    media_type, media_file_id = extract_media(message)

    try:
        send_time = parse_date(date_text)
        validate_content(text, media_type)
    except ValueError as error:
        await message.reply_text(str(error))
        return

    message_id = database.add_scheduled_message(
        text,
        send_time,
        media_type,
        media_file_id,
    )

    await message.reply_text(
        f"Announcement scheduled!\n"
        f"ID: {message_id}\n"
        f"Date: {display_date(send_time)} (Saudi time)"
    )


@admin_only
async def scheduled(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    messages = database.get_scheduled_messages()

    if not messages:
        await update.effective_message.reply_text(
            "No pending announcements."
        )
        return

    lines = ["Scheduled announcements:"]

    for item in messages:
        preview = item["text"][:80]

        lines.append(
            f"\nID: {item['id']}\n"
            f"Date: {display_date(item['send_time'])}\n"
            f"Text: {preview}"
        )

    result = "\n".join(lines)

    while result:
        await update.effective_message.reply_text(result[:4096])
        result = result[4096:]


@admin_only
async def edit_announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    parts = command_content(message).split(maxsplit=3)

    if len(parts) != 4 or not parts[0].isdigit():
        await message.reply_text(
            "Usage:\n"
            "/edit_announce ID YYYY-MM-DD HH:MM new text"
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
                "Announcement not found or no longer editable."
            )
            return

        validate_content(new_text, pending["media_type"])

    except ValueError as error:
        await message.reply_text(str(error))
        return

    changed = database.edit_scheduled_message(
        message_id,
        new_text,
        send_time,
    )

    if changed:
        await message.reply_text("Announcement updated!")
    else:
        await message.reply_text(
            "Announcement not found or no longer editable."
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
            "Usage: /cancel_announce ID"
        )
        return

    message_id = int(parts[0])
    cancelled = database.cancel_scheduled_message(message_id)

    if cancelled:
        await message.reply_text("Announcement cancelled!")
    else:
        await message.reply_text(
            "Announcement not found or already processed."
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
            successful, failed = await send_to_groups(
                context.bot,
                item["text"],
                item["media_type"],
                item["media_file_id"],
            )

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
                report = (
                    f"Scheduled announcement #{message_id} had delivery failures.\n"
                    f"Successful: {successful}\n"
                    f"Failed: {len(failed)}\n\n"
                    + "\n".join(failed)
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