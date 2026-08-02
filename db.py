"""SQLite-backed affection tracking. No economy/currency — just a per-user score."""

import datetime
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS affection (
            user_id TEXT PRIMARY KEY,
            score INTEGER NOT NULL DEFAULT 0,
            last_interaction_date TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_id TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            author, content, channel, timestamp,
            message_id UNINDEXED, channel_id UNINDEXED, guild_id UNINDEXED
        )
        """
    )
    # Migration: messages_fts predates the message_id/channel_id/guild_id columns
    # (added to support Discord jump links). FTS5 tables created before this change
    # need to be dropped and recreated — CREATE ... IF NOT EXISTS is a no-op against
    # an existing table with the old schema, so it won't add the columns on its own.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages_fts)")}
    if "message_id" not in existing_columns:
        conn.execute("DROP TABLE messages_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                author, content, channel, timestamp,
                message_id UNINDEXED, channel_id UNINDEXED, guild_id UNINDEXED
            )
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_affection(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute("SELECT score FROM affection WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else 0


def add_affection(conn: sqlite3.Connection, user_id: str, points_per_message: int, daily_bonus: int) -> int:
    """Atomic upsert — avoids a race between a SELECT and a later INSERT/UPDATE
    when on_message fires more than once for the same event (e.g. during a restart)."""
    today = datetime.date.today().isoformat()
    conn.execute(
        """
        INSERT INTO affection (user_id, score, last_interaction_date)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            score = score + ? + (
                CASE WHEN last_interaction_date != excluded.last_interaction_date THEN ? ELSE 0 END
            ),
            last_interaction_date = excluded.last_interaction_date
        """,
        (
            user_id,
            points_per_message + daily_bonus,  # first-ever message counts as first-of-day
            today,
            points_per_message,
            daily_bonus,
        ),
    )
    conn.commit()
    return get_affection(conn, user_id)


def log_token_usage(
    conn: sqlite3.Connection,
    user_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    conn.execute(
        """
        INSERT INTO token_usage (timestamp, user_id, prompt_tokens, completion_tokens, total_tokens)
        VALUES (?, ?, ?, ?, ?)
        """,
        (datetime.datetime.now().isoformat(), user_id, prompt_tokens, completion_tokens, total_tokens),
    )
    conn.commit()


def get_total_token_usage(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
        "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0) FROM token_usage"
    ).fetchone()
    calls, prompt_tokens, completion_tokens, total_tokens = row
    return {
        "calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def log_message(
    conn: sqlite3.Connection,
    author: str,
    content: str,
    channel: str,
    timestamp: str,
    message_id: str | None = None,
    channel_id: str | None = None,
    guild_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO messages_fts (author, content, channel, timestamp, message_id, channel_id, guild_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (author, content, channel, timestamp, message_id, channel_id, guild_id),
    )
    conn.commit()


def jump_link(row: dict) -> str | None:
    """Build a clickable Discord message link from a search-result row, if it has
    the IDs needed (older imported/logged rows from before jump-link support won't)."""
    if row.get("guild_id") and row.get("channel_id") and row.get("message_id"):
        return f"https://discord.com/channels/{row['guild_id']}/{row['channel_id']}/{row['message_id']}"
    return None


STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is",
    "are", "was", "were", "be", "been", "about", "with", "did", "does", "do",
    "who", "what", "when", "recently", "talk", "talked", "talking", "say", "said",
}


def search_messages(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """Keyword search over logged messages via SQLite FTS5.

    Splits the query into individual words (dropping common stopwords), escapes
    each as a literal token, and ORs them together — a message matches if it
    contains ANY of the significant words. This is deliberately loose: an exact
    phrase match would miss a message that expresses the same idea with
    different wording or word order (e.g. "league classic" vs. a query for
    "League of Legends classic").

    Each term is qualified with `content:` so the match is scored against the
    message body only — FTS5 matches across ALL columns by default, so an
    unqualified search for e.g. a username would also match every message
    where that name happens to be the value of the author column, i.e. every
    message that person ever sent, regardless of what it says."""
    raw_words = re.findall(r"[\w']+", query.lower())
    words = [w for w in raw_words if w not in STOPWORDS]
    if not words:
        words = raw_words  # fall back to the raw query if everything was a stopword
    quote = '"'
    escaped_terms = [f'content:{quote}{w.replace(quote, quote * 2)}{quote}' for w in words]
    fts_query = " OR ".join(escaped_terms)
    rows = conn.execute(
        """
        SELECT author, content, channel, timestamp, message_id, channel_id, guild_id
        FROM messages_fts
        WHERE messages_fts MATCH ?
        ORDER BY rowid DESC LIMIT ?
        """,
        (fts_query, limit),
    ).fetchall()
    return [
        {
            "author": r[0], "content": r[1], "channel": r[2], "timestamp": r[3],
            "message_id": r[4], "channel_id": r[5], "guild_id": r[6],
        }
        for r in rows
    ]


def get_messages_by_author(conn: sqlite3.Connection, author_name: str, limit: int = 30) -> list[dict]:
    """Fetch recent messages FROM a specific person — for questions like "what does
    X usually say" or "what are X's catchphrases", where the point is to sample that
    person's own messages, not to keyword-search message content. `search_messages`
    intentionally excludes author-name matches (see its docstring), so this is the
    dedicated path for author-scoped lookups. Case-insensitive substring match on the
    display name, since Discord names are inconsistent about capitalization."""
    rows = conn.execute(
        """
        SELECT author, content, channel, timestamp, message_id, channel_id, guild_id
        FROM messages_fts
        WHERE author LIKE ? ORDER BY rowid DESC LIMIT ?
        """,
        (f"%{author_name}%", limit),
    ).fetchall()
    return [
        {
            "author": r[0], "content": r[1], "channel": r[2], "timestamp": r[3],
            "message_id": r[4], "channel_id": r[5], "guild_id": r[6],
        }
        for r in rows
    ]


def save_impression(conn: sqlite3.Connection, user_id: str, note: str) -> None:
    conn.execute(
        "INSERT INTO impressions (user_id, note, created_at) VALUES (?, ?, ?)",
        (user_id, note, datetime.datetime.now().isoformat()),
    )
    conn.commit()


def get_impressions(conn: sqlite3.Connection, user_id: str, limit: int = 10) -> list[str]:
    rows = conn.execute(
        "SELECT note FROM impressions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


def stage_for_score(score: int, stages: list[dict]) -> dict:
    """`stages` is config['affection_stages'], sorted ascending by min_score."""
    current = stages[0]
    for stage in stages:
        if score >= stage["min_score"]:
            current = stage
    return current
