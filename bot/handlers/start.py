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

from bot.keyboards import MAIN_MENU_KEYBOARD, BACK_TO_MENU_KEYBOARD, region_picker_keyboard
from bot.messages import (
    HELP_TEXT,
    REGION_CURRENT,
    _main_menu_text,
    GENERIC_ERROR,
)
from db import crud


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
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
        except Exception:
            # The message might be a photo message (from /price result card).
            # Delete it and send a new text message instead.
            try:
                await query.message.delete()  # type: ignore[union-attr]
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=user.id,
                text=text,
                parse_mode="HTML",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
        return

    # "price" action — set awaiting state so the next message is treated as a search.
    if action == "price":
        if context.user_data is not None:
            context.user_data["awaiting"] = "price_search"
        await query.edit_message_text(
            "🔍 <b>Game Price Search</b>\n\nSend me a game name to search.\nExample: <code>elden ring</code>",
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
        return

    # "tf2" action — fetch and display live TF2 prices directly.
    if action == "tf2":
        from bot.keyboards import TF2_KEYBOARD
        from bot.handlers.tf2 import _build_tf2_text

        db_user = await crud.get_or_create_user(user.id)
        text = await _build_tf2_text(db_user["currency_code"])
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=TF2_KEYBOARD
        )
        return

    # "region" action — show current region + picker keyboard.
    if action == "region":
        from bot.handlers.settings import _CURRENCY_NAMES

        db_user = await crud.get_or_create_user(user.id)
        cc = db_user["region_cc"]
        cur_code = db_user["currency_code"]
        currency_name = _CURRENCY_NAMES.get(cur_code, str(cur_code))
        text = REGION_CURRENT.format(cc=cc, currency_name=currency_name)
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=region_picker_keyboard()
        )
        return

    # "wishlist" action — show the user's wishlist with prices.
    if action == "wishlist":
        from bot.handlers.wishlist import wishlist_menu_callback

        await wishlist_menu_callback(update, context)
        return

    # "wishlist_add" action — from wishlist actions keyboard.
    if action == "wishlist_add":
        if context.user_data is not None:
            context.user_data["awaiting"] = "wishlist_add"
        await query.edit_message_text(
            "🔍 <b>Add to Wishlist</b>\n\nSend me a game name to search.\nExample: <code>elden ring</code>",
            parse_mode="HTML",
            reply_markup=BACK_TO_MENU_KEYBOARD,
        )
        return

    # "wishlist_remove" action — from wishlist actions keyboard.
    if action == "wishlist_remove":
        # We need to call the picker, but we're in a callback context.
        # Create a pseudo-message update for the picker.
        items = await crud.list_wishlist_for_user(user.id)
        if not items:
            from bot.messages import WISHLIST_EMPTY
            await query.edit_message_text(
                WISHLIST_EMPTY,
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
        else:
            from bot.keyboards import wishlist_remove_keyboard
            await query.edit_message_text(
                "🗑️ Tap a game to remove it from your wishlist:",
                parse_mode="HTML",
                reply_markup=wishlist_remove_keyboard(items),
            )
        return

    # "wishlist_summary" action — from wishlist actions keyboard.
    if action == "wishlist_summary":
        from bot.handlers.wishlist import _build_wishlist_text

        await query.answer()
        try:
            await query.edit_message_text(
                "📋 Loading sale summary\u2026",
                parse_mode="HTML",
            )
        except Exception:
            pass

        items = await crud.list_wishlist_for_user(user.id)
        if not items:
            from bot.messages import WISHLIST_EMPTY
            await query.edit_message_text(
                WISHLIST_EMPTY,
                parse_mode="HTML",
                reply_markup=BACK_TO_MENU_KEYBOARD,
            )
        else:
            db_user = await crud.get_or_create_user(user.id)
            text = await _build_wishlist_text(items, db_user["region_cc"], summary_only=True)
            from bot.handlers.wishlist import _wishlist_actions_keyboard
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=_wishlist_actions_keyboard()
                )
            except Exception:
                pass
        return

    logger.warning("Unknown menu action: {!r}", action)
    await query.edit_message_text(
        GENERIC_ERROR,
        parse_mode="HTML",
        reply_markup=BACK_TO_MENU_KEYBOARD,
    )
