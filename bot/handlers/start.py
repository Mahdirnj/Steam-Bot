"""Handler module: /start, /help and the main menu inline keyboard.

Implemented in build step 6 (bot skeleton) and step 12 (/help polish).
See PROJECT.md §8.

The main menu is presented as an InlineKeyboardMarkup. Tapping a button fires
a CallbackQueryHandler routed through menu_callback(). Each button's
callback_data is prefixed "menu:<action>" so a single handler can dispatch.
"""
from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import MAIN_MENU_KEYBOARD, BACK_TO_MENU_KEYBOARD
from bot.messages import (
    HELP_TEXT,
    _main_menu_text,
    GENERIC_ERROR,
)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — greet the user and show the main menu inline keyboard."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    logger.info("/start from user={} ({})", user.id, user.first_name)

    text = _main_menu_text(user.first_name)
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show the full command reference."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    logger.info("/help from user={}", user.id)

    await update.message.reply_text(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=BACK_TO_MENU_KEYBOARD,
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch main-menu inline-button presses.

    callback_data format: "menu:<action>"
    Recognised actions for the skeleton (step 6):
        main    — return to main menu
        price   — stub (will be wired in step 7)
        tf2     — stub (will be wired in step 8)
        wishlist — stub (will be wired in step 10)
        region  — stub (will be wired in step 9)
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    # Always answer the callback to dismiss the loading spinner.
    await query.answer()

    action = query.data.split(":", 1)[1] if ":" in query.data else ""
    user = query.from_user
    logger.info("menu_callback action={!r} user={}", action, user.id)

    if action == "main":
        text = _main_menu_text(user.first_name)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # Stub actions — will be replaced by real handlers in later steps.
    stub_messages = {
        "price": "🔍 <b>Game Price Search</b>\n\nSend me a game name to search.\nExample: <code>elden ring</code>\n\n(Full implementation coming in step 7.)",
        "tf2": "🔑 <b>TF2 Key / Ticket Prices</b>\n\n(Full implementation coming in step 8.)",
        "wishlist": "📋 <b>My Wishlist</b>\n\n(Full implementation coming in step 10.)",
        "region": "⚙️ <b>Region Settings</b>\n\n(Full implementation coming in step 9.)",
    }

    msg = stub_messages.get(action)
    if msg:
        await query.edit_message_text(
            msg,
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
    else:
        logger.warning("Unknown menu action: {!r}", action)
        await query.edit_message_text(
            GENERIC_ERROR,
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
