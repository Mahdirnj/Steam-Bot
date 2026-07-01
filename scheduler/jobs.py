"""PTB JobQueue task: check_all_wishlists() — polls wishlists for price changes
every 6h, notifies on any change, throttled to respect Steam rate limits.

Implemented in build step 11. See PROJECT.md §8 (background job), §10 step 11.

Flow per wishlist item:
    1. Fetch appdetails(appid, cc=user's region).
    2. Compare final_cents + discount_pct against stored snapshot.
    3. If different → send Telegram notification, update snapshot.
    4. If no snapshot yet (first check) → create silently, no notification.
    5. On API failure → log and skip, continue with remaining items.
"""
import asyncio

from loguru import logger
from telegram.ext import ContextTypes

from db import crud
from services import steam
from services.notifier import has_price_changed, send_price_notification


# Delay (seconds) between Steam API calls to avoid hammering.
_THROTTLE_DELAY: float = 0.5


async def check_all_wishlists(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Poll every wishlisted game for price changes and notify users.

    Registered as a repeating job in main.py via:
        app.job_queue.run_repeating(check_all_wishlists, interval=21600, first=60)

    PROJECT.md §8: For every wishlist row joined with its user's region_cc:
    - Call appdetails(appid, cc=region_cc).
    - Compare final cents and discount_percent against price_snapshots.
    - If different → notify + update snapshot.
    - If no snapshot → create silently (don't notify on first check).
    - Throttle requests (asyncio.sleep) to respect Steam rate limits.
    """
    bot = context.bot
    rows = await crud.list_all_wishlist_with_users()

    if not rows:
        logger.debug("check_all_wishlists: no wishlist items found, skipping.")
        return

    logger.info("check_all_wishlists: checking {} wishlist items...", len(rows))

    checked = 0
    notified = 0
    errors = 0
    first_checks = 0

    for row in rows:
        wishlist_id = row["wishlist_id"]
        user_id = row["user_id"]
        appid = row["appid"]
        game_name = row["game_name"]
        cc = row["region_cc"]

        try:
            # Fetch current price from Steam.
            data = await steam.appdetails(appid, cc)

            if data is None:
                logger.warning("check_all_wishlists: appdetails returned None for appid={}", appid)
                errors += 1
                await asyncio.sleep(_THROTTLE_DELAY)
                continue

            is_free = data.get("is_free", False)
            price_overview = data.get("price_overview")

            if is_free:
                new_final = 0
                new_pct = 0
            elif price_overview is not None:
                new_final = price_overview.get("final", 0)
                new_pct = price_overview.get("discount_percent", 0)
            else:
                # Not available in this region — store as unavailable.
                new_final = None
                new_pct = 0

            # Get existing snapshot (if any).
            snapshot = await crud.get_snapshot(wishlist_id)

            if snapshot is None:
                # First check — create snapshot silently, don't notify.
                await crud.upsert_snapshot(
                    wishlist_id,
                    last_final_cents=new_final if new_final is not None else 0,
                    last_discount_pct=new_pct,
                )
                first_checks += 1
                logger.debug(
                    "check_all_wishlists: first snapshot for appid={} ({}), no notification.",
                    appid, game_name,
                )
            else:
                old_final = snapshot["last_final_cents"]
                old_pct = snapshot["last_discount_pct"]

                if has_price_changed(old_final, old_pct, new_final, new_pct):
                    # Price changed — notify and update.
                    await send_price_notification(
                        bot=bot,
                        user_id=user_id,
                        game_name=game_name,
                        appid=appid,
                        old_final_cents=old_final,
                        old_discount_pct=old_pct,
                        new_final_cents=new_final,
                        new_discount_pct=new_pct,
                    )
                    await crud.upsert_snapshot(
                        wishlist_id,
                        last_final_cents=new_final if new_final is not None else 0,
                        last_discount_pct=new_pct,
                    )
                    notified += 1
                    logger.info(
                        "check_all_wishlists: price changed for appid={} ({}), user notified.",
                        appid, game_name,
                    )
                else:
                    # No change — just update the checked_at timestamp.
                    await crud.upsert_snapshot(
                        wishlist_id,
                        last_final_cents=old_final,
                        last_discount_pct=old_pct,
                    )

            checked += 1

        except Exception as exc:
            errors += 1
            logger.warning(
                "check_all_wishlists: error checking appid={} ({}): {!r}",
                appid, game_name, exc,
            )

        # Throttle to avoid hammering Steam's API.
        await asyncio.sleep(_THROTTLE_DELAY)

    logger.info(
        "check_all_wishlists: done. checked={}, notified={}, first_checks={}, errors={}",
        checked, notified, first_checks, errors,
    )
