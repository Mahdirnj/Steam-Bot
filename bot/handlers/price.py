"""Handler module: /price — game name search → region price → result card.

Implemented in build step 7. See PROJECT.md §8 (Flow: /price).

Entry points:
    1. /price          — prompts user for a game name
    2. /price <name>   — searches immediately
    3. /price <url>    — extracts appid from Steam URL and shows result card
    4. menu:price      — same as #1 (from main menu button)
    5. Text input      — when user_data["awaiting"] == "price_search"

Flow:
    storesearch → show results as buttons → user picks → appdetails → result card
    OR: Steam URL → extract appid → appdetails → result card
    Result card buttons: Add to Wishlist, Compare Regions, Show DLCs, Steam Page
"""
import asyncio

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.utils import extract_appid_from_text

from bot.keyboards import (
    result_card_keyboard,
    search_results_keyboard,
)
from bot.messages import (
    PRICE_ASK_NAME,
    PRICE_ERROR,
    PRICE_INFO_DEVELOPER,
    PRICE_INFO_DIVIDER,
    PRICE_INFO_GENRES,
    PRICE_INFO_LAST_UPDATED,
    PRICE_INFO_METACRITIC,
    PRICE_INFO_PLATFORMS,
    PRICE_INFO_RELEASE,
    PRICE_INFO_REVIEWS,
    PRICE_NO_RESULTS,
    PRICE_RESULT_CARD,
    PRICE_RESULT_DISCOUNT_EXTRA,
    PRICE_RESULT_FREE,
    PRICE_RESULT_KEYS,
    PRICE_RESULT_NOT_PURCHASABLE,
    PRICE_RESULT_PRICE,
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
    """Handle /price — accept an inline argument, Steam URL, or prompt for a game name.

    If the user provides a name after the command (e.g. `/price elden ring`),
    search immediately. If they provide a Steam URL, extract the appid and
    show the result card directly. Otherwise, ask them to send a name.
    """
    if update.message is None:
        return

    # PTB splits the command args: "/price elden ring" → args = ["elden", "ring"].
    args = context.args or []
    term = " ".join(args).strip()

    if term:
        # Check if it's a Steam URL first
        appid = extract_appid_from_text(term)
        if appid is not None:
            await _show_url_result(update, context, appid)
            return

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

    # Check if it's a Steam URL
    appid = extract_appid_from_text(text)
    if appid is not None:
        await _show_url_result(update, context, appid)
        return True

    await _do_search(update, context, text)
    return True


# ─── Steam URL → result card ─────────────────────────────────────────────────


async def _show_url_result(
    update: Update, context: ContextTypes.DEFAULT_TYPE, appid: int
) -> None:
    """Handle a Steam URL — fetch appdetails and show the result card directly.

    Used when the user pastes a Steam URL as a /price argument or as text input.
    """
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    logger.info("price URL appid={} user={} cc={}", appid, user.id, cc)

    data = await steam.appdetails(appid, cc)
    if data is None:
        await update.message.reply_text(PRICE_ERROR, parse_mode="HTML")
        return

    game_name = data.get("name", "Unknown")
    if context.user_data is not None:
        context.user_data["last_price_appid"] = appid
        context.user_data["last_price_name"] = game_name

    text, kb, header_image = await _build_result_card(data, cc, db_user["currency_code"])

    try:
        await context.bot.send_photo(
            chat_id=user.id,
            photo=header_image,
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("price_url send_photo failed: {!r}", exc)
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )


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
    """Handle price:appid:<id> callback — fetch details and render result card.

    Deletes the search-results text message and sends a new photo message
    with the game's header art as the image and the price card as the caption.
    """
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

    text, kb, header_image = await _build_result_card(data, cc, db_user["currency_code"])

    try:
        # Delete the search-results text message.
        await query.message.delete()  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug("price_select delete old message: {!r}", exc)

    try:
        # Send a photo message with the game cover art + price card caption.
        await context.bot.send_photo(
            chat_id=user.id,
            photo=header_image,
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("price_select send_photo failed: {!r}", exc)
        # Fallback: send as text if photo fails.
        await context.bot.send_message(
            chat_id=user.id,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
        )


# ─── "Back to Game" button ───────────────────────────────────────────────────


async def price_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back:<appid> callback — re-fetch and re-render the result card.

    Since the result card is now a photo message, we edit the caption instead
    of the message text.
    """
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
        try:
            await query.edit_message_caption(
                caption=PRICE_ERROR,
                parse_mode="HTML",
            )
        except Exception:
            await query.edit_message_text(PRICE_ERROR, parse_mode="HTML")
        return

    text, kb, _ = await _build_result_card(data, cc, db_user["currency_code"])
    try:
        await query.edit_message_caption(
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("price_back edit_message_caption failed: {!r}", exc)


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
        await query.answer()
        try:
            await query.edit_message_caption(
                caption="\u2705 <b>{}</b> has been added to your wishlist!\n\n"
                "You\u2019ll be notified when the price changes.\n\n"
                "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
                parse_mode="HTML",
                reply_markup=_back_to_game_keyboard(appid),
            )
        except Exception as exc:
            logger.warning("price_add_wishlist edit_message_caption failed: {!r}", exc)
    else:
        await query.answer()
        try:
            await query.edit_message_caption(
                caption="\u2139\ufe0f <b>{}</b> is already in your wishlist.\n\n"
                "\U0001f4cb Use /wishlist to see all your tracked games.".format(game_name),
                parse_mode="HTML",
                reply_markup=_back_to_game_keyboard(appid),
            )
        except Exception as exc:
            logger.warning("price_add_wishlist edit_message_caption failed: {!r}", exc)


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
        await query.edit_message_caption(
            caption="🌍 Fetching prices across regions...",
            parse_mode="HTML",
        )
    except Exception:
        pass  # message may already be identical

    user = update.effective_user
    if user is None:
        return

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
        await query.edit_message_caption(
            caption="\n".join(lines), parse_mode="HTML", reply_markup=back_kb
        )
    except Exception as exc:
        logger.warning("price_compare edit_message_caption failed: {!r}", exc)


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
        await query.edit_message_caption(
            caption="📦 Fetching DLC info...",
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
        try:
            await query.edit_message_caption(caption=PRICE_ERROR, parse_mode="HTML")
        except Exception:
            pass
        return

    dlc_ids: list[int] = parent.get("dlc", [])
    if not dlc_ids:
        back_kb = _back_to_game_keyboard(appid)
        try:
            await query.edit_message_caption(
                caption="📦 <b>No DLCs found</b> for this game.",
                parse_mode="HTML",
                reply_markup=back_kb,
            )
        except Exception:
            pass
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
        await query.edit_message_caption(
            caption="\n".join(lines), parse_mode="HTML", reply_markup=back_kb
        )
    except Exception as exc:
        logger.warning("price_dlc edit_message_caption failed: {!r}", exc)


# ─── Result card builder ─────────────────────────────────────────────────────


async def _build_result_card(
    data: dict, cc: str, currency_code: int
) -> tuple[str, InlineKeyboardMarkup, str]:
    """Render the result card text, keyboard, and header image URL from appdetails data.

    Returns (text, keyboard, header_image_url) so the caller can send a photo
    message with the game cover art.
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

    # ── Game info section ──
    info_parts = []

    # Developer/Publisher
    developers = data.get("developers", [])
    if developers:
        info_parts.append(PRICE_INFO_DEVELOPER.format(developer=", ".join(developers[:2])))

    # Genres
    genres = data.get("genres", [])
    if genres:
        genre_names = [g.get("description", "") for g in genres[:3]]
        genre_names = [g for g in genre_names if g]
        if genre_names:
            info_parts.append(PRICE_INFO_GENRES.format(genres=" · ".join(genre_names)))

    # Release date
    release_date = data.get("release_date", {})
    if release_date:
        date_str = release_date.get("date", "")
        coming_soon = release_date.get("coming_soon", False)
        if date_str:
            if coming_soon:
                info_parts.append(PRICE_INFO_RELEASE.format(release_date=f"Coming Soon ({date_str})"))
            else:
                info_parts.append(PRICE_INFO_RELEASE.format(release_date=date_str))

    # Last update date - use the last_update field from appdetails if available
    # Some games have this field, others might have it in different formats
    last_update = data.get("last_update")
    if last_update:
        info_parts.append(PRICE_INFO_LAST_UPDATED.format(update_date=last_update))

    # Metacritic score
    metacritic = data.get("metacritic", {})
    if metacritic:
        score = metacritic.get("score")
        if score is not None:
            info_parts.append(PRICE_INFO_METACRITIC.format(score=score))

    # User reviews (fetch from reviews endpoint) - only show the summary line
    reviews = await steam.get_reviews(appid)
    if reviews and reviews.get("total_reviews", 0) > 0:
        review_score = reviews.get("review_score", "No reviews")
        review_pct = reviews.get("review_percentage", 0)

        # Format review line with expression
        info_parts.append(PRICE_INFO_REVIEWS.format(
            score=review_score,
            percentage=review_pct
        ))

    # Platforms
    platforms = data.get("platforms", {})
    if platforms:
        platform_list = []
        if platforms.get("windows"):
            platform_list.append("Windows")
        if platforms.get("mac"):
            platform_list.append("Mac")
        if platforms.get("linux"):
            platform_list.append("Linux")
        if platform_list:
            info_parts.append(PRICE_INFO_PLATFORMS.format(platforms=", ".join(platform_list)))

    # Build info line with divider
    if info_parts:
        info_line = "\n" + PRICE_INFO_DIVIDER + "\n" + "\n".join(info_parts)
    else:
        info_line = ""

    text = PRICE_RESULT_CARD.format(
        name=name, price_line=price_line, keys_line=keys_line, info_line=info_line
    )

    steam_url = f"https://store.steampowered.com/app/{appid}"
    kb = result_card_keyboard(appid, steam_url)

    # Grab the header image URL from appdetails (game cover art).
    header_image = data.get("header_image", "")

    return text, kb, header_image


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
