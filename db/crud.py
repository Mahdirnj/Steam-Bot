"""CRUD operations for the Steam Deal Bot (PROJECT.md §10 step 2, §6 schema).

Tables:
    users           — one row per Telegram user (region/currency settings)
    wishlist        — one row per wishlisted game per user
    price_snapshots — last observed price per wishlist item, used by the scheduler

Design notes:
- Every function assumes init_db() has been called; they obtain the live
  connection via get_db().
- Writes commit explicitly.
- Returns plain dicts (dict(row)), not Row objects, to avoid use-after-close
  surprises and to keep the handler layer clean.
- Currency code is always passed in by the caller (resolved via region_map.py,
  build step 3). This module knows nothing about region->currency mapping.
"""
from typing import Optional

from db.database import get_db


async def _ensure_user(user_id: int) -> None:
    """Insert a user row with defaults if it doesn't exist.

    Required before any wishlist write because wishlist.user_id is a FK to
    users.user_id and foreign keys are enforced.
    """
    db = get_db()
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,),
    )
    await db.commit()


async def get_or_create_user(user_id: int) -> dict:
    """Return the user row, creating it with defaults if absent.

    Defaults: region_cc='US', currency_code=1.
    """
    db = get_db()
    await _ensure_user(user_id)
    async with db.execute(
        "SELECT user_id, region_cc, currency_code, created_at "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row)


async def set_region(user_id: int, cc: str, currency_code: int) -> None:
    """Persist the user's region (ISO country code) and derived currency code."""
    await _ensure_user(user_id)
    db = get_db()
    await db.execute(
        "UPDATE users SET region_cc = ?, currency_code = ? WHERE user_id = ?",
        (cc, currency_code, user_id),
    )
    await db.commit()


async def add_wishlist_item(user_id: int, appid: int, game_name: str) -> bool:
    """Add a game to the user's wishlist.

    Returns True if newly added, False if it was already present (dedup via the
    UNIQUE(user_id, appid) constraint).
    """
    await _ensure_user(user_id)
    db = get_db()
    cur = await db.execute(
        "INSERT OR IGNORE INTO wishlist (user_id, appid, game_name) VALUES (?, ?, ?)",
        (user_id, appid, game_name),
    )
    await db.commit()
    return cur.rowcount == 1


async def remove_wishlist_item(user_id: int, appid: int) -> bool:
    """Remove a game from the user's wishlist.

    Deletes any price_snapshots row first so the foreign-key constraint on
    price_snapshots.wishlist_id is not violated. Returns True if a wishlist
    row was removed, False if the game wasn't wishlisted.
    """
    db = get_db()
    # Find the wishlist id (if any) before deleting it.
    async with db.execute(
        "SELECT id FROM wishlist WHERE user_id = ? AND appid = ?",
        (user_id, appid),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    wishlist_id = row["id"]

    # Child first (FK-safe), then parent.
    await db.execute("DELETE FROM price_snapshots WHERE wishlist_id = ?", (wishlist_id,))
    await db.execute("DELETE FROM wishlist WHERE id = ?", (wishlist_id,))
    await db.commit()
    return True


async def list_wishlist_for_user(user_id: int) -> list[dict]:
    """Return all wishlist rows for a user, oldest first."""
    db = get_db()
    async with db.execute(
        "SELECT id, user_id, appid, game_name, added_at "
        "FROM wishlist WHERE user_id = ? ORDER BY added_at ASC, id ASC",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_all_wishlist_with_users() -> list[dict]:
    """Return every wishlist row joined with its user's settings.

    Used by the scheduler job to know which region/currency to fetch each game's
    price in. Columns: wishlist_id, user_id, appid, game_name, region_cc,
    currency_code.
    """
    db = get_db()
    async with db.execute(
        "SELECT w.id   AS wishlist_id, "
        "       w.user_id AS user_id, "
        "       w.appid AS appid, "
        "       w.game_name AS game_name, "
        "       u.region_cc AS region_cc, "
        "       u.currency_code AS currency_code "
        "FROM wishlist w JOIN users u ON u.user_id = w.user_id "
        "ORDER BY w.id ASC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_snapshot(wishlist_id: int) -> Optional[dict]:
    """Return the last observed price snapshot for a wishlist item, or None."""
    db = get_db()
    async with db.execute(
        "SELECT wishlist_id, last_final_cents, last_discount_pct, last_checked_at "
        "FROM price_snapshots WHERE wishlist_id = ?",
        (wishlist_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def upsert_snapshot(
    wishlist_id: int, last_final_cents: int, last_discount_pct: int
) -> None:
    """Insert or update the snapshot for a wishlist item.

    Refreshes last_checked_at to now on every call.
    """
    db = get_db()
    await db.execute(
        "INSERT INTO price_snapshots "
        "    (wishlist_id, last_final_cents, last_discount_pct, last_checked_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(wishlist_id) DO UPDATE SET "
        "    last_final_cents  = excluded.last_final_cents, "
        "    last_discount_pct = excluded.last_discount_pct, "
        "    last_checked_at   = datetime('now')",
        (wishlist_id, last_final_cents, last_discount_pct),
    )
    await db.commit()
