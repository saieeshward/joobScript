"""
SQLite-backed seen-job tracker for early-stop deduplication.
Stores job IDs only — O(1) lookup via primary key index.
Old entries are pruned after 30 days on each startup.
"""
from __future__ import annotations
import sqlite3, threading, logging

log = logging.getLogger(__name__)
_DB_PATH = "seen_jobs.db"
_conn: sqlite3.Connection | None = None
_write_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                job_id     TEXT PRIMARY KEY,
                first_seen TEXT DEFAULT (date('now'))
            )
        """)
        # Rolling 30-day window — keeps DB from growing unboundedly
        deleted = _conn.execute(
            "DELETE FROM seen_jobs WHERE first_seen < date('now', '-30 days')"
        ).rowcount
        _conn.commit()
        if deleted:
            log.info("Pruned %d stale entries from seen_jobs.db", deleted)
        log.debug("Opened seen_jobs.db (%d active records)", count())
    return _conn


def filter_new(cards: list[dict], id_key: str = "job_id") -> tuple[list[dict], float]:
    """
    Return (new_cards, seen_ratio).
    Uses a single IN query instead of N individual lookups.
    """
    if not cards:
        return [], 0.0
    ids = [c[id_key] for c in cards]
    placeholders = ",".join("?" * len(ids))
    existing = {
        row[0] for row in _get_conn().execute(
            f"SELECT job_id FROM seen_jobs WHERE job_id IN ({placeholders})", ids
        )
    }
    new = [c for c in cards if c[id_key] not in existing]
    seen_ratio = 1.0 - len(new) / len(cards)
    return new, seen_ratio


def mark_seen_bulk(job_ids: list[str]) -> None:
    conn = _get_conn()
    with _write_lock:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_jobs (job_id) VALUES (?)",
            [(jid,) for jid in job_ids],
        )
        conn.commit()


def count() -> int:
    return _get_conn().execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
