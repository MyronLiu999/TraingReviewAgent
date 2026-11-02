"""SQLite storage: remembers seen headlines and logs every verdict for later review."""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    uid TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analyzed_at TEXT NOT NULL,
    published TEXT,
    source TEXT,
    title TEXT,
    link TEXT,
    market TEXT,
    verdict TEXT,
    impact INTEGER,
    confidence TEXT,
    alerted INTEGER,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str = "news_alerts.db"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def is_empty(self) -> bool:
        return self.db.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def unseen(self, uids: list[str]) -> set[str]:
        seen = {
            row[0] for row in self.db.execute(
                f"SELECT uid FROM seen WHERE uid IN ({','.join('?' * len(uids))})", uids
            )
        } if uids else set()
        return set(uids) - seen

    def mark_seen(self, uids: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", [(u, now) for u in uids])
        self.db.commit()

    def log_verdict(self, item, analysis: dict, alerted: bool) -> None:
        self.db.execute(
            "INSERT INTO verdicts (analyzed_at, published, source, title, link, market, verdict,"
            " impact, confidence, alerted, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                # Same format as SQLite's datetime('now') so time-window queries compare correctly
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                item.published.isoformat() if item.published else None,
                item.source, item.title, item.link,
                analysis["market"], analysis["verdict"], analysis["impact"],
                analysis["confidence"], int(alerted), json.dumps(analysis),
            ),
        )
        self.db.commit()

    def get_setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def recent_verdicts(self, hours: int = 4) -> list[tuple]:
        return self.db.execute(
            "SELECT market, verdict, impact FROM verdicts "
            "WHERE analyzed_at >= datetime('now', ?) AND verdict != 'IRRELEVANT'",
            (f"-{hours} hours",),
        ).fetchall()
