"""Smoke test for the database layer (PROJECT.md §10 step 2).

Uses an in-memory SQLite DB so it never touches real files. Run:

    python scripts/test_db.py

Exits 0 on success, 1 on failure.
"""
import asyncio
import sys

# Make the project root importable when run as a script from any directory.
sys.path.insert(0, "..")

from db import database  # noqa: E402
from db import crud  # noqa: E402


def check(label: str, condition: bool) -> None:
    """Tiny assertion helper with a readable failure."""
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


async def main() -> None:
    print("Running database smoke test (in-memory)...")
    await database.init_db(":memory:")
    try:
        # --- users: defaults ---
        u = await crud.get_or_create_user(111)
        check("get_or_create_user creates with defaults",
              u["user_id"] == 111 and u["region_cc"] == "US" and u["currency_code"] == 1)

        # idempotent: second call returns the same row, not a duplicate
        await crud.get_or_create_user(111)
        check("get_or_create_user is idempotent", u["region_cc"] == "US")

        # --- set_region round-trip ---
        await crud.set_region(111, "TR", 17)
        u = await crud.get_or_create_user(111)
        check("set_region persists cc + currency_code",
              u["region_cc"] == "TR" and u["currency_code"] == 17)

        # --- wishlist add: new ---
        added = await crud.add_wishlist_item(111, 1245620, "Elden Ring")
        check("add_wishlist_item returns True when newly added", added is True)

        # --- wishlist add: dedup ---
        added_again = await crud.add_wishlist_item(111, 1245620, "Elden Ring")
        check("add_wishlist_item returns False on duplicate", added_again is False)

        # --- wishlist add for a second game / second user ---
        await crud.add_wishlist_item(111, 1086940, "Baldur's Gate 3")
        await crud.add_wishlist_item(222, 1245620, "Elden Ring")
        items = await crud.list_wishlist_for_user(111)
        check("list_wishlist_for_user returns only this user's games",
              [i["appid"] for i in items] == [1245620, 1086940])

        # --- snapshots: get on missing returns None ---
        wl = items[0]  # Elden Ring, user 111
        snap = await crud.get_snapshot(wl["id"])
        check("get_snapshot returns None when absent", snap is None)

        # --- snapshots: upsert + get round-trip ---
        await crud.upsert_snapshot(wl["id"], last_final_cents=43900, last_discount_pct=20)
        snap = await crud.get_snapshot(wl["id"])
        check("upsert_snapshot inserts with correct values",
              snap["last_final_cents"] == 43900 and snap["last_discount_pct"] == 20)

        # --- snapshots: upsert updates existing row (no duplicate) ---
        await crud.upsert_snapshot(wl["id"], last_final_cents=29500, last_discount_pct=45)
        snap = await crud.get_snapshot(wl["id"])
        check("upsert_snapshot updates in place", snap["last_final_cents"] == 29500)

        # --- scheduler JOIN: returns all wishlist rows with user settings ---
        joined = await crud.list_all_wishlist_with_users()
        check("list_all_wishlist_with_users returns all rows", len(joined) == 3)
        er_user111 = next(j for j in joined if j["appid"] == 1245620 and j["user_id"] == 111)
        check("JOIN carries region_cc + currency_code",
              er_user111["region_cc"] == "TR" and er_user111["currency_code"] == 17)
        er_user222 = next(j for j in joined if j["user_id"] == 222)
        check("JOIN reflects user 222's default region",
              er_user222["region_cc"] == "US" and er_user222["currency_code"] == 1)

        # --- remove: snapshot exists first, to prove FK-safe cleanup ---
        removed = await crud.remove_wishlist_item(111, 1245620)
        check("remove_wishlist_item returns True when removed", removed is True)

        snap_after = await crud.get_snapshot(wl["id"])
        check("remove_wishlist_item cleans up the snapshot row", snap_after is None)

        items_after = await crud.list_wishlist_for_user(111)
        check("remove_wishlist_item removes the wishlist row",
              [i["appid"] for i in items_after] == [1086940])

        # --- remove: idempotent (returns False) ---
        removed_again = await crud.remove_wishlist_item(111, 1245620)
        check("remove_wishlist_item returns False when not present", removed_again is False)

        # --- remove of a game that has a snapshot but no FK error ---
        await crud.add_wishlist_item(111, 1245620, "Elden Ring")
        items2 = await crud.list_wishlist_for_user(111)
        wl2 = next(i for i in items2 if i["appid"] == 1245620)
        await crud.upsert_snapshot(wl2["id"], 1000, 0)
        removed_fk = await crud.remove_wishlist_item(111, 1245620)
        check("remove with an existing snapshot does not raise FK error", removed_fk is True)

    finally:
        await database.close_db()

    print("\nAll database checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # unexpected error
        print(f"\nUnexpected error: {exc!r}", file=sys.stderr)
        sys.exit(1)
