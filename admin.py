"""admin.py - basic commands, admin authorization and automatic group registration."""
import functools
import logging

from telegram import Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import ChatMemberHandler, CommandHandler, ContextTypes

import database

logger = logging.getLogger(__name__)


def admin_only(func):
    """Put @admin_only above any command that only admins may use."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or not database.is_admin(user.id):
            # events feature: answer button presses so the spinner stops for non-admins
            if update.callback_query:
                await update.callback_query.answer()
            await update.effective_message.reply_text("⛔ This command is for administrators only.")
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


# What admins see with /help. Written for people who don't code: plain words, and an example
# for every command that needs more than its name. Examples are in <code>, so tapping one copies it.
HELP_TEXT = (
    "🛠 <b>How to use the Computer Club bot</b>\n"
    "Tap a blue command to use it. Tap a grey example to copy it.\n"
    "\n<code>GENERAL</code>\n"
    "/help · show this guide\n"
    "/start · see your Telegram ID, which you need to become an admin\n"
    "\n<code>EVENTS · ADMINS ONLY</code>\n"
    "/addevent · create an event. I'll ask for its name, date, description, then the poster and link.\n"
    "/events · see the upcoming events\n"
    "/editevent · fix something in an event\n"
    "/deleteevent · delete an event\n"
    "Every group gets a reminder the day before each event, automatically.\n"
    "\n<code>ANNOUNCEMENTS · ADMINS ONLY</code>\n"
    "/announce · send a message to every club group now. "
    "Write your message after the command, in the same message:\n"
    "<code>/announce Meeting moved to 7 PM!</code>\n"
    "/announce <b>event</b> · send an event to every group, with its poster, description and link. "
    "You pick the event from a list.\n"
    "• <b>Picture or video:</b> attach it and write the /announce message as its caption.\n"
    "• <b>Link button:</b> put the link alone on the last line of your message.\n"
    "\n<code>SEND IT LATER · ADMINS ONLY</code>\n"
    "/schedule_announce · like /announce, at a time you choose. Write the date and time first:\n"
    "<code>/schedule_announce 2026-11-01 17:00 Meeting tomorrow!</code>\n"
    "/schedule_announce 2026-11-01 17:00 <b>event</b> · send an event later\n"
    "Dates are year-month-day. Times are Saudi time on a 24-hour clock: 17:00 is 5 PM.\n"
    "/scheduled · see what's waiting to be sent. Each one has an ID number.\n"
    "/edit_announce · change one, using its ID:\n"
    "<code>/edit_announce 3 2026-11-01 18:00 New message</code>\n"
    "/cancel_announce · cancel one, using its ID:\n"
    "<code>/cancel_announce 3</code>\n"
    "\n<code>GROUPS AND ADMINS · ADMINS ONLY</code>\n"
    "/groups · see which groups I post in\n"
    "To add a group, an admin adds me to the Telegram group. That's it.\n"
    "/addadmin · make someone an admin with their ID (they get it from /start):\n"
    "<code>/addadmin 123456789</code>\n"
    "/removeadmin · remove an admin the same way"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start - open to everyone, so a future admin can get their user ID here.
    Explains how to find a user ID and how to add an admin."""
    user = update.effective_user
    text = (
        "👋 <b>Hi, I'm the Computer Club bot.</b>\n"
        "I post the club's announcements and event reminders in its groups. "
        "My commands are for club admins.\n"
        f"\n<code>YOUR USER ID</code>\n<code>{user.id}</code> (tap to copy)\n"
        "\n<code>FIND SOMEONE'S USER ID</code>\n"
        "Ask them to open a private chat with me and send /start. "
        "I'll show them their ID, just like yours above.\n"
        "\n<code>ADD AN ADMIN</code>\n"
        "1. The new admin sends me /start and copies their ID.\n"
        "2. They send it to an existing admin.\n"
        "3. That admin sends <code>/addadmin 123456789</code> with the real ID.\n"
        "/removeadmin works the same way.\n"
    )
    if database.is_admin(user.id):
        text += "\nYou're an admin. Send /help for every command."
    else:
        text += "\nNot an admin yet? Send your ID to a club admin."
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help - admins only. Lists every command. Non-admins get no reply at all."""
    user = update.effective_user
    if user is None or not database.is_admin(user.id):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def track_bot_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when the bot is added to or removed from a chat. Registers/unregisters groups automatically."""
    change = update.my_chat_member
    chat = change.chat
    if chat.type not in ("group", "supergroup"):
        return

    new_status = change.new_chat_member.status

    if new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        database.unregister_group(chat.id)
        logger.info("Bot removed from '%s' (%s): group unregistered.", chat.title, chat.id)

    elif new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        # Only register if one of OUR admins added the bot, so random people can't add it to their groups.
        if database.is_admin(change.from_user.id):
            database.register_group(chat.id, chat.title)
            logger.info("Registered group '%s' (%s).", chat.title, chat.id)
        else:
            logger.info("Bot added to '%s' (%s) by a non-admin: not registered.", chat.title, chat.id)


@admin_only
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = database.get_groups()
    if not groups:
        await update.message.reply_text("No groups registered yet.")
        return

    lines = [f"{i}. {g['title'] or g['group_id']}" for i, g in enumerate(groups, start=1)]
    message = f"Registered groups ({len(groups)}):"
    for line in lines:
        # Telegram messages are limited to 4096 characters, so long lists are split.
        if len(message) + len(line) + 1 > 4000:
            await update.message.reply_text(message)
            message = line
        else:
            message += "\n" + line
    await update.message.reply_text(message)


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Write their Telegram ID after the command, e.g. /addadmin 123456789\nThey can get it by sending me /start.")
        return
    database.add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin added.")


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Write their Telegram ID after the command, e.g. /removeadmin 123456789")
        return
    user_id = int(context.args[0])
    if user_id == update.effective_user.id:
        await update.message.reply_text("You can't remove yourself.")
        return
    removed = database.remove_admin(user_id)
    await update.message.reply_text("✅ Admin removed." if removed else "That user isn't an admin.")


def get_handlers():
    return [
        CommandHandler("help", help_command),
        CommandHandler("start", start_command),
        CommandHandler("groups", list_groups),
        CommandHandler("addadmin", add_admin_command),
        CommandHandler("removeadmin", remove_admin_command),
        ChatMemberHandler(track_bot_membership, ChatMemberHandler.MY_CHAT_MEMBER),
    ]