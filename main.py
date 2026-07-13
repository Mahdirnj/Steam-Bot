"""Bot entry point: builds the Application, registers handlers, starts polling.

Implemented in build step 6 (skeleton), then extended in steps 7–11.
See PROJECT.md §7, §10 step 6.

Lifecycle:
    1. loguru is configured (console + rotating file).
    2. Application is built via ApplicationBuilder().token(...).
    3. post_init callback opens the DB and httpx client.
    4. Handlers are registered.
    5. Manual async lifecycle: initialize → start → start_polling → (run) → stop → shutdown.
    6. post_shutdown callback closes DB and httpx client.
"""
import asyncio
import sys

from loguru import logger
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
)

from config import settings
from db.database import close_db, init_db
from health import start_web_server, stop_web_server
from services.steam import close_client, init_client

# ── Logging setup ────────────────────────────────────────────────────────────

# Remove loguru's default stderr handler so we can add our own with a format.
logger.remove()

# Console handler — coloured, with module name for quick debugging.
logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    ),
    level=settings.log_level,
    colorize=True,
)

# Rotating log file — 10 MB per file, keep 7 days.
logger.add(
    "logs/bot.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
    encoding="utf-8",
)


# ── Lifecycle callbacks ──────────────────────────────────────────────────────


async def on_startup(app) -> None:
    """Called once after the Application is initialized, before polling starts.

    Opens the SQLite database, the shared httpx client, and the health server
    so every handler can use db.get_db() and steam.get_client() immediately.
    """
    logger.info("Starting up — initializing DB, HTTP client, and health server...")
    await init_db(settings.db_path)
    await init_client()
    await start_web_server(settings.web_port)
    logger.info("Startup complete. DB={}, httpx client ready, health server on :{}.",
                settings.db_path, settings.web_port)


async def on_shutdown(app) -> None:
    """Called after polling stops, before the process exits.

    Closes the health server, DB connection, and httpx client gracefully.
    """
    logger.info("Shutting down — closing health server, DB and HTTP client...")
    await stop_web_server()
    await close_client()
    await close_db()
    logger.info("Shutdown complete. Goodbye.")


# ── Handler imports ───────────────────────────────────────────────────────────

from bot.handlers.start import (  # noqa: E402
    help_handler,
    menu_callback,
    start_handler,
)
from bot.handlers.price import (  # noqa: E402
    handle_price_input,
    price_add_wishlist_callback,
    price_back_callback,
    price_compare_callback,
    price_dlc_callback,
    price_handler,
    price_select_callback,
)
from bot.handlers.tf2 import (  # noqa: E402
    convert_handler,
    tf2_handler,
    tf2_refresh_callback,
)
from bot.handlers.settings import (  # noqa: E402
    handle_region_input,
    region_handler,
    region_manual_callback,
    region_select_callback,
)
from bot.handlers.wishlist import (  # noqa: E402
    handle_wishlist_add_input,
    wishlist_direct_add_callback,
    wishlist_handler,
    wishlist_remove_callback,
)
from bot.handlers.inline import inline_query_handler  # noqa: E402
from scheduler.jobs import check_all_wishlists  # noqa: E402


# ── Text input dispatcher ────────────────────────────────────────────────────

from telegram import Update  # noqa: E402
from telegram.ext import MessageHandler, filters  # noqa: E402

from bot.utils import extract_appid_from_text  # noqa: E402


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch plain text messages to the correct handler based on user state.

    This handler is registered last (lowest priority) so commands and callbacks
    are always processed first. Only messages that don't match any command or
    callback pattern reach here.
    """
    # Check for Steam URL first — this takes priority over awaiting states
    # so users can paste a URL anytime.
    if update.message and update.message.text:
        appid = extract_appid_from_text(update.message.text)
        if appid is not None:
            await handle_steam_url(update, context, appid)
            return

    # Try each module's input handler in order.  The first one that recognizes
    # the current awaiting state consumes the message.
    if await handle_price_input(update, context):
        return

    if await handle_wishlist_add_input(update, context):
        return

    if await handle_region_input(update, context):
        return


async def handle_steam_url(update: Update, context: ContextTypes.DEFAULT_TYPE, appid: int) -> None:
    """Handle a Steam store URL — fetch game details and show the result card.

    This allows users to paste a Steam URL (e.g. from their browser) and
    instantly get the price card with all actions (wishlist, compare, DLCs, etc).
    """
    from bot.handlers.price import _build_result_card
    from bot.keyboards import result_card_keyboard
    from db import crud
    from services import steam

    if update.message is None:
        return

    user = update.effective_user
    if user is None:
        return

    db_user = await crud.get_or_create_user(user.id)
    cc = db_user["region_cc"]

    data = await steam.appdetails(appid, cc)
    if data is None:
        await update.message.reply_text(
            "⚠️ Couldn't fetch game details for this URL. Please check the link and try again.",
            parse_mode="HTML",
        )
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
        logger.warning("handle_steam_url send_photo failed: {!r}", exc)
        await context.bot.send_message(
            chat_id=user.id,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
        )


# ── Build and run ────────────────────────────────────────────────────────────


def main() -> None:
    """Build the Application, register handlers, and start long-polling.

    Uses manual async lifecycle instead of run_polling() for Python 3.14+
    compatibility (asyncio.get_event_loop() no longer auto-creates one).
    """
    logger.info("Building Application...")

    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(15)
        .build()
    )

    # --- Command handlers ---
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))

    # --- Main menu inline keyboard callbacks ---
    # Matches callback_data starting with "menu:" — dispatched to menu_callback.
    app.add_handler(
        CallbackQueryHandler(menu_callback, pattern=r"^menu:")
    )

    # --- Step 7: /price flow handlers ---
    app.add_handler(CommandHandler("price", price_handler))

    # Game selection from search results (price:appid:123456).
    app.add_handler(
        CallbackQueryHandler(price_select_callback, pattern=r"^price:appid:\d+$")
    )
    # Back to game result card (back:123456).
    app.add_handler(
        CallbackQueryHandler(price_back_callback, pattern=r"^back:\d+$")
    )
    # Add to wishlist from result card (wish:add:123456).
    app.add_handler(
        CallbackQueryHandler(price_add_wishlist_callback, pattern=r"^wish:add:\d+$")
    )
    # Compare regions (compare:123456).
    app.add_handler(
        CallbackQueryHandler(price_compare_callback, pattern=r"^compare:\d+$")
    )
    # Show DLCs (dlc:123456).
    app.add_handler(
        CallbackQueryHandler(price_dlc_callback, pattern=r"^dlc:\d+$")
    )

    # --- Step 8: /tf2 and /convert handlers ---
    app.add_handler(CommandHandler("tf2", tf2_handler))
    app.add_handler(CommandHandler("convert", convert_handler))
    # TF2 refresh button (refresh:tf2).
    app.add_handler(
        CallbackQueryHandler(tf2_refresh_callback, pattern=r"^refresh:tf2$")
    )

    # --- Step 9: /region handler ---
    app.add_handler(CommandHandler("region", region_handler))
    # Region selection from picker (region:TR, region:US, etc.).
    app.add_handler(
        CallbackQueryHandler(region_select_callback, pattern=r"^region:[A-Z]{2}$")
    )
    # More regions list (region:all).
    app.add_handler(
        CallbackQueryHandler(region_manual_callback, pattern=r"^region:all$")
    )

    # --- Step 10: /wishlist handler ---
    app.add_handler(CommandHandler("wishlist", wishlist_handler))
    # Direct add from search results (wish:direct:123456:Game Name).
    app.add_handler(
        CallbackQueryHandler(wishlist_direct_add_callback, pattern=r"^wish:direct:\d+:")
    )
    # Remove from wishlist picker (wish:remove:123456).
    app.add_handler(
        CallbackQueryHandler(wishlist_remove_callback, pattern=r"^wish:remove:\d+$")
    )

    # --- Inline mode handler ---
    app.add_handler(InlineQueryHandler(inline_query_handler))

    # --- Text input dispatcher (must be LAST — lowest priority) ---
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler)
    )

    # --- Start polling (manual async lifecycle for Python 3.14+) ---
    logger.info("Handlers registered. Starting long-polling...")

    async def run() -> None:
        # Retry initialization on transient network errors (ConnectTimeout, etc.)
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                await app.initialize()
                break
            except Exception as exc:
                if attempt == max_retries:
                    logger.error("Failed to connect after {} attempts: {!r}", max_retries, exc)
                    raise
                wait = min(2 ** attempt, 30)
                logger.warning("Connection failed (attempt {}/{}): {!r}. Retrying in {}s...",
                               attempt, max_retries, exc, wait)
                await asyncio.sleep(wait)

        await on_startup(app)  # post_init isn't called in manual lifecycle — call explicitly
        await app.start()
        if app.updater is None:
            raise RuntimeError("Updater not available — check ApplicationBuilder config")
        await app.updater.start_polling(drop_pending_updates=True)

        # --- Step 11: Register the wishlist price-check scheduler ---
        # Runs every 6 hours (21600s). First run after 60s so the bot
        # has time to settle and take initial snapshots.
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                check_all_wishlists,
                interval=21600,   # 6 hours in seconds
                first=60,         # first run 60s after startup
            )
            logger.info("Wishlist scheduler registered (every 6h, first in 60s).")
        else:
            logger.warning("JobQueue not available — scheduler not registered. "
                           "Install python-telegram-bot[job-queue].")

        logger.info("Bot is running. Press Ctrl-C to stop.")

        try:
            # Block forever (until KeyboardInterrupt / SIGTERM).
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("Stopping bot...")
            await app.updater.stop()
            await app.stop()
            await on_shutdown(app)  # post_shutdown isn't called in manual lifecycle — call explicitly
            await app.shutdown()
            logger.info("Shutdown complete. Goodbye.")

    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
