"""Inline mode handler: allows users to search Steam prices from any chat.

Users type @botname <game name> in any chat and get price cards as inline results.
Tapping a result sends a formatted price card to that chat.

BotFather setup required: /setinline → set placeholder text to "Search for a Steam game…"
"""
from loguru import logger
from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    LinkPreviewOptions,
    Update,
)
from telegram.ext import ContextTypes

from bot.keyboards import inline_price_keyboard
from bot.messages import INLINE_PRICE_CARD
from db import crud
from services import steam, tf2_market

# Maximum number of inline results to return.
_MAX_INLINE_RESULTS = 5


def _build_inline_description(data: dict | None) -> str:
    """Build a short description line for the inline result card."""
    if data is None:
        return "Price unavailable"

    is_free = data.get("is_free", False)
    if is_free:
        return "Free to Play"

    po = data.get("price_overview")
    if po is None:
        return "Not purchasable in this region"

    final = po.get("final_formatted", "?")
    pct = po.get("discount_percent", 0)
    if pct > 0:
        return f"{final} (−{pct}%)"
    return final


async def _build_inline_price_card(name: str, data: dict | None, currency_code: int = 1) -> str:
    """Build the full price card message text sent when a user taps an inline result."""
    if data is None:
        return INLINE_PRICE_CARD.format(name=name, price_line="⚠️ Price unavailable", keys_line="")

    is_free = data.get("is_free", False)
    if is_free:
        return INLINE_PRICE_CARD.format(name=name, price_line="🆓 <b>Free to Play</b>", keys_line="")

    po = data.get("price_overview")
    if po is None:
        return INLINE_PRICE_CARD.format(name=name, price_line="⚠️ Not purchasable in this region", keys_line="")

    final_fmt = po.get("final_formatted", "?")
    pct = po.get("discount_percent", 0)
    game_price = po.get("final", 0) / 100.0

    if pct > 0:
        initial_fmt = po.get("initial_formatted", "")
        if not initial_fmt:
            initial_fmt = final_fmt
        price_line = f"💰 Price: <b>{final_fmt}</b>  (was {initial_fmt}, <b>−{pct}%</b>)"
    else:
        price_line = f"💰 Price: <b>{final_fmt}</b>"

    # Compute TF2 key/ticket equivalents.
    keys_line = ""
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
            t_needed = tf2_market.keys_needed(game_price, ticket_net) if ticket_net > 0 else 0
            keys_line = f"🔑 Keys needed: <b>~{k_needed:.2f}</b>  (≈{t_needed:.2f} tickets)\n"
        except ValueError:
            pass

    return INLINE_PRICE_CARD.format(name=name, price_line=price_line, keys_line=keys_line)


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries — search Steam and return price cards."""
    query = update.inline_query
    if query is None:
        return

    search_term = query.query.strip()
    if not search_term:
        await query.answer([], cache_time=300, is_personal=True)
        return

    # Look up user's saved region from DB.
    db_user = await crud.get_or_create_user(query.from_user.id)
    region_cc = db_user["region_cc"]
    currency_code = db_user["currency_code"]

    # Search Steam using user's region.
    results = await steam.storesearch(search_term, region_cc)

    # Build inline results.
    inline_results = []
    for r in results[:_MAX_INLINE_RESULTS]:
        appid = r["appid"]
        name = r["name"]

        # Fetch price details in user's region.
        data = await steam.appdetails(appid, region_cc)

        description = _build_inline_description(data)
        thumbnail = data.get("header_image", "") if data else ""
        steam_url = f"https://store.steampowered.com/app/{appid}"

        # Build the message text (sent when user taps the result).
        # Note: _build_inline_price_card is async because it calls tf2_market.
        message_text = await _build_inline_price_card(name, data, currency_code)

        article = InlineQueryResultArticle(
            id=str(appid),
            title=name,
            description=description,
            thumbnail_url=thumbnail if thumbnail else None,
            input_message_content=InputTextMessageContent(
                message_text=message_text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ),
            reply_markup=inline_price_keyboard(appid, steam_url),
        )
        inline_results.append(article)

    await query.answer(
        inline_results,
        cache_time=300,
        is_personal=True,
    )
