"""Handler module: /tf2 (live key/ticket price, net of commission) and /convert.

Implemented in build step 8. See PROJECT.md §8 (Flow: /tf2, /convert).

/tf2:
    Fetches live key + ticket prices in the user's saved currency via the
    cached tf2_market service. Shows market price and net-after-commission
    (85%) for both items. A "Refresh" button re-fetches (cache handles TTL).

/convert:
    /convert 5 keys      → 5 * key_net_price  = currency value
    /convert 20 tickets  → 20 * ticket_net_price = currency value
    /convert 20          → 20 / key_net_price = number of keys (bare = currency)
"""
import re

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.utils import TypingIndicator

from bot.keyboards import BACK_TO_MENU_KEYBOARD, TF2_KEYBOARD
from bot.messages import (
    CONVERT_ERROR,
    CONVERT_RESULT,
    CONVERT_USAGE,
    TF2_ERROR,
    TF2_TEMPLATE,
)
from db import crud
from services import tf2_market

# Matches: "5 keys", "1 key", "3 tickets", "1 ticket"
_RE_ITEMS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(keys?|tickets?)\s*$", re.IGNORECASE)


# ─── /tf2 command ────────────────────────────────────────────────────────────


async def tf2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tf2 — show live key + ticket prices with net-after-commission."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    async with TypingIndicator(context, user.id):
        db_user = await crud.get_or_create_user(user.id)
        currency_code = db_user["currency_code"]

        text = await _build_tf2_text(currency_code)
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=TF2_KEYBOARD
        )


async def tf2_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle refresh:tf2 callback — re-fetch prices and edit the message."""
    query = update.callback_query
    if query is None:
        return

    user = update.effective_user
    if user is None:
        return

    async with TypingIndicator(context, user.id):
        db_user = await crud.get_or_create_user(user.id)
        currency_code = db_user["currency_code"]

        # Show a loading indicator while fetching.
        await query.answer()
        try:
            await query.edit_message_text(
                "Fetching live TF2 prices\u2026",
                parse_mode="HTML",
            )
        except Exception:
            pass

        text = await _build_tf2_text(currency_code)
        try:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=TF2_KEYBOARD
            )
        except Exception as exc:
            logger.warning("tf2_refresh edit_message failed: {!r}", exc)


# ─── /convert command ────────────────────────────────────────────────────────


async def convert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /convert — convert between keys/tickets and currency.

    Parses the argument:
        /convert 5 keys      → value of 5 keys in user's currency
        /convert 20 tickets  → value of 20 tickets in user's currency
        /convert 20          → how many keys for that currency amount (default: keys)
    """
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    async with TypingIndicator(context, user.id):
        args = context.args or []
        raw = " ".join(args).strip()

        if not raw:
            await update.message.reply_text(
                CONVERT_USAGE, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
            )
            return

        db_user = await crud.get_or_create_user(user.id)
        currency_code = db_user["currency_code"]

        # Try to parse as "<amount> <item>" (e.g. "5 keys", "3 tickets").
        match = _RE_ITEMS.match(raw)
        if match:
            amount = float(match.group(1))
            item_word = match.group(2).lower()

            if item_word.startswith("key"):
                raw_price = await tf2_market.get_key_price(currency_code)
                item_label = "keys" if amount != 1 else "key"
            else:
                raw_price = await tf2_market.get_ticket_price(currency_code)
                item_label = "tickets" if amount != 1 else "ticket"

            if raw_price is None:
                await update.message.reply_text(
                    CONVERT_ERROR, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
                )
                return

            net = tf2_market.net_proceeds(raw_price)
            result = round(amount * net, 2)
            result_str = f"{result:.2f}"

            await update.message.reply_text(
                CONVERT_RESULT.format(amount=f"{amount:g}", item=item_label, result=result_str),
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
            return

        # Otherwise treat as a bare currency amount → divide by key net price.
        try:
            amount = float(raw)
        except ValueError:
            await update.message.reply_text(
                CONVERT_USAGE, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
            )
            return

        if amount <= 0:
            await update.message.reply_text(
                "Amount must be positive.",
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
            return

        key_price = await tf2_market.get_key_price(currency_code)
        if key_price is None:
            await update.message.reply_text(
                CONVERT_ERROR, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
            )
            return

        key_net = tf2_market.net_proceeds(key_price)
        if key_net <= 0:
            await update.message.reply_text(
                CONVERT_ERROR, parse_mode="HTML", reply_markup=BACK_TO_MENU_KEYBOARD
            )
            return

        keys_count = round(amount / key_net, 2)

        await update.message.reply_text(
            CONVERT_RESULT.format(amount=f"{amount:g}", item="currency", result=f"~{keys_count:.2f} keys"),
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _build_tf2_text(currency_code: int) -> str:
    """Fetch TF2 prices and render the template. Returns error text on failure."""
    key_price = await tf2_market.get_key_price(currency_code)
    ticket_price = await tf2_market.get_ticket_price(currency_code)

    if key_price is None and ticket_price is None:
        return TF2_ERROR

    # Key
    if key_price is not None:
        key_net = tf2_market.net_proceeds(key_price)
        key_price_str = f"{key_price:.2f}"
        key_net_str = f"{key_net:.2f}"
    else:
        key_price_str = "unavailable"
        key_net_str = "—"

    # Ticket
    if ticket_price is not None:
        ticket_net = tf2_market.net_proceeds(ticket_price)
        ticket_price_str = f"{ticket_price:.2f}"
        ticket_net_str = f"{ticket_net:.2f}"
    else:
        ticket_price_str = "unavailable"
        ticket_net_str = "—"

    return TF2_TEMPLATE.format(
        key_price=key_price_str,
        key_net=key_net_str,
        ticket_price=ticket_price_str,
        ticket_net=ticket_net_str,
    )
