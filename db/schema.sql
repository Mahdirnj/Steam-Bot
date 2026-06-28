-- SQLite schema for the Steam Deal Bot (PROJECT.md §6).
--
-- This file is the source of truth for the schema. The connection helper
-- (db/database.py, build step 2) executes it with executescript() on startup.
-- CREATE TABLE IF NOT EXISTS makes this idempotent.

-- users: one row per Telegram user.
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,         -- Telegram user id
    region_cc      TEXT NOT NULL DEFAULT 'US',  -- ISO country code for appdetails
    currency_code  INTEGER NOT NULL DEFAULT 1,  -- numeric code for priceoverview
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- wishlist: many rows per user, one per wishlisted game.
CREATE TABLE IF NOT EXISTS wishlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(user_id),
    appid       INTEGER NOT NULL,
    game_name   TEXT NOT NULL,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, appid)
);

-- price_snapshots: last known price per wishlisted game, used to detect changes.
CREATE TABLE IF NOT EXISTS price_snapshots (
    wishlist_id        INTEGER PRIMARY KEY REFERENCES wishlist(id),
    last_final_cents   INTEGER NOT NULL,
    last_discount_pct  INTEGER NOT NULL,
    last_checked_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
