"""Shared utilities for bot handlers."""
import re

# Steam store URL patterns we support:
# https://store.steampowered.com/app/1174180/Red_Dead_Redemption_2/
# https://store.steampowered.com/app/1174180/
# http://store.steampowered.com/app/1174180
# store.steampowered.com/app/1174180/
STEAM_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?store\.steampowered\.com/app/(\d+)",
    re.IGNORECASE,
)


def extract_appid_from_text(text: str) -> int | None:
    """Extract a Steam appid from a Steam store URL in the text.

    Returns the appid (int) if found, None otherwise.
    Matches URLs like:
        https://store.steampowered.com/app/1174180/Red_Dead_Redemption_2/
        store.steampowered.com/app/1174180
        https://store.steampowered.com/app/12345/
    """
    match = STEAM_URL_PATTERN.search(text)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            return None
    return None
