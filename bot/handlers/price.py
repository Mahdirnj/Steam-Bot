"""Handler module: /price — game name search → region price → result card.

Implemented in build step 7. See PROJECT.md §8 (Flow: /price).

Entry points:
    1. /price          — prompts user for a game name
    2. /price <name>   — searches immediately
    3. menu:price      — same as #1 (from main menu button)
    4. Text input      — when user_data["awaiting"] == "price_search"

Flow:
    storesearch → show results as buttons → user picks → appdetails → result card
    Result card buttons: Add to Wishlist, Compare Regions, Show DLCs, Steam Page
"""
import asyncio

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    BACK_TO_MENU_KEYBOARD,
    result_card_keyboard,
    search_results_keyboard,
)
from bot.messages import (
    GENERIC_ERROR,
    PRICE_ASK_NAME,
    PRICE_ERROR,
    PRICE_NO_RESULTS,
    PRICE_RESULT_CARD,
    PRICE_RESULT_DISCOUNT_EXTRA,
    PRICE_RESULT_FREE,
    PRICE_RESULT_KEYS,
    PRICE_RESULT_NOT_PURCHASABLE,
    PRICE_RESULT_PRICE,
    WISHLIST_ADDED,
    WISHLIST_ALREADY_EXISTS,
)
from db import crud
from services import steam, tf2_market

# Maximum number of DLCs to fetch and display.
_MAX_DLCS = 10

# Regions used in the "Compare Regions" feature.
_COMPARE_REGIONS = [
    ("US", "🇺🇸 US"),
    ("TR", "🇹🇷 TR"),
    ("UA", "🇺🇦 UA"),
    ("AR", "🇦🇷 AR"),
    ("CN", "🇨🇳 CN"),
]


# ─── /price command ──────────────────────────────────────────────────────────


async def price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /price — accept an inline argument or prompt for a game name.

    If the user provides a name after the command (e.g. `/price elden ring`),
    search immediately. Otherwise, ask them to send a name.
    """
    if update.message is None:
        return

    # PTB splits the command args: "/price elden ring" → args = ["elden", "ring"].
    args = context.args or []
    term = " ".join(args).strip()

    if term:
        # User provided a name — search immediately.
        await _do_search(update, context, term)
    else:
        # No name — prompt and wait for text input.
        if context.user_data is not None:
            context.user_data["awaiting"] = "price_search"
        await update.message.reply_text(PRICE_ASK_NAME, parse_mode="HTML")


# ─── Text input handler (called from main.py's dispatcher) ───────────────────


async def handle_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input when the user is in the price-search awaiting state.

    Returns True if the message was consumed, False otherwise.
    """
    if context.user_data is None or context.user_data.get("awaiting") != "price_search":
        return False

    # Consume the awaiting state regardless of outcome.
    context.user_data.pop("awaiting", None)

    raw_text = update.message.text if update.message else None
    text = raw_text.strip() if raw_text else ""
    if not text:
        return False

    await _do_search(update, context, text)
    return True


# ─── Search + show results ───────────────────────────────────────────────────


async def _do_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, term: str
) -> None:
    """Run storesearch and reply with result buttons (or an error/no-results msg).

    Also used by the /wishlist add flow (step 10) — keep this function
    focused on search + display, no side effects beyond replying.
    """
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    # Resolve the user's saved region (default US if first interaction).
    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    logger.info("price search term={!r} user={} cc={}", term, user.id, cc)

    results = await steam.storesearch(term, cc)
    if not results:
        await update.message.reply_text(
            PRICE_NO_RESULTS.format(term=term),
            parse_mode="HTML",
        )
        return

    kb = search_results_keyboard(results)
    await update.message.reply_text(
        f"🔍 Results for <b>{term}</b> — tap a game:",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─── Game selected from search results ───────────────────────────────────────


async def price_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle price:appid:<id> callback — fetch details and render result card."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    appid = _extract_appid("price:appid:", query.data)
    if appid is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    data = await steam.appdetails(appid, cc)
    if data is None:
        await query.edit_message_text(PRICE_ERROR, parse_mode="HTML")
        return

    # Remember the game name for wishlist-add and back-button flows.
    game_name = data.get("name", "Unknown")
    if context.user_data is not None:
        context.user_data["last_price_appid"] = appid
        context.user_data["last_price_name"] = game_name

    text, kb = await _build_result_card(data, cc, db_user["currency_code"])
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("price_select edit_message failed: {!r}", exc)


# ─── "Back to Game" button ───────────────────────────────────────────────────


async def price_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back:<appid> callback — re-fetch and re-render the result card."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    appid = _extract_appid("back:", query.data)
    if appid is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    data = await steam.appdetails(appid, cc)
    if data is None:
        await query.edit_message_text(PRICE_ERROR, parse_mode="HTML")
        return

    text, kb = await _build_result_card(data, cc, db_user["currency_code"])
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("price_back edit_message failed: {!r}", exc)


# ─── "Add to Wishlist" button ────────────────────────────────────────────────


async def price_add_wishlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wish:add:<appid> callback — add the game to the user's wishlist."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    appid = _extract_appid("wish:add:", query.data)
    if appid is None:
        return

    user = update.effective_user
    if user is None:
        return

    # Use the name we remembered when showing the result card.
    game_name = context.user_data.get("last_price_name", "Unknown") if context.user_data else "Unknown"

    added = await crud.add_wishlist_item(user.id, appid, game_name)
    if added:
        logger.info("Wishlist add: user={} appid={} name={!r}", user.id, appid, game_name)
        await query.answer(
            WISHLIST_ADDED.format(name=game_name),
            show_alert=True,
        )
    else:
        await query.answer(
            WISHLIST_ALREADY_EXISTS.format(name=game_name),
            show_alert=True,
        )


# ─── "Compare Regions" button ────────────────────────────────────────────────


async def price_compare_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle compare:<appid> — fetch price in multiple regions and show a table.

    Edits the message to show a "Fetching…" indicator first, then replaces
    it with the comparison results once all API calls complete.
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    appid = _extract_appid("compare:", query.data)
    if appid is None:
        return

    # Show a loading indicator immediately — no buttons while fetching.
    await query.answer()
    try:
        await query.edit_message_text(
            "🌍 Fetching prices across regions…",
            parse_mode="HTML",
        )
    except Exception:
        pass  # message may already be identical

    lines: list[str] = ["🌍 <b>Region Price Comparison</b>\n"]
    for cc, label in _COMPARE_REGIONS:
        data = await steam.appdetails(appid, cc)
        if data is None or data.get("price_overview") is None:
            if data is not None and data.get("is_free"):
                lines.append(f"  {label}: 🆓 Free to Play")
            else:
                lines.append(f"  {label}: ⚠️ N/A")
        else:
            po = data["price_overview"]
            final = po.get("final_formatted", "?")
            discount = po.get("discount_percent", 0)
            if discount > 0:
                initial = po.get("initial_formatted", "")
                if initial:
                    lines.append(
                        f"  {label}: <b>{final}</b>  <s>{initial}</s>  (−{discount}%)"
                    )
                else:
                    lines.append(f"  {label}: <b>{final}</b>  (−{discount}%)")
            else:
                lines.append(f"  {label}: <b>{final}</b>")

        # Small delay between API calls to be polite to Steam.
        await asyncio.sleep(0.3)

    lines.append("")
    lines.append("\u2139\ufe0f Prices are in each region's local currency.")

    back_kb = _back_to_game_keyboard(appid)
    try:
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=back_kb
        )
    except Exception as exc:
        logger.warning("price_compare edit_message failed: {!r}", exc)


# ─── "Show DLCs" button ─────────────────────────────────────────────────────


async def price_dlc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle dlc:<appid> — fetch and display the game's DLC list.

    Edits the message to show a "Fetching…" indicator first, then replaces
    it with the DLC list once all API calls complete.
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    appid = _extract_appid("dlc:", query.data)
    if appid is None:
        return

    # Show a loading indicator immediately — no buttons while fetching.
    await query.answer()
    try:
        await query.edit_message_text(
            "📦 Fetching DLC info…",
            parse_mode="HTML",
        )
    except Exception:
        pass

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    # We need the parent game's data to get the DLC list.
    parent = await steam.appdetails(appid, cc)
    if parent is None:
        await query.edit_message_text(PRICE_ERROR, parse_mode="HTML")
        return

    dlc_ids: list[int] = parent.get("dlc", [])
    if not dlc_ids:
        back_kb = _back_to_game_keyboard(appid)
        await query.edit_message_text(
            "📦 <b>No DLCs found</b> for this game.",
            parse_mode="HTML",
            reply_markup=back_kb,
        )
        return

    dlc_ids = dlc_ids[:_MAX_DLCS]
    lines: list[str] = [f"📦 <b>DLCs</b> (showing {len(dlc_ids)}):\n"]

    for dlc_id in dlc_ids:
        dlc_data = await steam.appdetails(dlc_id, cc)
        if dlc_data is None:
            lines.append(f"  • AppID {dlc_id} — ⚠️ unavailable")
        else:
            name = dlc_data.get("name", f"AppID {dlc_id}")
            po = dlc_data.get("price_overview")
            if dlc_data.get("is_free"):
                lines.append(f"  • <b>{name}</b> — 🆓 Free")
            elif po is not None:
                final = po.get("final_formatted", "?")
                discount = po.get("discount_percent", 0)
                if discount > 0:
                    lines.append(f"  • <b>{name}</b> — {final}  (−{discount}%)")
                else:
                    lines.append(f"  • <b>{name}</b> — {final}")
            else:
                lines.append(f"  • <b>{name}</b> — ⚠️ Not purchasable")

        await asyncio.sleep(0.3)

    total_dlc = len(parent.get("dlc", []))
    if total_dlc > _MAX_DLCS:
        lines.append(f"\n…and {total_dlc - _MAX_DLCS} more.")

    back_kb = _back_to_game_keyboard(appid)
    try:
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=back_kb
        )
    except Exception as exc:
        logger.warning("price_dlc edit_message failed: {!r}", exc)


# ─── Result card builder ─────────────────────────────────────────────────────


async def _build_result_card(
    data: dict, cc: str, currency_code: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the result card text and keyboard from appdetails data.

    Returns (text, keyboard) so the caller can use reply_text or edit_message_text.
    """
    name = data.get("name", "Unknown")
    appid = data.get("steam_appid", 0)

    # ── Price line ──
    is_free = data.get("is_free", False)
    price_overview = data.get("price_overview")

    if is_free:
        price_line = PRICE_RESULT_FREE
        keys_line = ""
    elif price_overview is not None:
        final_fmt = price_overview.get("final_formatted", "?")
        discount_pct = price_overview.get("discount_percent", 0)

        if discount_pct > 0:
            initial_fmt = price_overview.get("initial_formatted", "")
            # Steam may return empty initial_formatted even with a discount —
            # fall back to final_formatted so the card doesn't show "(was , −20%)".
            if not initial_fmt:
                initial_fmt = final_fmt
            discount_extra = PRICE_RESULT_DISCOUNT_EXTRA.format(
                initial=initial_fmt, pct=discount_pct
            )
        else:
            discount_extra = ""

        price_line = PRICE_RESULT_PRICE.format(
            final=final_fmt, discount_extra=discount_extra
        )

        # ── Keys/tickets needed ──
        # Game price is in cents; key/ticket price is a float in the same currency.
        game_price = price_overview.get("final", 0) / 100.0
        key_raw = await tf2_market.get_key_price(currency_code)
        ticket_raw = await tf2_market.get_ticket_price(currency_code)

        if key_raw is not None and key_raw > 0:
            key_net = tf2_market.net_proceeds(key_raw)
            ticket_net = (
                tf2_market.net_proceeds(ticket_raw)
                if ticket_raw is not None and ticket_raw > 0
                else 0
            )

            try:
                k_needed = tf2_market.keys_needed(game_price, key_net)
                t_needed = (
                    tf2_market.keys_needed(game_price, ticket_net)
                    if ticket_net > 0
                    else 0
                )
                keys_line = PRICE_RESULT_KEYS.format(
                    keys=f"{k_needed:.2f}", tickets=f"{t_needed:.2f}"
                )
            except ValueError:
                # key_net_price <= 0 — shouldn't happen but guard anyway.
                keys_line = ""
        else:
            # TF2 prices unavailable — skip the keys line gracefully.
            keys_line = ""
    else:
        price_line = PRICE_RESULT_NOT_PURCHASABLE
        keys_line = ""

    text = PRICE_RESULT_CARD.format(
        name=name, price_line=price_line, keys_line=keys_line
    )

    steam_url = f"https://store.steampowered.com/app/{appid}"
    kb = result_card_keyboard(appid, steam_url)
    return text, kb


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _extract_appid(prefix: str, data: str) -> int | None:
    """Extract an integer appid from callback_data like 'price:appid:123456'."""
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid appid in callback_data: {!r}", data)
        return None


def _back_to_game_keyboard(appid: int) -> InlineKeyboardMarkup:
    """Build a keyboard with a single 'Back to Game' button."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Game", callback_data=f"back:{appid}")]]
    )
