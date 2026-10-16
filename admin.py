"""admin.py - basic commands, admin authorization and automatic group registration."""
import functools
import logging

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ChatMemberHandler, CommandHandler, ContextTypes

import database

logger = logging.getLogger(__name__)


def admin_only(func):
    """Put @admin_only above any command that only admins may use."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or not database.is_admin(user.id):
            await update.effective_message.reply_text("⛔ This command is for administrators only.")
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the Computer Club bot!\nUse /help to see what I can do."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - welcome message\n"
        "/help - this list\n"
        "\nAdmin commands:\n"
        "/groups - list of registered groups\n"
        "/addadmin <user_id> - add an admin\n"
        "/removeadmin <user_id> - remove an admin\n"
        "\nTo register a group, just add the bot to that group."
    )


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
        await update.message.reply_text("Usage: /addadmin <telegram_user_id>")
        return
    database.add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin added.")


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <telegram_user_id>")
        return
    user_id = int(context.args[0])
    if user_id == update.effective_user.id:
        await update.message.reply_text("You can't remove yourself.")
        return
    removed = database.remove_admin(user_id)
    await update.message.reply_text("✅ Admin removed." if removed else "That user isn't an admin.")


def get_handlers():
    return [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("groups", list_groups),
        CommandHandler("addadmin", add_admin_command),
        CommandHandler("removeadmin", remove_admin_command),
        ChatMemberHandler(track_bot_membership, ChatMemberHandler.MY_CHAT_MEMBER),
    ]