"""SQLite connection helper (PROJECT.md §10 step 2).

Single long-lived async connection — appropriate for a single-process bot.
`init_db(path)` is pure (no `config` import) so it can be unit-tested with an
in-memory DB. The schema path is resolved relative to this file so it works
regardless of the current working directory.
"""
from pathlib import Path
from typing import Optional

import aiosqlite

# Module-level connection singleton.
_db: Optional[aiosqlite.Connection] = None

# Schema file sits next to this module (db/schema.sql).
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


async def init_db(path: str) -> None:
    """Open the SQLite database at `path` and create its schema.

    Idempotent: schema.sql uses CREATE TABLE IF NOT EXISTS. Safe to call on
    every startup. `:memory:` is supported for tests.
    """
    global _db
    if _db is not None:
        raise RuntimeError("Database already initialized. Call close_db() first.")
    _db = await aiosqlite.connect(path)
    _db.row_factory = aiosqlite.Row
    # Enforce FK constraints (off by default in SQLite). Defense-in-depth:
    # the app also cleans up child rows explicitly before deleting parents.
    await _db.execute("PRAGMA foreign_keys = ON")
    await _db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    await _db.commit()


def get_db() -> aiosqlite.Connection:
    """Return the live connection. Raises if init_db() has not been called."""
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first.")
    return _db


async def close_db() -> None:
    """Close the connection. Safe to call multiple times."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
