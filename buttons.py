"""buttons.py - the "club website" button shown under the bot's replies.

The website address lives in the .env file as WEBSITE_URL (e.g. WEBSITE_URL=https://kfupmcc.example).
Until it is filled in, the button still appears, but pressing it only shows a
"coming soon" pop-up, so nothing can break.

Usage in any command:
    await update.message.reply_text("Hello", reply_markup=with_website())
    await update.message.reply_text("Pick one", reply_markup=with_website(my_keyboard))
"""
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

logger = logging.getLogger(__name__)

BUTTON_TEXT = "🌐 Club website"
COMING_SOON_DATA = "website:soon"
COMING_SOON_TEXT = "🌐 The club website is coming soon!"

_warned_about_bad_url = False


def website_url():
    """The URL from WEBSITE_URL, or "" if it is empty or not a valid http(s) address.
    (Telegram rejects any other kind of link, which would make every reply fail.)"""
    global _warned_about_bad_url
    url = os.getenv("WEBSITE_URL", "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        if not _warned_about_bad_url:
            logger.warning("WEBSITE_URL must start with http:// or https:// (got %r). Button disabled.", url)
            _warned_about_bad_url = True
        return ""
    return url


def website_row():
    """One row holding the website button (a real link if WEBSITE_URL is set, else a placeholder)."""
    url = website_url()
    if url:
        return [InlineKeyboardButton(BUTTON_TEXT, url=url)]
    return [InlineKeyboardButton(BUTTON_TEXT, callback_data=COMING_SOON_DATA)]


def with_website(markup=None):
    """Returns a keyboard made of `markup`'s buttons (if any) with the website button added at the bottom."""
    rows = [list(row) for row in markup.inline_keyboard] if markup else []
    rows.append(website_row())
    return InlineKeyboardMarkup(rows)


async def reply_long(message, text, limit=4096):
    """Replies with text that may be longer than Telegram's limit, split into pieces.
    The website button goes under the last piece only."""
    while text:
        chunk, text = text[:limit], text[limit:]
        await message.reply_text(chunk, reply_markup=None if text else with_website())


async def website_soon_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pressing the placeholder button (no WEBSITE_URL yet) shows a small pop-up."""
    await update.callback_query.answer(COMING_SOON_TEXT, show_alert=True)


def get_handlers():
    return [CallbackQueryHandler(website_soon_pressed, pattern=rf"^{COMING_SOON_DATA}$")]