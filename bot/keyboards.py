"""All InlineKeyboardMarkup builders (main menu, search results, result card, etc.).

Every handler imports keyboards from here instead of building button lists inline.
Button text and callback_data formats are centralised so refactoring is safe.

See PROJECT.md §8 for the exact keyboard layout of each command flow.

Callback_data conventions:
    "menu:<action>"       — main menu buttons
    "price:appid:<id>"    — game selected from search results
    "wish:add:<appid>"    — add game to wishlist from result card
    "wish:remove:<appid>" — remove game from wishlist
    "region:<cc>"         — region picker button
    "dlc:<appid>"         — show DLCs for a game
    "compare:<appid>"     — compare regions
    "noop"                — placeholder (e.g. "Steam Page" is a URL button, not callback)
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ── Main menu (/start) ───────────────────────────────────────────────────────

MAIN_MENU_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🔍 Search Game Price", callback_data="menu:price")],
        [InlineKeyboardButton("🔑 TF2 Key / Ticket Price", callback_data="menu:tf2")],
        [InlineKeyboardButton("📋 My Wishlist", callback_data="menu:wishlist")],
        [InlineKeyboardButton("⚙️ Settings (Region)", callback_data="menu:region")],
    ]
)


# ── Search results (from /price or menu:price) ──────────────────────────────


def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    """Build an inline keyboard from storesearch results.

    Each result is ``{"appid": int, "name": str}``.
    One button per row so long game names aren't truncated.
    """
    rows = [
        [InlineKeyboardButton(r["name"], callback_data=f"price:appid:{r['appid']}")]
        for r in results
    ]
    return InlineKeyboardMarkup(rows)


# ── Result card (after selecting a game) ─────────────────────────────────────


def result_card_keyboard(
    appid: int, steam_url: str
) -> InlineKeyboardMarkup:
    """Buttons shown under a game's price result card.

    Args:
        appid:     Steam appid for wishlist-add and DLC callbacks.
        steam_url: Full URL to the game's Steam store page.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add to Wishlist", callback_data=f"wish:add:{appid}"),
                InlineKeyboardButton("🌍 Compare Regions", callback_data=f"compare:{appid}"),
            ],
            [
                InlineKeyboardButton("📦 Show DLCs", callback_data=f"dlc:{appid}"),
                InlineKeyboardButton("🔗 Steam Page", url=steam_url),
            ],
            [
                InlineKeyboardButton("⬅️ Home", callback_data="menu:main"),
            ],
        ]
    )


# ── Region picker (/region) ──────────────────────────────────────────────────

# Human-friendly names for the five picker regions + their cc.
_PICKER_REGIONS = [
    ("US", "🇺🇸 USD"),
    ("TR", "🇹🇷 TRY"),
    ("UA", "🇺🇦 UAH"),
    ("AR", "🇦🇷 ARS"),
    ("CN", "🇨🇳 CNY"),
]


def region_picker_keyboard() -> InlineKeyboardMarkup:
    """Grid of common regions + an 'Other' button for manual entry.

    Layout: one row per region (compact), with "Other" at the bottom.
    """
    rows = [
        [InlineKeyboardButton(label, callback_data=f"region:{cc}")]
        for cc, label in _PICKER_REGIONS
    ]
    rows.append([InlineKeyboardButton("✏️ Other (type code)", callback_data="region:manual")])
    return InlineKeyboardMarkup(rows)


# ── Wishlist remove picker ───────────────────────────────────────────────────


def wishlist_remove_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    """Buttons for each wishlisted game, tapping removes it.

    Each item is ``{"appid": int, "game_name": str}``.
    """
    rows = [
        [InlineKeyboardButton(
            f"🗑️ {item['game_name']}",
            callback_data=f"wish:remove:{item['appid']}",
        )]
        for item in items
    ]
    return InlineKeyboardMarkup(rows)


# ── Back-to-menu button ──────────────────────────────────────────────────────

BACK_TO_MENU_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main")]]
)
