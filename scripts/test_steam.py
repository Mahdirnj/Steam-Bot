"""Live integration test for services/steam.py (PROJECT.md §10 step 4).

Hits real Steam endpoints. PROJECT.md requires testing "against real appids/
searches (e.g. Elden Ring, appid 1245620)". Also covers the network-failure
path offline via monkeypatching.

Run:

    python scripts/test_steam.py

Requires internet. Exits 0 on success, 1 on failure.
"""
import asyncio
import sys
from unittest.mock import patch

# Make the project root importable when run as a script from any directory.
sys.path.insert(0, "..")

import httpx  # noqa: E402

from services import steam  # noqa: E402


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


async def test_live() -> None:
    print("Live Steam checks...")

    # --- storesearch: resolves Elden Ring to its appid, items carry id+name only ---
    results = await steam.storesearch("elden ring", "US")
    check("storesearch returns non-empty results", len(results) > 0)
    check("storesearch first result is ELDEN RING (1245620)",
          results[0]["appid"] == 1245620)
    check("storesearch items carry only appid + name",
          set(results[0].keys()) == {"appid", "name"})
    check("storesearch appid is an int", isinstance(results[0]["appid"], int))

    # --- storesearch: respects the 5-result cap (term with many matches) ---
    check("storesearch caps at MAX_SEARCH_RESULTS (5)", len(results) <= 5)

    # --- storesearch: no match -> [] ---
    empty = await steam.storesearch("zzzznomatchxyz", "US")
    check("storesearch returns [] on no match", empty == [])

    # --- storesearch: empty term -> [] without calling the API ---
    check("storesearch empty term -> []", await steam.storesearch("", "US") == [])
    check("storesearch whitespace term -> []", await steam.storesearch("   ", "US") == [])

    # --- appdetails: paid game has name + price_overview with int final (cents) ---
    er = await steam.appdetails(1245620, "US")
    check("appdetails(1245620) returns a dict", isinstance(er, dict))
    check("appdetails(1245620) has a name", bool(er.get("name")))
    check("appdetails(1245620) has price_overview", isinstance(er.get("price_overview"), dict))
    check("appdetails price_overview.final is int (cents)",
          isinstance(er["price_overview"].get("final"), int))

    # --- appdetails: free game (TF2) is_free True, NO price_overview ---
    tf2 = await steam.appdetails(440, "US")
    check("appdetails(440) returns a dict", isinstance(tf2, dict))
    check("appdetails(440) is_free is True", tf2.get("is_free") is True)
    check("appdetails(440) has NO price_overview", "price_overview" not in tf2)

    # --- appdetails: invalid appid -> None (success:false, data absent) ---
    bad = await steam.appdetails(99999999, "US")
    check("appdetails(invalid appid) -> None", bad is None)


async def test_network_failure() -> None:
    print("Network-failure checks (monkeypatched, no internet needed)...")

    # Force get_client() to return a client whose GET always raises ConnectError.
    class _Broken:
        async def get(self, *a, **k):
            raise httpx.ConnectError("simulated outage")

    with patch.object(steam, "get_client", return_value=_Broken()):
        check("storesearch on network failure -> []",
              await steam.storesearch("elden ring", "US") == [])
        check("appdetails on network failure -> None",
              await steam.appdetails(1245620, "US") is None)


async def test_client_lifecycle() -> None:
    print("Client lifecycle checks...")

    # Fresh state for this check: ensure not already initialized.
    try:
        await steam.close_client()
    except Exception:
        pass

    await steam.init_client()
    c1 = steam.get_client()
    check("init_client creates a client", c1 is not None)

    raised = False
    try:
        await steam.init_client()
    except RuntimeError:
        raised = True
    check("init_client twice raises RuntimeError", raised)

    check("get_client returns the same instance", steam.get_client() is c1)

    # After close, get_client must raise until re-initialized.
    await steam.close_client()
    raised = False
    try:
        steam.get_client()
    except RuntimeError:
        raised = True
    check("get_client raises after close_client", raised)

    # close_client is idempotent: calling it again (no active client) is a no-op.
    await steam.close_client()
    await steam.close_client()
    check("close_client is idempotent (no-op on empty state)", True)


async def main() -> None:
    await steam.init_client()
    try:
        await test_live()
        await test_network_failure()
    finally:
        await steam.close_client()

    # Lifecycle tests manage their own client state.
    await test_client_lifecycle()

    print("\nAll steam service checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nUnexpected error: {exc!r}", file=sys.stderr)
        sys.exit(1)
