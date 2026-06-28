"""Integration + unit tests for services/tf2_market.py (PROJECT.md §10 step 5).

Covers:
    - _parse_price() — unit tests for many currency formats
    - net_proceeds() and keys_needed() — pure math
    - Live API: get_key_price(1) and get_ticket_price(1) in USD
    - Cache behaviour: second call hits cache
    - Network failure: monkeypatched
    - Invalid inputs: zero/negative key_net_price

Run:

    python scripts/test_tf2_market.py

Requires internet for live tests. Exits 0 on success, 1 on failure.
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

# Make the project root importable when run as a script from any directory.
sys.path.insert(0, "..")

import httpx  # noqa: E402

from services import steam  # noqa: E402
from services import tf2_market  # noqa: E402
from services.tf2_market import (  # noqa: E402
    _parse_price,
    get_key_price,
    get_ticket_price,
    keys_needed,
    net_proceeds,
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


# ── Unit tests for _parse_price ──────────────────────────────────────────────


def test_parse_price() -> None:
    """Verify the parser handles every Steam currency format correctly."""
    print("Price-string parser tests...")

    # USD (period decimal)
    check("_parse_price('$2.37') == 2.37", _parse_price("$2.37") == 2.37)
    check("_parse_price('$0.99') == 0.99", _parse_price("$0.99") == 0.99)

    # EUR with comma decimal, symbol after
    check("_parse_price('2,37€') == 2.37", _parse_price("2,37€") == 2.37)
    check("_parse_price('0,99€') == 0.99", _parse_price("0,99€") == 0.99)

    # UAH — symbol before, period decimal
    check("_parse_price('₴99.00') == 99.00", _parse_price("₴99.00") == 99.00)
    check("_parse_price('₴2.37') == 2.37", _parse_price("₴2.37") == 2.37)

    # RUB — symbol after, comma decimal
    check("_parse_price('185,00pуб') == 185.00", _parse_price("185,00pуб") == 185.00)

    # USD large — comma as thousands, period as decimal
    check(
        "_parse_price('$1,234.56') == 1234.56",
        _parse_price("$1,234.56") == 1234.56,
    )

    # EUR large — period as thousands, comma as decimal
    check(
        "_parse_price('1.234,56€') == 1234.56",
        _parse_price("1.234,56€") == 1234.56,
    )

    # Volume strings (comma as thousands, no decimal)
    check("_parse_price('3,942') == 3942.0", _parse_price("3,942") == 3942.0)
    check("_parse_price('12,345') == 12345.0", _parse_price("12,345") == 12345.0)

    # Plain number (no symbol, no separator)
    check("_parse_price('5') == 5.0", _parse_price("5") == 5.0)
    check("_parse_price('99.99') == 99.99", _parse_price("99.99") == 99.99)

    # Negative (unlikely but safe)
    check("_parse_price('-1.50') == -1.5", _parse_price("-1.50") == -1.5)

    # Empty / whitespace
    check("_parse_price('') == 0.0", _parse_price("") == 0.0)
    check("_parse_price('   ') == 0.0", _parse_price("   ") == 0.0)

    # JPY — no decimal places, symbol before
    check("_parse_price('¥500') == 500.0", _parse_price("¥500") == 500.0)

    # KRW — large number, comma thousands
    check("_parse_price('₩35,000') == 35000.0", _parse_price("₩35,000") == 35000.0)

    # CHF — period decimal, symbol before
    check("_parse_price('CHF 2.37') == 2.37", _parse_price("CHF 2.37") == 2.37)

    # INR — symbol before, period decimal, comma thousands
    check("_parse_price('₹1,234.56') == 1234.56", _parse_price("₹1,234.56") == 1234.56)

    print()


# ── Unit tests for net_proceeds ──────────────────────────────────────────────


def test_net_proceeds() -> None:
    """Verify the 85% commission calculation."""
    print("net_proceeds tests...")

    check("net_proceeds(2.37) == 2.01", net_proceeds(2.37) == 2.01)
    check("net_proceeds(100.00) == 85.00", net_proceeds(100.00) == 85.00)
    check("net_proceeds(0.0) == 0.0", net_proceeds(0.0) == 0.0)
    check("net_proceeds(1.0) == 0.85", net_proceeds(1.0) == 0.85)
    # Rounding: 2.37 * 0.85 = 2.0145 → round to 2.01
    check("net_proceeds handles rounding (2.37)", net_proceeds(2.37) == 2.01)
    # Large value
    check("net_proceeds(1000.0) == 850.0", net_proceeds(1000.0) == 850.0)

    print()


# ── Unit tests for keys_needed ───────────────────────────────────────────────


def test_keys_needed() -> None:
    """Verify the division logic and error handling."""
    print("keys_needed tests...")

    check("keys_needed(59.99, 2.01) == 29.85", keys_needed(59.99, 2.01) == 29.85)
    check("keys_needed(10.0, 2.0) == 5.0", keys_needed(10.0, 2.0) == 5.0)
    check("keys_needed(0.0, 2.0) == 0.0", keys_needed(0.0, 2.0) == 0.0)
    check("keys_needed(1.0, 0.5) == 2.0", keys_needed(1.0, 0.5) == 2.0)

    # Must raise on zero or negative key_net_price.
    raised_zero = False
    try:
        keys_needed(10.0, 0.0)
    except ValueError:
        raised_zero = True
    check("keys_needed raises ValueError on zero key_net_price", raised_zero)

    raised_neg = False
    try:
        keys_needed(10.0, -1.0)
    except ValueError:
        raised_neg = True
    check("keys_needed raises ValueError on negative key_net_price", raised_neg)

    print()


# ── Cache tests ──────────────────────────────────────────────────────────────


async def test_cache() -> None:
    """Verify that the TTLCache stores and returns cached values."""
    print("Cache tests...")

    # Clear cache for a clean test.
    tf2_market._cache.clear()

    # Fake a successful priceoverview response.
    fake_response = {
        "success": True,
        "lowest_price": "$2.50",
        "volume": "3,942",
        "median_price": "$2.48",
    }

    mock_data = {"success": True, "lowest_price": "$2.50", "volume": "3,942", "median_price": "$2.48"}

    # Patch _fetch_priceoverview to count calls.
    call_count = 0

    async def mock_fetch(name: str, code: int) -> dict:
        nonlocal call_count
        call_count += 1
        return mock_data

    with patch.object(tf2_market, "_fetch_priceoverview", side_effect=mock_fetch):
        # First call — should hit the mock.
        price1 = await get_key_price(1)
        check("get_key_price(1) returns a float", isinstance(price1, float))
        check("get_key_price(1) == 2.50", price1 == 2.50)
        check("fetch called once", call_count == 1)

        # Second call — should hit cache, no extra fetch.
        price2 = await get_key_price(1)
        check("get_key_price(1) cached == 2.50", price2 == 2.50)
        check("fetch still called only once (cache hit)", call_count == 1)

        # Different currency — should fetch again.
        price3 = await get_key_price(17)
        check("get_key_price(17) returns 2.50 (same mock)", price3 == 2.50)
        check("fetch called twice (new currency)", call_count == 2)

    tf2_market._cache.clear()
    print()


# ── Live API tests ───────────────────────────────────────────────────────────


async def test_live() -> None:
    """Hit real Steam endpoints. Requires internet."""
    print("Live TF2 market checks (USD, currency=1)...")

    # Key price in USD.
    key_price = await get_key_price(1)
    check("get_key_price(1) returns a float", isinstance(key_price, float))
    check("get_key_price(1) > 0", key_price is not None and key_price > 0)
    print(f"    key_price(USD) = {key_price}")

    # Net proceeds from the key.
    key_net = net_proceeds(key_price)
    check("net_proceeds(key_price) < key_price", key_net < key_price)
    check("net_proceeds(key_price) > 0", key_net > 0)
    print(f"    key_net(USD)   = {key_net}")

    # Ticket price in USD.
    ticket_price = await get_ticket_price(1)
    check("get_ticket_price(1) returns a float", isinstance(ticket_price, float))
    check("get_ticket_price(1) > 0", ticket_price is not None and ticket_price > 0)
    print(f"    ticket_price(USD) = {ticket_price}")

    # Net proceeds from the ticket.
    ticket_net = net_proceeds(ticket_price)
    check("net_proceeds(ticket_price) > 0", ticket_net > 0)
    print(f"    ticket_net(USD)   = {ticket_net}")

    # keys_needed with real prices.
    count = keys_needed(59.99, key_net)
    check("keys_needed(59.99, key_net) > 0", count > 0)
    print(f"    keys to afford $59.99 game ≈ {count}")

    print()


# ── Network failure tests ────────────────────────────────────────────────────


async def test_network_failure() -> None:
    """Verify graceful handling when the HTTP call fails."""
    print("Network-failure checks (monkeypatched)...")

    tf2_market._cache.clear()

    # Simulate a network error from _fetch_priceoverview.
    async def broken_fetch(name: str, code: int):
        raise httpx.ConnectError("simulated outage")

    with patch.object(tf2_market, "_fetch_priceoverview", side_effect=broken_fetch):
        key = await get_key_price(1)
        check("get_key_price on failure -> None", key is None)

        ticket = await get_ticket_price(1)
        check("get_ticket_price on failure -> None", ticket is None)

    # Simulate success=false from the API.
    async def failed_fetch(name: str, code: int):
        return None  # _fetch_priceoverview returns None on success=false

    with patch.object(tf2_market, "_fetch_priceoverview", side_effect=failed_fetch):
        key2 = await get_key_price(99)
        check("get_key_price on success=false -> None", key2 is None)

    tf2_market._cache.clear()
    print()


# ── Main ─────────────────────────────────────────────────────────────────────


async def main() -> None:
    # Unit tests (no network needed).
    test_parse_price()
    test_net_proceeds()
    test_keys_needed()

    # Cached tests (no network, uses mock).
    await test_cache()

    # Network-failure tests (no network, monkeypatched).
    await test_network_failure()

    # Live tests — need internet + steam client.
    await steam.init_client()
    try:
        await test_live()
    finally:
        await steam.close_client()

    print("All tf2_market checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nUnexpected error: {exc!r}", file=sys.stderr)
        sys.exit(1)
