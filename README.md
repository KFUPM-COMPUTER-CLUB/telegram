<div align="center">

# 🤖 KFUPM Computer Club Bot

**The club's Telegram assistant: it announces, it reminds, and it keeps everyone in the loop.**

[![CI](https://github.com/KFUPM-COMPUTER-CLUB/telegram/actions/workflows/ci.yml/badge.svg)](https://github.com/KFUPM-COMPUTER-CLUB/telegram/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![python-telegram-bot 21+](https://img.shields.io/badge/python--telegram--bot-21%2B-26A5E4?logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/database-SQLite-003B57?logo=sqlite&logoColor=white)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-ff69b4)

[🌐 Website](https://cckfupm.vercel.app/) &nbsp;·&nbsp; [💬 Commands](#-commands) &nbsp;·&nbsp; [🚀 Quick start](#-quick-start) &nbsp;·&nbsp; [☁️ Hosting](docs/DEPLOY.md) &nbsp;·&nbsp; [🤝 Contribute](workflow.md)

</div>

---

## ✨ What it does

- 📢 **Announcements:** admins send text, photos or videos to every club group at once, or schedule them for later.
- 📅 **Events:** admins add events with a poster. Everyone can browse what's coming up with `/events`.
- 🔔 **Reminders:** every group gets an automatic reminder at 9:00 AM (Riyadh time) the day before each event.
- 📆 **Add to calendar:** every event comes with an *Add to Google Calendar* button.
- 👥 **Zero-setup groups:** add the bot to a group as an admin and the group is registered automatically.
- 🌐 **Club website:** a button under every reply opens [cckfupm.vercel.app](https://cckfupm.vercel.app/).

## 💬 What it looks like

`/events` shows the upcoming list, and tapping a number opens that event with its poster:

```text
📅 Upcoming events (2):

1. Python Workshop
    🗓 Tue, 27 Oct 2026 · in 4 days
2. Hack Night
    🗓 Thu, 05 Nov 2026 · in 13 days

👀 Tap a number to see the full event info:
[ 1 ] [ 2 ]
[ 🌐 Club website ]
```

The day before an event, each group gets:

```text
🔔 Reminder: Python Workshop is tomorrow!
📅 Tuesday, 27 October 2026
[ 📆 Add to Google Calendar ]
```

## 💬 Commands

### Everyone

| Command | What it does |
|---|---|
| `/help` | Lists the commands |
| `/events` | Upcoming events. Tap a number for the poster, date and an *Add to Google Calendar* button |

### Admins only

| Command | What it does |
|---|---|
| **📅 Events** | |
| `/addevent` | Add an event, step by step: name, date (`YYYY-MM-DD`), poster |
| `/editevent` | Change an event's name, date or poster |
| `/deleteevent` | Delete an event |
| `/sendreminder` | Send a reminder about one event, or all of them, to every group right now |
| **📢 Announcements** | |
| `/announce <text>` | Send a message to every group now. Attach a photo or video by putting the command in its caption |
| `/schedule_announce YYYY-MM-DD HH:MM <text>` | Send it later (Saudi time) |
| `/scheduled` | List the pending announcements |
| `/edit_announce <id> YYYY-MM-DD HH:MM <new text>` | Change a pending announcement |
| `/cancel_announce <id>` | Cancel a pending announcement |
| **👥 Groups and admins** | |
| `/groups` | List the registered groups |
| `/addadmin <user_id>` · `/removeadmin <user_id>` | Add or remove an admin |
| `/adminhelp` | Shows this list inside Telegram |

While adding or editing an event, `/cancel` stops and `/skip` skips the poster.

## ⚙️ How it works

- **Groups register themselves.** When an admin adds the bot to a group it is saved. When the bot is removed, the group is forgotten. Someone who isn't an admin can't register a group.
- **Reminders run on their own.** A daily job at 9:00 (Riyadh) reminds about tomorrow's events. The bot remembers what it already sent, so a restart never causes a duplicate, and a reminder missed while the bot was off goes out on the next start.
- **Scheduled announcements** are checked every 30 seconds and sent once, in Saudi time.
- **Everything is stored in `bot.db`**, a small SQLite file next to the code. Back it up before moving the bot to a new server.

## 🚀 Quick start

```bash
git clone https://github.com/KFUPM-COMPUTER-CLUB/telegram.git
cd telegram

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # Windows: copy .env.example .env
# open .env and fill in BOT_TOKEN and INITIAL_ADMIN_IDS (see below)

python main.py                     # wait for "Bot is running."
```

1. Install **Python 3.10+** and **Git** first.
2. Create **your own test bot** with [@BotFather](https://t.me/BotFather) and copy its token. Don't use the club's real bot while developing.
3. Get your Telegram user ID from [@userinfobot](https://t.me/userinfobot).
4. Send `/help` to your bot to check it works. To try group features, add it to a test group.

### Configuration (`.env`)

| Variable | Required | What it is |
|---|:-:|---|
| `BOT_TOKEN` | ✅ | The token from @BotFather |
| `INITIAL_ADMIN_IDS` | ✅ | Telegram user IDs of the first admins, separated by commas. More can be added later with `/addadmin` |
| `WEBSITE_URL` | | Overrides where the 🌐 button points. It opens `https://cckfupm.vercel.app/` by default |

## 🗂 Project structure

```text
telegram/
├── main.py              starts the bot and connects every module
├── database.py          all SQL lives here, nowhere else
├── admin.py             /help, /groups, admin management, the @admin_only decorator
├── events.py            events, Google Calendar button, automatic reminders
├── announcements.py     /announce and scheduled announcements
├── buttons.py           the 🌐 club website button
├── tests/smoke_test.py  quick self-check
├── .github/             automatic checks, pull request and issue templates
├── .env.example         settings template
├── requirements.txt
├── workflow.md          how we work: branches, pull requests, code rules
├── CONTRIBUTORS.md
```

## 🤝 Contributing

New here? Read **[workflow.md](workflow.md)**. The short version:

- Never commit `.env` or `bot.db`.
- Never edit `main` directly. Work on your own branch and open a pull request.
- Don't write SQL outside `database.py`. Add a function there instead.
- Admin-only commands use the `@admin_only` decorator from `admin.py`.
- Run `python tests/smoke_test.py` before you open a pull request.

## 👥 Contributors

<a href="https://github.com/Tamim196"><img src="https://github.com/Tamim196.png?size=64" width="64" alt="Tamim Aleidan" title="Tamim Aleidan"></a>
<a href="https://github.com/h4mza295"><img src="https://github.com/h4mza295.png?size=64" width="64" alt="Hamza Osman" title="Hamza Osman"></a>
<a href="https://github.com/DNILX"><img src="https://github.com/DNILX.png?size=64" width="64" alt="Daniel Albohassan" title="Daniel Albohassan"></a>

See who built what in [CONTRIBUTORS.md](CONTRIBUTORS.md).
