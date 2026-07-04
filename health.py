"""Minimal async HTTP server exposing /health for Docker HEALTHCHECK and monitoring."""

from aiohttp import web
from loguru import logger

_runner: web.AppRunner | None = None


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def start_web_server(port: int) -> None:
    global _runner
    app = web.Application()
    app.router.add_get("/health", _health)
    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server listening on port {}.", port)


async def stop_web_server() -> None:
    global _runner
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
