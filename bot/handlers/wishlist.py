"""Handler module: /wishlist — list / add / remove / summary subcommands.

Implemented in build step 10. See PROJECT.md §8 (Flow: /wishlist).

Entry points:
    1. /wishlist          — list all wishlisted games with prices
    2. /wishlist add      — prompt for game name (or search immediately)
    3. /wishlist add <game> — search immediately
    4. /wishlist remove   — interactive removal picker
    5. /wishlist summary  — only show games on discount
    6. menu:wishlist      — same as #1
    7. wish:remove:<id>   — remove a game (from remove picker)
    8. Text input when user_data["awaiting"] == "wishlist_add"
"""
import asyncio

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils import extract_appid_from_text

from bot.keyboards import (
    BACK_TO_MENU_KEYBOARD,
    wishlist_remove_keyboard,
)
from bot.messages import (
    WISHLIST_ADD_ASK,
    WISHLIST_EMPTY,
    WISHLIST_ERROR,
    WISHLIST_FOOTER,
    WISHLIST_HEADER,
    WISHLIST_ITEM_FREE,
    WISHLIST_ITEM_NORMAL,
    WISHLIST_ITEM_SALE,
    WISHLIST_ITEM_UNAVAILABLE,
    WISHLIST_REFRESHING,
    WISHLIST_SUMMARY_EMPTY,
    WISHLIST_SUMMARY_HEADER,
)
from db import crud
from services import steam

# Maximum number of games to fetch prices for (avoid hammering Steam).
_MAX_WISHLIST_FETCH = 20


# ─── /wishlist command ───────────────────────────────────────────────────────


async def wishlist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /wishlist — dispatch to subcommands or list all wishlisted games.

    Subcommands:
        /wishlist add [game name|url] — add a game
        /wishlist remove              — interactive removal
        /wishlist summary             — only show discounted games
        /wishlist                     — list all
    """
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    args = context.args or []
    if args:
        sub = args[0].lower()
        if sub == "add":
            # /wishlist add elden ring → search immediately
            # /wishlist add <steam url> → add directly
            rest = " ".join(args[1:]).strip()
            if rest:
                # Check if it's a Steam URL
                appid = extract_appid_from_text(rest)
                if appid is not None:
                    await _wishlist_add_from_url(update, context, appid)
                else:
                    await _wishlist_add_search(update, context, rest)
            else:
                # /wishlist add (no name) → prompt
                if context.user_data is not None:
                    context.user_data["awaiting"] = "wishlist_add"
                await update.message.reply_text(
                    WISHLIST_ADD_ASK, parse_mode="HTML"
                )
            return
        elif sub == "remove":
            await wishlist_remove_picker(update, context)
            return
        elif sub == "summary":
            await _wishlist_list(update, context, summary_only=True)
            return

    # No subcommand — list all.
    await _wishlist_list(update, context, summary_only=False)


# ─── Menu callback entry point ───────────────────────────────────────────────


async def wishlist_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu:wishlist — show the full wishlist from the main menu."""
    query = update.callback_query
    if query is None:
        return

    user = update.effective_user
    if user is None:
        return

    # Show loading indicator.
    await query.answer()
    try:
        await query.edit_message_text(
            "📋 Loading your wishlist\u2026",
            parse_mode="HTML",
        )
    except Exception:
        pass

    items = await crud.list_wishlist_for_user(user.id)
    if not items:
        try:
            await query.edit_message_text(
                WISHLIST_EMPTY,
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
        except Exception:
            pass
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    text = await _build_wishlist_text(items, cc, summary_only=False)
    kb = _wishlist_actions_keyboard()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("wishlist_menu edit_message failed: {!r}", exc)


# ─── /wishlist add — search + direct add ─────────────────────────────────────


async def _wishlist_add_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, term: str
) -> None:
    """Search for a game and show results — tapping a result adds directly."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    logger.info("wishlist add search term={!r} user={} cc={}", term, user.id, cc)

    results = await steam.storesearch(term, cc)
    if not results:
        from bot.messages import PRICE_NO_RESULTS

        await update.message.reply_text(
            PRICE_NO_RESULTS.format(term=term),
            parse_mode="HTML",
        )
        return

    # Build keyboard — callbacks use "wish:direct:<appid>" for direct add.
    rows = [
        [InlineKeyboardButton(r["name"], callback_data=f"wish:direct:{r['appid']}:{r['name']}")]
        for r in results
    ]
    rows.append([InlineKeyboardButton("⬅️ Home", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(rows)

    await update.message.reply_text(
        f"🔍 Results for <b>{term}</b> — tap to add to wishlist:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def handle_wishlist_add_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle text input when the user is in the wishlist-add awaiting state.

    Returns True if the message was consumed, False otherwise.
    """
    if context.user_data is None or context.user_data.get("awaiting") != "wishlist_add":
        return False

    context.user_data.pop("awaiting", None)

    raw_text = update.message.text if update.message else None
    text = raw_text.strip() if raw_text else ""
    if not text:
        return False

    # Check if it's a Steam URL — add directly
    appid = extract_appid_from_text(text)
    if appid is not None:
        await _wishlist_add_from_url(update, context, appid)
        return True

    await _wishlist_add_search(update, context, text)
    return True


async def _wishlist_add_from_url(
    update: Update, context: ContextTypes.DEFAULT_TYPE, appid: int
) -> None:
    """Add a game to wishlist directly from a Steam URL."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    # Fetch game details to get the name
    data = await steam.appdetails(appid, cc)
    game_name = data.get("name", "Unknown") if data else "Unknown"

    added = await crud.add_wishlist_item(user.id, appid, game_name)
    if added:
        logger.info("Wishlist add (URL): user={} appid={} name={!r}", user.id, appid, game_name)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 My Wishlist", callback_data="menu:wishlist"),
                    InlineKeyboardButton("⬅️ Home", callback_data="menu:main"),
                ],
            ]
        )
        await update.message.reply_text(
            "\u2705 <b>{}</b> has been added to your wishlist!\n\n"
            "You\u2019ll be notified when the price changes.\n\n"
            "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 My Wishlist", callback_data="menu:wishlist"),
                    InlineKeyboardButton("⬅️ Home", callback_data="menu:main"),
                ],
            ]
        )
        await update.message.reply_text(
            "\u2139\ufe0f <b>{}</b> is already in your wishlist.\n\n"
            "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
            parse_mode="HTML",
            reply_markup=kb,
        )


async def wishlist_direct_add_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle wish:direct:<appid>:<name> — add a game to wishlist directly.

    Game name is embedded in the callback_data (after the 3rd colon).
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    user = update.effective_user
    if user is None:
        return

    parts = query.data.split(":", 3)
    if len(parts) < 3:
        await query.answer("Invalid data.", show_alert=True)
        return

    try:
        appid = int(parts[2])
    except (ValueError, TypeError):
        await query.answer("Invalid appid.", show_alert=True)
        return

    # Game name is everything after the 3rd colon (may contain colons).
    game_name = parts[3] if len(parts) > 3 else "Unknown"

    added = await crud.add_wishlist_item(user.id, appid, game_name)
    if added:
        logger.info("Wishlist add (direct): user={} appid={} name={!r}", user.id, appid, game_name)
        await query.answer()
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 My Wishlist", callback_data="menu:wishlist"),
                    InlineKeyboardButton("⬅️ Home", callback_data="menu:main"),
                ],
            ]
        )
        try:
            await query.edit_message_text(
                "\u2705 <b>{}</b> has been added to your wishlist!\n\n"
                "You\u2019ll be notified when the price changes.\n\n"
                "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("wishlist_direct_add edit_message failed: {!r}", exc)
    else:
        await query.answer()
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 My Wishlist", callback_data="menu:wishlist"),
                    InlineKeyboardButton("⬅️ Home", callback_data="menu:main"),
                ],
            ]
        )
        try:
            await query.edit_message_text(
                "\u2139\ufe0f <b>{}</b> is already in your wishlist.\n\n"
                "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("wishlist_direct_add edit_message failed: {!r}", exc)


# ─── /wishlist remove ────────────────────────────────────────────────────────


async def wishlist_remove_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show an interactive picker — tapping a game removes it."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    items = await crud.list_wishlist_for_user(user.id)
    if not items:
        await update.message.reply_text(
            WISHLIST_EMPTY,
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
        return

    kb = wishlist_remove_keyboard(items)
    await update.message.reply_text(
        "🗑️ Tap a game to remove it from your wishlist:",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def wishlist_remove_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle wish:remove:<appid> — remove a game and refresh the picker."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    user = update.effective_user
    if user is None:
        return

    raw = query.data.split(":", 2)[-1]
    try:
        appid = int(raw)
    except (ValueError, TypeError):
        await query.answer("Invalid data.", show_alert=True)
        return

    # Get the game name before removing.
    items = await crud.list_wishlist_for_user(user.id)
    game_name = next((i["game_name"] for i in items if i["appid"] == appid), "Unknown")

    removed = await crud.remove_wishlist_item(user.id, appid)
    if removed:
        logger.info("Wishlist remove: user={} appid={} name={!r}", user.id, appid, game_name)
        await query.answer(f"🗑️ {game_name} removed from your wishlist.", show_alert=True)
    else:
        await query.answer("Game not found in wishlist.", show_alert=True)

    # Refresh the remove picker with remaining items.
    remaining = await crud.list_wishlist_for_user(user.id)
    if remaining:
        try:
            await query.edit_message_text(
                "🗑️ Tap a game to remove it from your wishlist:",
                parse_mode="HTML",
                reply_markup=wishlist_remove_keyboard(remaining),
            )
        except Exception:
            pass
    else:
        try:
            await query.edit_message_text(
                WISHLIST_EMPTY,
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
        except Exception:
            pass


# ─── List / Summary builder ──────────────────────────────────────────────────


async def _wishlist_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, summary_only: bool
) -> None:
    """Fetch prices and render the wishlist (full list or summary-only)."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    items = await crud.list_wishlist_for_user(user.id)
    if not items:
        await update.message.reply_text(
            WISHLIST_EMPTY,
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
        return

    # Show loading indicator.
    loading_msg = await update.message.reply_text("📋 Loading your wishlist\u2026", parse_mode="HTML")

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    text = await _build_wishlist_text(items, cc, summary_only=summary_only)
    kb = _wishlist_actions_keyboard()

    try:
        await loading_msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("wishlist_list edit_message failed: {!r}", exc)


async def _build_wishlist_text(
    items: list[dict], cc: str, *, summary_only: bool
) -> str:
    """Fetch prices for each wishlisted game and build the display text.

    If summary_only is True, only include games currently on discount.
    Returns a beautifully formatted wishlist with proper spacing and hierarchy.
    """
    sale_items: list[dict] = []
    normal_items: list[dict] = []
    fetch_errors = 0

    capped = items[:_MAX_WISHLIST_FETCH]
    overflow = len(items) - _MAX_WISHLIST_FETCH

    # First pass: fetch all prices and categorize
    for item in capped:
        appid = item["appid"]
        name = item["game_name"]

        data = await steam.appdetails(appid, cc)
        await asyncio.sleep(0.3)  # polite delay between API calls

        item_info = {"name": name, "appid": appid}

        if data is None:
            fetch_errors += 1
            item_info["status"] = "unavailable"
            normal_items.append(item_info)
            continue

        is_free = data.get("is_free", False)
        po = data.get("price_overview")

        if is_free:
            item_info["status"] = "free"
            normal_items.append(item_info)
            continue

        if po is None:
            item_info["status"] = "not_purchasable"
            normal_items.append(item_info)
            continue

        final = po.get("final_formatted", "?")
        pct = po.get("discount_percent", 0)

        if pct > 0:
            initial = po.get("initial_formatted", "")
            if not initial:
                initial = final
            item_info["final"] = final
            item_info["initial"] = initial
            item_info["pct"] = pct
            sale_items.append(item_info)
        else:
            item_info["price"] = final
            normal_items.append(item_info)

    # Second pass: build the display
    lines: list[str] = []

    if summary_only:
        if not sale_items:
            return WISHLIST_SUMMARY_EMPTY
        game_word = "game is" if len(sale_items) == 1 else "games are"
        header = WISHLIST_SUMMARY_HEADER.format(
            count=len(sale_items),
            game_word=game_word
        )
        lines.append(header)

        for i, item in enumerate(sale_items, 1):
            lines.append(WISHLIST_ITEM_SALE.format(
                number=i,
                name=item["name"],
                initial=item["initial"],
                final=item["final"],
                pct=item["pct"]
            ))
    else:
        item_word = "game" if len(items) == 1 else "games"
        header = WISHLIST_HEADER.format(count=len(items), item_word=item_word)
        lines.append(header)

        # Show sale items first (they're more interesting)
        if sale_items:
            lines.append("  🔥 <b>On Sale</b>\n")
            for i, item in enumerate(sale_items, 1):
                lines.append(WISHLIST_ITEM_SALE.format(
                    number=i,
                    name=item["name"],
                    initial=item["initial"],
                    final=item["final"],
                    pct=item["pct"]
                ))
            lines.append("")

        # Then normal items
        if normal_items:
            if sale_items:
                lines.append("  📦 <b>Other Games</b>\n")
            for i, item in enumerate(normal_items, len(sale_items) + 1):
                status = item.get("status")
                if status == "free":
                    lines.append(WISHLIST_ITEM_FREE.format(
                        number=i,
                        name=item["name"]
                    ))
                elif status in ("unavailable", "not_purchasable"):
                    status_text = "Price unavailable" if status == "unavailable" else "Not purchasable"
                    lines.append(WISHLIST_ITEM_UNAVAILABLE.format(
                        number=i,
                        name=item["name"],
                        status=status_text
                    ))
                else:
                    lines.append(WISHLIST_ITEM_NORMAL.format(
                        number=i,
                        name=item["name"],
                        price=item["price"]
                    ))

    # Footer with notes
    notes: list[str] = []
    if overflow > 0:
        notes.append(f"  ⚠️ {overflow} more games not shown")
    if fetch_errors > 0:
        notes.append(f"  ⚠️ {fetch_errors} price(s) could not be fetched")

    # Build final output
    result = "".join(lines)

    if notes:
        result += "\n" + "\n".join(notes)

    result += WISHLIST_FOOTER

    return result


# ─── Wishlist refresh callback ────────────────────────────────────────────────


async def wishlist_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu:wishlist_refresh — re-fetch prices for all wishlisted games and update snapshots."""
    query = update.callback_query
    if query is None:
        return

    user = query.from_user
    if user is None:
        return

    await query.answer()
    try:
        await query.edit_message_text(WISHLIST_REFRESHING, parse_mode="HTML")
    except Exception:
        pass

    items = await crud.list_wishlist_for_user(user.id)
    if not items:
        try:
            await query.edit_message_text(
                WISHLIST_EMPTY, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
            )
        except Exception:
            pass
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    # Fetch fresh prices and update snapshots.
    for item in items[:_MAX_WISHLIST_FETCH]:
        data = await steam.appdetails(item["appid"], cc)
        await asyncio.sleep(0.3)

        if data is None:
            continue

        is_free = data.get("is_free", False)
        po = data.get("price_overview")

        if is_free:
            final_cents = 0
            discount_pct = 0
        elif po is not None:
            final_cents = po.get("final", 0)
            discount_pct = po.get("discount_percent", 0)
        else:
            final_cents = 0
            discount_pct = 0

        await crud.upsert_snapshot(item["id"], final_cents, discount_pct)

    # Rebuild and display the refreshed wishlist.
    text = await _build_wishlist_text(items, cc, summary_only=False)
    kb = _wishlist_actions_keyboard()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("wishlist_refresh edit_message failed: {!r}", exc)


def _wishlist_actions_keyboard():
    """Keyboard with Add / Remove / Refresh / Summary / Home buttons."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh Prices", callback_data="menu:wishlist_refresh"),
                InlineKeyboardButton("🔥 Sale Summary", callback_data="menu:wishlist_summary"),
            ],
            [
                InlineKeyboardButton("➕ Add Game", callback_data="menu:wishlist_add"),
                InlineKeyboardButton("🗑️ Remove Game", callback_data="menu:wishlist_remove"),
            ],
            [
                InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main"),
            ],
        ]
    )
