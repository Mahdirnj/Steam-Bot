"""ISO country code -> Steam priceoverview currency code map (PROJECT.md §10 step 3).

The two Steam parameter systems this maps between are *different*:
- `cc`        is an ISO 3166-1 alpha-2 country code used by `appdetails`
               (e.g. US, TR, UA). Steam derives the currency from the country.
- `currency`  is a *numeric* code used by `priceoverview` (e.g. 1=USD, 17=TRY).

This module bridges them so the `/region` handler can persist both the cc and
its derived currency code in a single call (crud.set_region needs both).

PROJECT.md §2 note honored here: cc=IR (Iran) has no Steam currency, so unknown
codes fall back to USD (1) — matching Steam's own behavior of returning USD
pricing for IR.

Source table: PROJECT.md §4 "currency code table (priceoverview currency param)".
"""
# Default region + currency. These match db/schema.sql's column defaults, so
# new users get sensible values even before they pick a region.
DEFAULT_CC: str = "US"
DEFAULT_CURRENCY_CODE: int = 1  # USD

# ISO country code -> priceoverview numeric currency code.
# The five "picker" regions from PROJECT.md §8 (/region grid) are marked.
CC_TO_CURRENCY: dict[str, int] = {
    "US": 1,   # USD  (picker region)
    "TR": 17,  # TRY  (picker region)
    "UA": 18,  # UAH  (picker region)
    "AR": 34,  # ARS  (picker region)
    "CN": 23,  # CNY  (picker region)
    "GB": 2,   # GBP
    "DE": 3,   # EUR  (representative Eurozone country)
    "RU": 5,   # RUB
    "BR": 7,   # BRL
    "JP": 8,   # JPY
    "KR": 16,  # KRW
    "MX": 19,  # MXN
    "CA": 20,  # CAD
    "AU": 21,  # AUD
    "IN": 24,  # INR
    "CH": 25,  # CHF
    "AE": 40,  # AED
}


def get_currency_code(cc: str) -> int:
    """Return the priceoverview currency code for an ISO country code.

    Input is normalized (whitespace stripped, uppercased) so manual entry of
    "tr", "Tr", or " TR " all resolve. Unknown codes fall back to USD (1) —
    never raises. The db layer requires a non-null currency_code, and this
    fallback matches Steam's behavior for countries without their own currency.
    """
    if not isinstance(cc, str) or not cc.strip():
        return DEFAULT_CURRENCY_CODE
    return CC_TO_CURRENCY.get(cc.strip().upper(), DEFAULT_CURRENCY_CODE)
