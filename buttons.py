"""buttons.py - the "club website" button shown under the bot's replies.

The button opens DEFAULT_WEBSITE_URL. To point it somewhere else (for example while testing),
set WEBSITE_URL in the .env file. A value that is not a proper http(s) link is ignored.

Usage in any command:
    await update.message.reply_text("Hello", reply_markup=with_website())
    await update.message.reply_text("Pick one", reply_markup=with_website(my_keyboard))
"""
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

logger = logging.getLogger(__name__)

DEFAULT_WEBSITE_URL = "https://cckfupm.vercel.app/"
BUTTON_TEXT = "🌐 Club website"

# Messages sent before the website existed carry a placeholder button with this callback data.
# They still work: pressing one now replies with the real link (see legacy_button_pressed).
LEGACY_BUTTON_DATA = "website:soon"

_warned_about_bad_url = False


def website_url():
    """WEBSITE_URL from the environment if it is a valid http(s) link, otherwise DEFAULT_WEBSITE_URL.
    (Telegram rejects any other kind of link, which would make every reply fail.)"""
    global _warned_about_bad_url
    url = os.getenv("WEBSITE_URL", "").strip()
    if not url:
        return DEFAULT_WEBSITE_URL
    if not url.startswith(("http://", "https://")):
        if not _warned_about_bad_url:
            logger.warning("WEBSITE_URL must start with http:// or https:// (got %r). Using the default.", url)
            _warned_about_bad_url = True
        return DEFAULT_WEBSITE_URL
    return url


def website_row():
    """One row holding the website button."""
    return [InlineKeyboardButton(BUTTON_TEXT, url=website_url())]


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


async def legacy_button_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Someone pressed an old 'coming soon' button from before the website was live: send the link."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🌐 The club website is live!", reply_markup=InlineKeyboardMarkup([website_row()]))


def get_handlers():
    return [CallbackQueryHandler(legacy_button_pressed, pattern=rf"^{LEGACY_BUTTON_DATA}$")]
