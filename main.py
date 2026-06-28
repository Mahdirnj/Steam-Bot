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
)

from config import settings
from db.database import close_db, init_db
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

    Opens the SQLite database and the shared httpx client so every handler
    can use db.get_db() and steam.get_client() immediately.
    """
    logger.info("Starting up — initializing DB and HTTP client...")
    await init_db(settings.db_path)
    await init_client()
    logger.info("Startup complete. DB={}, httpx client ready.", settings.db_path)


async def on_shutdown(app) -> None:
    """Called after polling stops, before the process exits.

    Closes the DB connection and httpx client gracefully.
    """
    logger.info("Shutting down — closing DB and HTTP client...")
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


# ── Text input dispatcher ────────────────────────────────────────────────────

from telegram import Update  # noqa: E402
from telegram.ext import MessageHandler, filters  # noqa: E402


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch plain text messages to the correct handler based on user state.

    This handler is registered last (lowest priority) so commands and callbacks
    are always processed first. Only messages that don't match any command or
    callback pattern reach here.
    """
    # Try each module's input handler in order.  The first one that recognizes
    # the current awaiting state consumes the message.
    if await handle_price_input(update, context):
        return

    # Future awaiting states go here:
    # if await handle_region_input(update, context):
    #     return
    # if await handle_wishlist_input(update, context):
    #     return


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

    # --- Text input dispatcher (must be LAST — lowest priority) ---
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler)
    )

    # --- Start polling (manual async lifecycle for Python 3.14+) ---
    logger.info("Handlers registered. Starting long-polling...")

    async def run() -> None:
        await app.initialize()
        await on_startup(app)  # post_init isn't called in manual lifecycle — call explicitly
        await app.start()
        if app.updater is None:
            raise RuntimeError("Updater not available — check ApplicationBuilder config")
        await app.updater.start_polling(drop_pending_updates=True)
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
