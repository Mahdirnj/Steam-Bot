"""Shared utilities for bot handlers."""
import asyncio

from telegram.constants import ChatAction
from telegram.ext import ContextTypes


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, stop: asyncio.Event) -> None:
    """Periodically re-send typing action until stop event is set."""
    while not stop.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass


class TypingIndicator:
    """Async context manager that keeps the Telegram 'typing...' indicator alive.

    Usage:
        async with TypingIndicator(context, chat_id):
            # slow work here — typing indicator stays visible
            data = await steam.appdetails(appid, cc)
    """

    def __init__(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        self.context = context
        self.chat_id = chat_id
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        try:
            await self.context.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
        except Exception:
            pass  # Don't crash the handler if typing action fails
        self._task = asyncio.create_task(_keep_typing(self.context, self.chat_id, self._stop))
        return self

    async def __aexit__(self, *args):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
