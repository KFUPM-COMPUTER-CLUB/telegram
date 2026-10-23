"""admin.py - basic commands, admin authorization and automatic group registration."""
import functools
import logging

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ChatMemberHandler, CommandHandler, ContextTypes

import database
from buttons import with_website

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
            await update.effective_message.reply_text("⛔ This command is for administrators only.", reply_markup=with_website())
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


# What normal users see with /help. Only commands that everyone may use.
USER_HELP = (
    "Commands:\n"
    "/help - this list\n"
    "/events - view upcoming events"
)

# What admins see with /adminhelp, grouped by topic.
ADMIN_HELP = (
    "🔧 Admin commands\n"
    "\n📅 Events\n"
    "/addevent - add a new event\n"
    "/editevent - edit an event's name, date or poster\n"
    "/deleteevent - delete an event\n"
    "/sendreminder - remind all groups about an event now\n"
    "Groups also get an automatic reminder the day before each event.\n"
    "\n📢 Announcements\n"
    "/announce <text> - send a message to all groups now\n"
    "/schedule_announce YYYY-MM-DD HH:MM <text> - send it later (Saudi time)\n"
    "/scheduled - list pending announcements\n"
    "/edit_announce <id> YYYY-MM-DD HH:MM <new text> - change a pending one\n"
    "/cancel_announce <id> - cancel a pending one\n"
    "To include a photo or video, send it with the command in its caption.\n"
    "\n👥 Groups\n"
    "/groups - list the registered groups\n"
    "To register a group, an admin just adds the bot to it.\n"
    "\n🛡 Admins\n"
    "/addadmin <user_id> - add an admin\n"
    "/removeadmin <user_id> - remove an admin\n"
    "/adminhelp - show this list"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help - open to everyone. Lists only the commands normal users can use."""
    text = USER_HELP
    user = update.effective_user
    if user is not None and database.is_admin(user.id):
        text += "\n\n🔧 You're an admin: send /adminhelp to see your commands."
    await update.message.reply_text(text, reply_markup=with_website())


@admin_only
async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/adminhelp - admins only. Lists the admin commands."""
    await update.message.reply_text(ADMIN_HELP, reply_markup=with_website())


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
        await update.message.reply_text("No groups registered yet.", reply_markup=with_website())
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
    await update.message.reply_text(message, reply_markup=with_website())


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <telegram_user_id>", reply_markup=with_website())
        return
    database.add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin added.", reply_markup=with_website())


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <telegram_user_id>", reply_markup=with_website())
        return
    user_id = int(context.args[0])
    if user_id == update.effective_user.id:
        await update.message.reply_text("You can't remove yourself.", reply_markup=with_website())
        return
    removed = database.remove_admin(user_id)
    await update.message.reply_text("✅ Admin removed." if removed else "That user isn't an admin.", reply_markup=with_website())


def get_handlers():
    return [
        CommandHandler("help", help_command),
        CommandHandler("adminhelp", admin_help_command),
        CommandHandler("groups", list_groups),
        CommandHandler("addadmin", add_admin_command),
        CommandHandler("removeadmin", remove_admin_command),
        ChatMemberHandler(track_bot_membership, ChatMemberHandler.MY_CHAT_MEMBER),
    ]