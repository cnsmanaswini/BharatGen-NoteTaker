"""URL helpers for logs: meeting links may carry account/tracking parameters."""
from urllib.parse import urlsplit, urlunsplit


def redact_meeting_url(url: str | None) -> str:
    """Return `url` without query string or fragment.

    `https://meet.google.com/abc-defg-hij?authuser=0&hs=179` becomes
    `https://meet.google.com/abc-defg-hij`.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    if not parts.scheme or not parts.netloc:
        return url.split("?", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
