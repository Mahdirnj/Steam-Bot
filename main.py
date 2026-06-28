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


# ── Handler imports (stubs for unimplemented steps) ──────────────────────────

from bot.handlers.start import (  # noqa: E402
    help_handler,
    menu_callback,
    start_handler,
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

    # Placeholder handlers for steps 7–10 will be registered here.
    # app.add_handler(CommandHandler("price", price_handler))
    # app.add_handler(CommandHandler("tf2", tf2_handler))
    # app.add_handler(CommandHandler("convert", convert_handler))
    # app.add_handler(CommandHandler("wishlist", wishlist_handler))
    # app.add_handler(CommandHandler("region", region_handler))

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
