"""TF2 market service: priceoverview with TTLCache, commission + keys_needed math.

Implemented in build step 5. See PROJECT.md §4.3, §5.1, §5.2.

Components:
    - _parse_price():     robust currency-string parser (handles $, €, ₴, commas, etc.)
    - _fetch_priceoverview(): raw HTTP call to Steam Community Market
    - get_key_price():     cached live Mann Co. Key price in a given currency
    - get_ticket_price():  cached live Tour of Duty Ticket price in a given currency
    - net_proceeds():      85% of listing price (after 15% Steam commission)
    - keys_needed():       how many keys/tickets to sell to afford a game
"""
import re
from typing import Optional, Tuple

import httpx
from cachetools import TTLCache
from loguru import logger

from services.steam import get_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRICEOVERVIEW_URL = (
    "https://steamcommunity.com/market/priceoverview/"
)

# Steam Community Market takes 15% total (5% Steam + 10% TF2).
# Seller receives 85% of the listing price.
STEAM_COMMISSION: float = 0.15

# Exact market_hash_name values for the two tracked items.
KEY_ITEM_NAME: str = "Mann Co. Supply Crate Key"
TICKET_ITEM_NAME: str = "Tour of Duty Ticket"

# ---------------------------------------------------------------------------
# Cache — global, keyed by (market_hash_name, currency_code).
# 15-minute TTL (900 s). maxsize=50 covers 17 currencies × 2 items = 34.
# ---------------------------------------------------------------------------

_cache: TTLCache[Tuple[str, int], float] = TTLCache(maxsize=50, ttl=900)

# ---------------------------------------------------------------------------
# Price-string parser
# ---------------------------------------------------------------------------

# Characters that are never part of a numeric value — strip them all.
_STRIP_RE = re.compile(r"[^\d,.\-]")


def _parse_price(raw: str) -> float:
    """Parse a formatted price string into a float.

    Steam's priceoverview returns formatted strings whose shape varies by
    currency.  Examples:

        "$2.37"       → 2.37      (USD — period decimal)
        "2,37€"       → 2.37      (EUR — comma decimal, symbol after)
        "₴99.00"      → 99.00     (UAH — period decimal)
        "$1,234.56"   → 1234.56   (USD large — comma thousands, period decimal)
        "1.234,56€"   → 1234.56   (EUR large — period thousands, comma decimal)
        "3,942"       → 3942.0    (volume string — comma thousands, no decimal)

    Algorithm:
        1. Strip everything except digits, comma, period, and minus.
        2. If *both* comma and period are present, the **last** one is the
           decimal separator (true for every Steam currency format).
        3. If *only* comma is present AND it's followed by exactly 1–2 digits
           at the end of the string, treat it as a decimal separator.
           Otherwise treat it as a thousands separator and remove it.
        4. If *only* period is present, it is the decimal separator (keep it).
    """
    cleaned = _STRIP_RE.sub("", raw).strip()
    if not cleaned:
        return 0.0

    # Count separators.
    has_comma = "," in cleaned
    has_period = "." in cleaned

    if has_comma and has_period:
        # The last separator is the decimal one.
        last_comma = cleaned.rfind(",")
        last_period = cleaned.rfind(".")
        if last_comma > last_period:
            # European style: 1.234,56  →  remove period thousands, keep comma.
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US style: 1,234.56  →  remove comma thousands, keep period.
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        # Only comma.  Decimal if followed by 1–2 digits at end; else thousands.
        if re.search(r",\d{1,2}$", cleaned):
            cleaned = cleaned.replace(",", ".")  # decimal comma → period
        else:
            cleaned = cleaned.replace(",", "")    # thousands separator
    # elif has_period: nothing to do — already the right separator.

    return float(cleaned)


# ---------------------------------------------------------------------------
# Raw API call
# ---------------------------------------------------------------------------


async def _fetch_priceoverview(
    market_hash_name: str, currency_code: int
) -> Optional[dict]:
    """Call the Steam Community Market priceoverview endpoint.

    Returns the parsed JSON dict (success, lowest_price, volume, median_price)
    or None on any failure. Network errors and malformed responses are logged,
    never raised — callers get None and can show "price unavailable".

    Args:
        market_hash_name: Exact item name (URL-encoded by httpx).
        currency_code:    Numeric currency code (1=USD, 17=TRY, ...).
    """
    try:
        resp = await get_client().get(
            PRICEOVERVIEW_URL,
            params={
                "appid": 440,  # TF2 — always 440
                "currency": currency_code,
                "market_hash_name": market_hash_name,
            },
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "priceoverview network error for {!r} (cur={}): {!r}",
            market_hash_name, currency_code, exc,
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "priceoverview non-200 ({}) for {!r} (cur={})",
            resp.status_code, market_hash_name, currency_code,
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.warning(
            "priceoverview returned non-JSON for {!r} (cur={})",
            market_hash_name, currency_code,
        )
        return None

    if not data.get("success"):
        logger.warning(
            "priceoverview success=false for {!r} (cur={})",
            market_hash_name, currency_code,
        )
        return None

    return data


# ---------------------------------------------------------------------------
# Cached public helpers
# ---------------------------------------------------------------------------


async def get_key_price(currency_code: int) -> Optional[float]:
    """Return the live lowest listing price for a Mann Co. Key.

    Cached for 15 minutes per currency. Returns the raw market price
    (before commission) as a float in the requested currency, or None if
    the API call fails.

    Args:
        currency_code: Numeric Steam currency code (1=USD, 17=TRY, ...).
    """
    cache_key: Tuple[str, int] = (KEY_ITEM_NAME, currency_code)
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        data = await _fetch_priceoverview(KEY_ITEM_NAME, currency_code)
    except Exception as exc:
        logger.warning("get_key_price unexpected error (cur={}): {!r}", currency_code, exc)
        return None
    if data is None:
        return None

    try:
        price = _parse_price(data["lowest_price"])
    except (KeyError, ValueError) as exc:
        logger.warning(
            "Failed to parse key price from response {!r}: {!r}", data, exc
        )
        return None

    _cache[cache_key] = price
    return price


async def get_ticket_price(currency_code: int) -> Optional[float]:
    """Return the live lowest listing price for a Tour of Duty Ticket.

    Cached for 15 minutes per currency. Returns the raw market price
    (before commission) as a float, or None on failure.

    Args:
        currency_code: Numeric Steam currency code (1=USD, 17=TRY, ...).
    """
    cache_key: Tuple[str, int] = (TICKET_ITEM_NAME, currency_code)
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        data = await _fetch_priceoverview(TICKET_ITEM_NAME, currency_code)
    except Exception as exc:
        logger.warning("get_ticket_price unexpected error (cur={}): {!r}", currency_code, exc)
        return None
    if data is None:
        return None

    try:
        price = _parse_price(data["lowest_price"])
    except (KeyError, ValueError) as exc:
        logger.warning(
            "Failed to parse ticket price from response {!r}: {!r}", data, exc
        )
        return None

    _cache[cache_key] = price
    return price


# ---------------------------------------------------------------------------
# Pure math helpers (PROJECT.md §5.1, §5.2)
# ---------------------------------------------------------------------------


def net_proceeds(listing_price: float) -> float:
    """Amount a seller actually receives after Steam's 15% commission.

    PROJECT.md §5.1: 5% Steam fee + 10% TF2 fee = 15% total. Seller gets 85%.

    Args:
        listing_price: The price the item is listed at (raw market price).
    """
    return round(listing_price * (1 - STEAM_COMMISSION), 2)


def keys_needed(game_price: float, key_net_price: float) -> float:
    """How many keys (or tickets) a user must sell to afford a game.

    PROJECT.md §5.2: game_price / key_net_price, rounded to 2 decimals.

    Args:
        game_price:     The cost of the game in the same currency as key_net_price.
        key_net_price:  The net (after-commission) proceeds from selling one key.

    Raises:
        ValueError: If key_net_price <= 0 (can't divide by zero/negative).
    """
    if key_net_price <= 0:
        raise ValueError("key_net_price must be positive")
    return round(game_price / key_net_price, 2)
