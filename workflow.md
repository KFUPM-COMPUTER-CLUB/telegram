# Workflow

How we work on the KFUPM Computer Club bot: how to make a change, check it and get it merged.
First get the bot running on your own computer by following **Setup** in the [README](README.md).

## The short version
1. Create your **own test bot** with @BotFather. Never test with the real club bot's token.
2. Make a branch, make your change, test it in Telegram.
3. Run `python tests/smoke_test.py`.
4. Open a pull request and ask a teammate to review it.

## Making a change

```bash
git switch main
git pull                              # always start from the latest main
git switch -c feature/short-name      # or fix/short-name for bug fixes
# ...edit files...
git status                            # check that only the files you meant to change are listed
git add file1.py file2.py             # add files by name, not "git add ."
git commit -m "Add /next command"
git push -u origin feature/short-name
```

Then open a pull request on GitHub (a checklist appears in the description). Never push to `main` directly.
After it is merged, clean up:

```bash
git switch main && git pull && git branch -d feature/short-name
```

**Commit messages:** one short line that says what changed, starting with a verb ("Add ...", "Fix ...", "Update ...").
**Branch names:** `feature/...` for new things, `fix/...` for bugs, `docs/...` for documentation.

## Where things live

| Path | What it does |
|---|---|
| `main.py` | Starts the bot and connects every module's handlers |
| `database.py` | **All** database code |
| `admin.py` | `/start`, `/help`, `/groups`, admin management, the `@admin_only` decorator |
| `events.py` | Event commands, event cards and buttons, and the automatic day-before reminders |
| `announcements.py` | `/announce`, event announcements and scheduled announcements |
| `brand.py` | The club's look and voice: labels, footer, HTML escaping, link checks |
| `buttons.py` | Colored buttons and the 🌐 club website button added under announcements and reminders |
| `tests/smoke_test.py` | Quick self-check: every module loads, every handler builds, the database works |

## Rules for the code
- **No SQL outside `database.py`.** Add a function there and call it.
- **Admin-only commands** get the `@admin_only` decorator from `admin.py`.
- **Only announcements and reminders sent into groups carry the website button:** pass `reply_markup=with_website()` (or `with_website(your_keyboard)` to add it under other buttons). Replies to admin commands don't get it.
- **Every command is admin-only:** put `@admin_only` on new commands. Only `/start` is open to everyone.
- **Follow the brand:** build messages with the helpers in `brand.py` (`kicker`, `headline`, `post`) and read the rules at the top of that file. Messages are HTML, so pass anything a user typed through `esc()`. Buttons come from `buttons.py`: `link_button` for links, `action_button` with `GREEN` to confirm or `RED` to delete.
- **Times are Saudi time.** Use `_today()` in `events.py`, not `date.today()`, because the server may be in another timezone.
- **Long messages:** Telegram allows 4096 characters per message (1024 for a photo or video caption).
- **Secrets:** never commit `.env` or `bot.db` (they are in `.gitignore`). If a bot token is ever pushed to GitHub, revoke it right away with `/revoke` in @BotFather, because deleting the commit is not enough.

## Adding a new command
1. Write the handler function in the file where it belongs (or a new file, which needs a `get_handlers()` function).
2. Return it from that file's `get_handlers()`. If you created a new file, also add it to the module list in `main.py`.
3. List it in `HELP_TEXT` in `admin.py` so admins can find it. The marketing team uses the bot, so write it in plain words (no `<placeholders>`) and add a copyable example in `<code>` if the command needs more than its name.
4. Run it on your test bot, including the failure cases: wrong input, a non-admin trying it, an empty list.

## Before you open the pull request
- `python tests/smoke_test.py` prints "All checks passed". (GitHub runs it again automatically on your pull request.)
- The bot starts and shows "Bot is running." with no errors.
- You tried your change in Telegram, as an admin and as a normal user (who should get no access).
- `git status` shows no `.env`, `bot.db` or `.venv`.

## Found a bug or have an idea?
Open an issue on GitHub. 