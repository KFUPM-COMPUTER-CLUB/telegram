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