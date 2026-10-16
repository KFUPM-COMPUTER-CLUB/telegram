# Computer Club Telegram Bot

A bot that sends announcements to the club's Telegram groups and reminds members about upcoming events.

## Setup
1. Install Python 3.10+ and Git.
2. Create a virtual environment and activate it:
   - `python3 -m venv .venv`
   - Mac/Linux: `source .venv/bin/activate`  |  Windows: `.venv\Scripts\activate`
3. Install libraries: `pip install -r requirements.txt`
4. Create your OWN test bot with @BotFather on Telegram and copy its token.
5. Copy `.env.example` to `.env` and fill in `BOT_TOKEN` and `INITIAL_ADMIN_IDS`
   (get your user ID from @userinfobot).
6. Run the bot: `python main.py`

Groups are registered automatically when an admin adds the bot to them (and removed when the bot is removed). Use `/groups` to see the list.

## Rules
- Never commit `.env` or `bot.db`.
- Never edit `main` directly. Work on your own branch and open a pull request.
- Don't write SQL outside `database.py`. Add a function there instead.
- Admin-only commands use the `@admin_only` decorator from `admin.py`.