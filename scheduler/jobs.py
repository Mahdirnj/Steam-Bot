"""PTB JobQueue task: check_all_wishlists() — polls wishlists for price changes
every 6h, notifies on any change, throttled to respect Steam rate limits.

Implemented in build step 11. See PROJECT.md §8 (background job), §10 step 11.
"""
