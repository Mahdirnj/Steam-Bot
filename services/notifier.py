"""Wishlist notifier: diff logic + sends Telegram messages on price change.

Implemented in build step 11 (scheduler). See PROJECT.md §7, §8 (background job).

Two responsibilities:
    1. Compare current price data against a stored snapshot.
    2. Send a Telegram notification to the user when a change is detected.
"""
from loguru import logger
from telegram import Bot
from telegram.constants import ParseMode


def has_price_changed(
    old_final_cents: int | None,
    old_discount_pct: int | None,
    new_final_cents: int | None,
    new_discount_pct: int | None,
) -> bool:
    """Return True if the price or discount has changed between snapshots.

    Handles the case where a game becomes free (new_final_cents is None)
    or unavailable (new_final_cents is None when it previously had a price).
    """
    if old_final_cents != new_final_cents:
        return True
    if old_discount_pct != new_discount_pct:
        return True
    return False


def format_price(cents: int | None, currency_symbol: str = "") -> str:
    """Format cents into a display string like '$29.99' or 'Free'."""
    if cents is None or cents == 0:
        return "Free"
    amount = cents / 100
    if currency_symbol:
        return f"{currency_symbol}{amount:,.2f}"
    return f"{amount:,.2f}"


def build_change_message(
    game_name: str,
    appid: int,
    old_final_cents: int,
    old_discount_pct: int,
    new_final_cents: int | None,
    new_discount_pct: int,
) -> str:
    """Build a user-facing notification message for a price change.

    Shows direction emoji (i/ii/ii), old vs new price, and discount info.
    """
    old_price = format_price(old_final_cents)
    new_price = format_price(new_final_cents)

    # Determine direction
    if new_final_cents is None:
        emoji = "\u26a0\ufe0f"
        direction = "is no longer available"
    elif new_final_cents < old_final_cents:
        emoji = "\U0001f4c9"
        direction = "price dropped"
    elif new_final_cents > old_final_cents:
        emoji = "\U0001f4c8"
        direction = "price increased"
    else:
        emoji = "\u27a1\ufe0f"
        direction = "discount changed"

    lines = [
        f"{emoji} <b>Price Alert: {game_name}</b>",
        "",
        f"  {direction.title()}!",
    ]

    # Show price change
    if new_final_cents is not None:
        lines.append(f"  Price: <s>{old_price}</s> \u2192 <b>{new_price}</b>")
    else:
        lines.append(f"  Price: {old_price} \u2192 <b>Unavailable</b>")

    # Show discount change
    if old_discount_pct != new_discount_pct:
        old_pct = f"-{old_discount_pct}%" if old_discount_pct > 0 else "No discount"
        new_pct = f"-{new_discount_pct}%" if new_discount_pct > 0 else "No discount"
        lines.append(f"  Discount: {old_pct} \u2192 <b>{new_pct}</b>")
    elif new_discount_pct > 0:
        lines.append(f"  Discount: <b>-{new_discount_pct}%</b>")

    lines.append("")
    lines.append(f'\U0001f517 <a href="https://store.steampowered.com/app/{appid}">View on Steam</a>')

    return "\n".join(lines)


async def send_price_notification(
    bot: Bot,
    user_id: int,
    game_name: str,
    appid: int,
    old_final_cents: int,
    old_discount_pct: int,
    new_final_cents: int | None,
    new_discount_pct: int,
) -> None:
    """Send a price change notification to a user via Telegram.

    Catches Forbidden (user blocked the bot) and logs it instead of crashing.
    """
    message = build_change_message(
        game_name=game_name,
        appid=appid,
        old_final_cents=old_final_cents,
        old_discount_pct=old_discount_pct,
        new_final_cents=new_final_cents,
        new_discount_pct=new_discount_pct,
    )

    try:
        await bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info("Price notification sent to user={} for appid={} ({})", user_id, appid, game_name)
    except Exception as exc:
        logger.warning("Failed to send notification to user={}: {!r}", user_id, exc)
