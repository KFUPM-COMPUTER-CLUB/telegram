"""database.py - all database code lives here.

Rule: nobody writes SQL anywhere else. Call functions from this file instead.
"""
import sqlite3

DB_PATH = "bot.db"


def _run(query, params=(), fetch="none"):
    """Runs one SQL command. fetch: 'all' list, 'one' row, 'id' new row id, 'none' rows changed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(query, params)
        conn.commit()
        if fetch == "all":
            return [dict(row) for row in cur.fetchall()]
        if fetch == "one":
            row = cur.fetchone()
            return dict(row) if row else None
        if fetch == "id":
            return cur.lastrowid
        return cur.rowcount
    finally:
        conn.close()


def create_tables():
    """Creates the tables if they don't exist yet. Called when the bot starts."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                poster TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_groups (
                group_id INTEGER PRIMARY KEY,
                title TEXT
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                name TEXT
            );
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                send_time TEXT NOT NULL
            );
            -- Added for event reminders: remembers which (event, date) already got a reminder,
            -- so the bot never sends one twice, even after a restart.
            CREATE TABLE IF NOT EXISTS reminders_sent (
                event_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                PRIMARY KEY (event_id, event_date)
            );
            """
        )
        # Added for posters: older databases were created without the poster column, so add it.
        # (poster holds the Telegram file_id of the photo; NULL means the event has no poster.)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        if "poster" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN poster TEXT")
        conn.commit()
    finally:
        conn.close()


# ---------- Admins ----------

def add_admin(user_id, name=None):
    _run("INSERT OR IGNORE INTO admins (user_id, name) VALUES (?, ?)", (user_id, name))


def remove_admin(user_id):
    return _run("DELETE FROM admins WHERE user_id = ?", (user_id,))


def is_admin(user_id):
    return _run("SELECT 1 AS ok FROM admins WHERE user_id = ?", (user_id,), "one") is not None


def get_admins():
    return _run("SELECT * FROM admins", fetch="all")


# ---------- Telegram groups ----------

def register_group(group_id, title):
    _run(
        "INSERT INTO telegram_groups (group_id, title) VALUES (?, ?) "
        "ON CONFLICT(group_id) DO UPDATE SET title = excluded.title",
        (group_id, title),
    )


def unregister_group(group_id):
    return _run("DELETE FROM telegram_groups WHERE group_id = ?", (group_id,))


def get_groups():
    """All registered groups, as a list of dicts with 'group_id' and 'title'."""
    return _run("SELECT * FROM telegram_groups ORDER BY title, group_id", fetch="all")



# ---------- Scheduled announcements ----------

def prepare_scheduled_messages():
    """Upgrade the existing scheduled_messages table safely."""
    conn = sqlite3.connect(DB_PATH)
    try:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(scheduled_messages)"
            )
        }

        additions = {
            "media_type": "TEXT",
            "media_file_id": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
        }

        for name, definition in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE scheduled_messages "
                    f"ADD COLUMN {name} {definition}"
                )

        conn.commit()
    finally:
        conn.close()


def add_scheduled_message(text, send_time, media_type=None, media_file_id=None):
    return _run(
        """
        INSERT INTO scheduled_messages
        (text, send_time, media_type, media_file_id, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (text, send_time, media_type, media_file_id),
        fetch="id",
    )


def get_scheduled_messages():
    return _run(
        """
        SELECT * FROM scheduled_messages
        WHERE status = 'pending'
        ORDER BY send_time
        """,
        fetch="all",
    )


def get_due_messages(now):
    return _run(
        """
        SELECT * FROM scheduled_messages
        WHERE status = 'pending' AND send_time <= ?
        ORDER BY send_time
        """,
        (now,),
        fetch="all",
    )


def claim_scheduled_message(message_id):
    return _run(
        """
        UPDATE scheduled_messages
        SET status = 'sending'
        WHERE id = ? AND status = 'pending'
        """,
        (message_id,),
    ) == 1


def edit_scheduled_message(message_id, text, send_time):
    return _run(
        """
        UPDATE scheduled_messages
        SET text = ?, send_time = ?
        WHERE id = ? AND status = 'pending'
        """,
        (text, send_time, message_id),
    )


def cancel_scheduled_message(message_id):
    return _run(
        """
        UPDATE scheduled_messages
        SET status = 'cancelled'
        WHERE id = ? AND status = 'pending'
        """,
        (message_id,),
    )


def finish_scheduled_message(message_id, status):
    if status not in ("sent", "failed"):
        raise ValueError("Invalid scheduled message status")

    return _run(
        """
        UPDATE scheduled_messages
        SET status = ?
        WHERE id = ? AND status = 'sending'
        """,
        (status, message_id),
    )


# ---------- Events ----------
# Added for the add-event feature (events.py): saves a new event row.

def add_event(title, date, poster=None):
    """Saves an event. date must be text 'YYYY-MM-DD'; poster is the photo's Telegram file_id
    or None. Returns the new event id."""  # changed: poster added
    return _run("INSERT INTO events (title, date, poster) VALUES (?, ?, ?)", (title, date, poster), "id")


# Added for /events: lists events that haven't passed yet.
def get_upcoming_events(today):
    """Events dated today or later, soonest first. today must be text 'YYYY-MM-DD'."""
    return _run("SELECT * FROM events WHERE date >= ? ORDER BY date, id", (today,), "all")


# Added for /deleteevent: looks up one event (for the success message) and removes it.
def get_event(event_id):
    """One event as a dict, or None if that id doesn't exist."""
    return _run("SELECT * FROM events WHERE id = ?", (event_id,), "one")


def delete_event(event_id):
    """Deletes the event with that id. Returns 1 if it existed, 0 if not."""
    _run("DELETE FROM reminders_sent WHERE event_id = ?", (event_id,))  # added: tidy up reminder rows
    return _run("DELETE FROM events WHERE id = ?", (event_id,))


# Added for /editevent: change one field of an existing event.
def update_event_title(event_id, title):
    return _run("UPDATE events SET title = ? WHERE id = ?", (title, event_id))


def update_event_date(event_id, date):
    """date must be text 'YYYY-MM-DD'."""
    return _run("UPDATE events SET date = ? WHERE id = ?", (date, event_id))


def update_event_poster(event_id, poster):
    """poster is the photo's Telegram file_id."""  # added for /editevent -> Poster
    return _run("UPDATE events SET poster = ? WHERE id = ?", (poster, event_id))


# Added for automatic reminders.
def get_events_needing_reminder(dates):
    """Events whose date is one of the given 'YYYY-MM-DD' strings and that haven't had a
    reminder for that date yet. Editing an event's date makes it due again."""
    placeholders = ",".join("?" * len(dates))
    return _run(
        f"SELECT * FROM events e WHERE e.date IN ({placeholders}) AND NOT EXISTS "
        "(SELECT 1 FROM reminders_sent r WHERE r.event_id = e.id AND r.event_date = e.date) "
        "ORDER BY e.date, e.id",
        tuple(dates),
        "all",
    )


def mark_reminder_sent(event_id, event_date):
    _run(
        "INSERT OR IGNORE INTO reminders_sent (event_id, event_date) VALUES (?, ?)",
        (event_id, event_date),
    )