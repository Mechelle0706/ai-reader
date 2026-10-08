"""Shared application exceptions used by content fetchers."""


class FetchError(Exception):
    """Raised when a source cannot be fetched or parsed into usable content."""
