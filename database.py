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
                date TEXT NOT NULL
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
            """
        )
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
