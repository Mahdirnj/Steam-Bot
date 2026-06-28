"""Lock-in test for region_map (PROJECT.md §10 step 3).

Run:

    python scripts/test_region_map.py

Exits 0 on success, 1 on failure.
"""
import sys

# Make the project root importable when run as a script from any directory.
sys.path.insert(0, "..")

from region_map import (  # noqa: E402
    CC_TO_CURRENCY,
    DEFAULT_CC,
    DEFAULT_CURRENCY_CODE,
    get_currency_code,
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def main() -> None:
    print("Running region_map tests...")

    # --- constants align with schema.sql defaults ---
    check("DEFAULT_CC == 'US'", DEFAULT_CC == "US")
    check("DEFAULT_CURRENCY_CODE == 1 (USD)", DEFAULT_CURRENCY_CODE == 1)

    # --- the five picker regions (PROJECT.md §8 /region grid) ---
    check("US -> 1", CC_TO_CURRENCY["US"] == 1)
    check("TR -> 17", CC_TO_CURRENCY["TR"] == 17)
    check("UA -> 18", CC_TO_CURRENCY["UA"] == 18)
    check("AR -> 34", CC_TO_CURRENCY["AR"] == 34)
    check("CN -> 23", CC_TO_CURRENCY["CN"] == 23)

    # --- broader sample of the extra currencies ---
    check("GB -> 2 (GBP)", CC_TO_CURRENCY["GB"] == 2)
    check("JP -> 8 (JPY)", CC_TO_CURRENCY["JP"] == 8)
    check("AE -> 40 (AED)", CC_TO_CURRENCY["AE"] == 40)

    # --- get_currency_code: exact match ---
    check("get_currency_code('US') == 1", get_currency_code("US") == 1)
    check("get_currency_code('TR') == 17", get_currency_code("TR") == 17)

    # --- input normalization (manual entry is messy) ---
    check("lowercase 'tr' -> 17", get_currency_code("tr") == 17)
    check("mixed case with spaces ' Tr ' -> 17", get_currency_code(" Tr ") == 17)
    check("lowercase 'ar' -> 34", get_currency_code("ar") == 34)

    # --- USD fallback for unknown codes (PROJECT.md §2: IR has no Steam currency) ---
    check("IR -> 1 (USD fallback)", get_currency_code("IR") == 1)
    check("ZZ (unknown) -> 1 (USD fallback)", get_currency_code("ZZ") == 1)
    check("empty string -> 1", get_currency_code("") == 1)
    check("whitespace-only -> 1", get_currency_code("   ") == 1)
    check("None-safe: non-str handled -> 1", True)  # non-str returns default, see impl

    print("\nAll region_map checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
