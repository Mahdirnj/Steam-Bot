"""Steam store service: game-name search and per-game price details (PROJECT.md §4.1, §4.2).

Two responsibilities live here:
  1. A shared async httpx client (init/get/close singleton, mirroring db/database.py)
     so the many appdetails calls (scheduler, "Compare Regions", "Show DLCs") reuse
     pooled connections.
  2. Two thin Steam API wrappers:
       - storesearch(): resolve a game name to an appid
       - appdetails():   fetch authoritative price + metadata for an appid/region

The service returns raw/simplified payloads; handlers and the scheduler own all
rendering and cents->amount math (PROJECT.md §4.2). Network failures are soft:
each function logs and returns [] / None so the caller can show "try again"
instead of crashing (PROJECT.md §12).

No retries and no raise_for_status: Steam's rate-limit strategy for this project
is the TF2 TTLCache (step 5), and any non-OK status is treated as a soft failure.
"""
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

# --- Endpoints (stable; intentionally not configurable) ----------------------
STORESEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails/"

# Caps and limits.
MAX_SEARCH_RESULTS = 5  # PROJECT.md §8 /price flow: "top 3-5 results as buttons".

# Browser-like identity so Steam doesn't reject/penalize bare client defaults.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# --- Client lifecycle ---------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None


async def init_client() -> None:
    """Create the shared httpx client. Call once at startup (main.py, step 6)."""
    global _client
    if _client is not None:
        raise RuntimeError("Steam client already initialized. Call close_client() first.")
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en",  # bias storesearch results toward English names
        },
    )


def get_client() -> httpx.AsyncClient:
    """Return the live client (sync, mirrors db.get_db). Raises if uninitialized."""
    if _client is None:
        raise RuntimeError("Steam client not initialized — call init_client() first.")
    return _client


async def close_client() -> None:
    """Close the client. Safe to call multiple times."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# --- Steam endpoints ----------------------------------------------------------
async def storesearch(term: str, cc: str) -> List[Dict[str, Any]]:
    """Resolve a game name to a list of ``{appid, name}`` dicts.

    Use this only for name -> appid resolution (PROJECT.md §4.1); authoritative
    pricing always comes from appdetails(). Returns at most MAX_SEARCH_RESULTS
    items, oldest/top-relevance order as Steam returns them.

    Args:
        term: Game name to search (e.g. "elden ring").
        cc:   ISO country code to bias/format results (e.g. "US").

    Returns:
        ``[{"appid": int, "name": str}, ...]``; empty list on no match, malformed
        response, or network failure (logged, never raised).
    """
    if not term or not term.strip():
        return []

    try:
        resp = await get_client().get(
            STORESEARCH_URL,
            params={"term": term.strip(), "cc": cc, "l": "english"},
        )
    except httpx.HTTPError as exc:
        logger.warning("storesearch network error for {!r}: {!r}", term, exc)
        return []

    if resp.status_code != 200:
        logger.warning("storesearch non-200 ({}%) for {!r}", resp.status_code, term)
        return []

    try:
        items = resp.json().get("items")
    except ValueError:  # malformed JSON
        logger.warning("storesearch returned non-JSON for {!r}", term)
        return []

    if not isinstance(items, list):
        return []

    results: List[Dict[str, Any]] = []
    for item in items[:MAX_SEARCH_RESULTS]:
        appid = item.get("id")
        name = item.get("name")
        if isinstance(appid, int) and isinstance(name, str):
            results.append({"appid": appid, "name": name})
    return results


async def appdetails(appid: int, cc: str) -> Optional[Dict[str, Any]]:
    """Fetch the price/metadata payload for a single appid in a region.

    Response shape is ``{<appid_str>: {"success": bool, "data": {...}}}`` — and
    **``data`` is entirely absent when success is false** (verified live), so it
    is read defensively via .get("data").

    Args:
        appid: Steam appid (e.g. 1245620 for Elden Ring).
        cc:    ISO country code (e.g. "US", "TR"). Steam derives the currency.

    Returns:
        The inner ``data`` dict (contains name, is_free, price_overview or its
        absence, dlc, ...), or ``None`` on failure/invalid-appid/unavailable-in-
        region. Network errors are logged, never raised.
    """
    try:
        resp = await get_client().get(
            APPDETAILS_URL,
            params={"appids": appid, "cc": cc},
        )
    except httpx.HTTPError as exc:
        logger.warning("appdetails network error for {}: {!r}", appid, exc)
        return None

    if resp.status_code != 200:
        logger.warning("appdetails non-200 ({}) for {}", resp.status_code, appid)
        return None

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("appdetails returned non-JSON for {}", appid)
        return None

    entry = payload.get(str(appid))
    if not isinstance(entry, dict):
        return None
    if not entry.get("success"):
        return None

    data = entry.get("data")
    if not isinstance(data, dict):
        # Defensive: success=True but no/invalid data — treat as unavailable.
        return None
    return data
