"""main.py - starts the bot and connects everyone's work."""
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

import database
import admin
import events
import announcements

load_dotenv()  # reads the .env file

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


async def error_handler(update, context):
    logger.error("Error while handling an update:", exc_info=context.error)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")

    database.create_tables()
    admin_ids = [int(x) for x in os.getenv("INITIAL_ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    for admin_id in admin_ids:
        database.add_admin(admin_id)

    app = Application.builder().token(token).build()

    # Each file provides its handlers through get_handlers()
    for module in (events, announcements, admin):
        for handler in module.get_handlers():
            app.add_handler(handler)

    app.add_error_handler(error_handler)
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()