from telegram import Update 
from telegram.ext import CommandHandler, ContextTypes
from admin import admin_only
import database
from telegram.error import Forbidden, TelegramError

def get_handlers():
    return [
        CommandHandler("announce", announce)
    ]


@admin_only
async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message


    content = message.caption if message.caption is not None else message.text
    sentence = content.partition(" ")[2].strip() if content else ""

    if not sentence:
        await message.reply_text("Please provide an announcement message.")
        return

    # Check Telegram message
    if len(sentence) > (1024 if message.photo or message.video else 4096):
        await message.reply_text("Announcement text is too long.")
        return

    groups = database.get_groups()

    if not groups:
        await message.reply_text("No registered groups found.")
        return

    successful = 0
    failed = []

    for group in groups:
        group_id = group["group_id"]
        group_name = group["title"] or str(group_id)

        try:
            if message.photo:
                await context.bot.send_photo(
                    chat_id=group_id,
                    photo=message.photo[-1].file_id,
                    caption=sentence
                )

            elif message.video:
                await context.bot.send_video(
                    chat_id=group_id,
                    video=message.video.file_id,
                    caption=sentence
                )

            else:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=sentence
                )

            successful += 1

        except Forbidden:
            failed.append(f"{group_name}: Bot lacks permission.")

        except TelegramError as error:
            failed.append(f"{group_name}: {error}")


    result = (
        f"Announcement complete!\n"
        f"Successful: {successful}\n"
        f"Failed: {len(failed)}"
    )

    if failed:
        result += "\n\nErrors:\n" + "\n".join(failed)

    # Split the report if it exceeds Telegram's message limit --> chatgpt told me to do this just incase
    while result:
        await message.reply_text(result[:4096])
        result = result[4096:]
