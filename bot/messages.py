"""Message templates: functions returning formatted text for each command/flow.

Every handler imports its messages from here instead of building strings inline.
This keeps formatting consistent and makes wording changes painless.

See PROJECT.md §8 for the exact layout of each command's output.
"""

# ── /start & /help ───────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🎮 <b>Steam Deal Bot</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "\n"
    "Hey, <b>{name}</b>! 👋\n"
    "\n"
    "I track Steam prices, TF2 market values,\n"
    "and alert you when games go on sale.\n"
    "\n"
    "Choose an action below 👇"
)

HELP_TEXT = (
    "\U0001f4d6 <b>Steam Deal Bot \u2014 Commands</b>\n"
    "\n"
    "<b>/price</b> &lt;game name&gt;\n"
    "  Search for a game and see its price in your saved region.\n"
    "  Shows game cover art, price, discounts, and TF2 key/ticket equivalents.\n"
    "  Example: <code>/price elden ring</code>\n"
    "\n"
    "<b>/tf2</b>\n"
    "  Show live Mann Co. Key and Tour of Duty Ticket prices\n"
    "  (with 15% Steam commission already subtracted).\n"
    "\n"
    "<b>/convert</b> &lt;amount&gt; [key|keys|ticket|tickets]\n"
    "  Convert between keys/tickets and currency.\n"
    "  Examples:\n"
    "    <code>/convert 5 keys</code> \u2192 currency value of 5 keys\n"
    "    <code>/convert 20 tickets</code> \u2192 currency value of 20 tickets\n"
    "    <code>/convert 20</code> \u2192 how many keys for that amount\n"
    "\n"
    "<b>/wishlist</b> [add|remove|summary]\n"
    "  Manage your game wishlist. Get notified on price changes.\n"
    "    <code>/wishlist</code> \u2014 list all wishlisted games\n"
    "    <code>/wishlist add elden ring</code> \u2014 add a game\n"
    "    <code>/wishlist remove</code> \u2014 remove a game (interactive)\n"
    "    <code>/wishlist summary</code> \u2014 only show games on sale\n"
    "\n"
    "<b>/region</b>\n"
    "  Change your default region / currency.\n"
    "\n"
    "<b>/help</b>\n"
    "  Show this help message."
)


def _main_menu_text(username: str | None) -> str:
    """Welcome text personalised with the user's first name or 'there'."""
    name = username or "there"
    return WELCOME_TEXT.format(name=name)


# ── /price flow ──────────────────────────────────────────────────────────────

PRICE_ASK_NAME = "🔍 Send me a game name to search:"
PRICE_NO_RESULTS = "😕 No games found for <b>{term}</b>. Try a different name."
PRICE_ERROR = "⚠️ Couldn't fetch prices right now. Please try again later."

PRICE_RESULT_CARD = (
    "🎮 <b>{name}</b>\n"
    "\n"
    "{price_line}\n"
    "{keys_line}"
)

PRICE_RESULT_FREE = "🆓 <b>Free to Play</b>"
PRICE_RESULT_PRICE = "💰 Price: <b>{final}</b>{discount_extra}"
PRICE_RESULT_DISCOUNT_EXTRA = "  (was {initial}, <b>−{pct}%</b>)"
PRICE_RESULT_NOT_PURCHASABLE = "⚠️ Not purchasable in your region."
PRICE_RESULT_KEYS = "🔑 Keys needed: <b>~{keys}</b>  (≈{tickets} tickets)\n"


# ── /tf2 flow ────────────────────────────────────────────────────────────────

TF2_TEMPLATE = (
    "🔑 <b>Mann Co. Key</b>\n"
    "  Market price: <b>{key_price}</b>\n"
    "  You receive after sale: <b>{key_net}</b> (85%)\n"
    "\n"
    "🎫 <b>Tour of Duty Ticket</b>\n"
    "  Market price: <b>{ticket_price}</b>\n"
    "  You receive after sale: <b>{ticket_net}</b> (85%)\n"
)
TF2_ERROR = "⚠️ Couldn't fetch TF2 prices right now. Please try again later."


# ── /convert flow ────────────────────────────────────────────────────────────

CONVERT_RESULT = "💱 <b>{amount} {item}</b> = <b>{result}</b>"
CONVERT_ERROR = "⚠️ Couldn't fetch prices for conversion. Please try again later."
CONVERT_USAGE = (
    "Usage:\n"
    "  <code>/convert 5 keys</code> → value of 5 keys in your currency\n"
    "  <code>/convert 20 tickets</code> → value of 20 tickets\n"
    "  <code>/convert 20</code> → how many keys for that amount"
)


# ── /wishlist flow ───────────────────────────────────────────────────────────

WISHLIST_EMPTY = "📋 Your wishlist is empty.\nAdd games with <b>/wishlist add &lt;game&gt;</b> or via /price."
WISHLIST_HEADER = "📋 <b>Your Wishlist ({count} games):</b>\n"
WISHLIST_ITEM = "  • <b>{name}</b> — {price_info}\n"
WISHLIST_ITEM_SALE = "🔥 <b>{name}</b> — <s>{initial}</s> → <b>{final}</b> (−{pct}%)\n"
WISHLIST_ADDED = "\u2705 <b>{name}</b> added to your wishlist."
WISHLIST_REMOVED = "\U0001f5d1\ufe0f <b>{name}</b> removed from your wishlist."
WISHLIST_ALREADY_EXISTS = "\u2139\ufe0f <b>{name}</b> is already in your wishlist."
WISHLIST_SUMMARY_EMPTY = "📋 No wishlisted games are currently on sale."
WISHLIST_SUMMARY_HEADER = "🔥 <b>Games on sale in your wishlist ({count}):</b>\n"
WISHLIST_ERROR = "⚠️ Couldn't load your wishlist right now. Please try again later."
WISHLIST_REFRESHING = "🔄 Refreshing your prices\u2026"


# ── /region flow ─────────────────────────────────────────────────────────────

REGION_CURRENT = "⚙️ Your current region: <b>{cc}</b> ({currency_name}).\nPick a new one:"
REGION_CHANGED = "✅ Region changed to <b>{cc}</b>. Currency: <b>{currency_name}</b>."
REGION_INVALID = "⚠️ <b>{cc}</b> is not a recognized region code. Try again or pick from the buttons."
REGION_PROMPT_MANUAL = "✏️ Send me a 2-letter country code (e.g. <code>JP</code>, <code>BR</code>):"


# ── /wishlist add (used inside /price flow too) ─────────────────────────────

WISHLIST_ADD_ASK = "🔍 Send me a game name to add to your wishlist:"


# ── Error templates ──────────────────────────────────────────────────────────

GENERIC_ERROR = "⚠️ Something went wrong. Please try again."


# ── Inline mode ──────────────────────────────────────────────────────────────

INLINE_PRICE_CARD = (
    "🎮 <b>{name}</b>\n"
    "\n"
    "{price_line}\n"
    "{keys_line}"
)
