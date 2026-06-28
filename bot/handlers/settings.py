"""Handler module: /region — pick default region, persist to DB.

Implemented in build step 9. See PROJECT.md §8 (Flow: /region).

Entry points:
    1. /region command — show current region + picker keyboard
    2. menu:region — same as #1 (from main menu button)
    3. region:<cc> callback — region selected from picker (e.g. region:TR)
    4. region:manual callback — user wants to type a custom 2-letter code
    5. Text input — when user_data["awaiting"] == "region_manual"

Flow:
    Show current region → user picks → save cc + currency_code → confirm
"""
from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import BACK_TO_MENU_KEYBOARD, region_picker_keyboard, all_regions_keyboard
from bot.messages import (
    REGION_CHANGED,
    REGION_CURRENT,
    REGION_INVALID,
    REGION_PROMPT_MANUAL,
)
from db import crud
from region_map import CC_TO_CURRENCY, get_currency_code

# Friendly currency names for display (subset — covers all picker regions + extras).
_CURRENCY_NAMES: dict[int, str] = {
    1: "USD",
    2: "GBP",
    3: "EUR",
    5: "RUB",
    7: "BRL",
    8: "JPY",
    16: "KRW",
    17: "TRY",
    18: "UAH",
    19: "MXN",
    20: "CAD",
    21: "AUD",
    23: "CNY",
    24: "INR",
    25: "CHF",
    34: "ARS",
    40: "AED",
}


# ─── /region command ─────────────────────────────────────────────────────────


async def region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /region — show the user's current region and the region picker."""
    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]
    cur_code = db_user["currency_code"]
    currency_name = _CURRENCY_NAMES.get(cur_code, str(cur_code))

    text = REGION_CURRENT.format(cc=cc, currency_name=currency_name)
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=region_picker_keyboard(),
    )


# ─── Region button selected ──────────────────────────────────────────────────


async def region_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle region:<cc> callback — save the selected region and confirm.

    Accepts any 2-letter code. If the code is not in CC_TO_CURRENCY,
    region_map.get_currency_code() falls back to USD — this is intentional
    (PROJECT.md §2: IR has no Steam currency, so unknown codes get USD).
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    # Extract the 2-letter cc from "region:TR".
    cc = query.data.split(":", 1)[1].strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        await query.answer("Invalid region code.", show_alert=True)
        return

    user = update.effective_user
    if user is None:
        return

    currency_code = get_currency_code(cc)
    await crud.set_region(user.id, cc, currency_code)

    currency_name = _CURRENCY_NAMES.get(currency_code, str(currency_code))
    logger.info("Region changed: user={} cc={} currency={}", user.id, cc, currency_name)

    await query.answer()
    try:
        await query.edit_message_text(
            REGION_CHANGED.format(cc=cc, currency_name=currency_name),
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
    except Exception as exc:
        logger.warning("region_select edit_message failed: {!r}", exc)


# ─── "Other (type code)" button ──────────────────────────────────────────────


async def region_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle region:all — show the full list of all supported regions.

    Each region is displayed as a tappable button with flag, currency name,
    and country code. Selecting any button triggers region:<CC> which is
    handled by region_select_callback() — no text input needed.
    """
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    try:
        await query.edit_message_text(
            "🌍 <b>All Available Regions</b>\n\nTap a region to select it:",
            parse_mode="HTML",
            reply_markup=all_regions_keyboard(),
        )
    except Exception as exc:
        logger.warning("region_manual edit_message failed: {!r}", exc)


# ─── Text input handler ─────────────────────────────────────────────────────


async def handle_region_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input when the user is in the region-manual awaiting state.

    Returns True if the message was consumed, False otherwise.
    """
    if context.user_data is None or context.user_data.get("awaiting") != "region_manual":
        return False

    context.user_data.pop("awaiting", None)

    raw_text = update.message.text if update.message else None
    cc = raw_text.strip().upper() if raw_text else ""

    if len(cc) != 2 or not cc.isalpha():
        # Invalid — show error and re-prompt.
        if update.message is not None:
            await update.message.reply_text(
                REGION_INVALID.format(cc=cc or "(empty)"),
                parse_mode="HTML",
            )
        return True

    user = update.effective_user
    if user is None:
        return True

    currency_code = get_currency_code(cc)
    await crud.set_region(user.id, cc, currency_code)

    currency_name = _CURRENCY_NAMES.get(currency_code, str(currency_code))
    logger.info("Region changed (manual): user={} cc={} currency={}", user.id, cc, currency_name)

    if update.message is not None:
        await update.message.reply_text(
            REGION_CHANGED.format(cc=cc, currency_name=currency_name),
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
    return True
