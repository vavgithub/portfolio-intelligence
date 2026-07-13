"""Parse portfolio URL fields from batch JSON (handles multi-link strings)."""

from __future__ import annotations

import re

BEHANCE_URL_RE = re.compile(
    r"https?://(?:www\.)?behance\.net/\S+",
    re.IGNORECASE,
)
_MULTI_LINK_RE = re.compile(
    r"(?:\s[–-]\s|\bInstagram\b|\bPortfolio\s*:|https?://.*https?://)",
    re.IGNORECASE,
)


def looks_multi_link(portfolio: str) -> bool:
    """True when the raw field looks like a label + multiple links, not a single URL."""
    if not portfolio:
        return False
    if len(re.findall(r"https?://", portfolio, flags=re.IGNORECASE)) >= 2:
        return True
    return bool(_MULTI_LINK_RE.search(portfolio))


def extract_behance_url(portfolio: str) -> str | None:
    match = BEHANCE_URL_RE.search(portfolio)
    if not match:
        return None
    return match.group(0).rstrip(".,;)")


def normalize_portfolio_url(portfolio: str) -> tuple[str, str | None]:
    """
    Normalize a portfolio field to a single URL.

    Returns:
        (url, error) where error is None on success, or:
        - "empty" for blank input
        - "invalid_link" for multi-link strings with no Behance URL
    """
    if not portfolio or not isinstance(portfolio, str):
        return "", "empty"

    raw = portfolio.strip()
    if not raw:
        return "", "empty"

    if looks_multi_link(raw):
        behance = extract_behance_url(raw)
        if not behance:
            return "", "invalid_link"
        return behance, None

    token = raw.split()[0].strip()
    if token.startswith("http://") or token.startswith("https://"):
        return token, None
    if re.match(r"(?:www\.)?behance\.net/", token, re.IGNORECASE) or token.startswith("www."):
        return "https://" + token.lstrip("/"), None
    return "https://" + token.lstrip("/"), None


def normalize_url(portfolio: str) -> str:
    """Backward-compatible helper: URL string, or empty if empty/invalid."""
    url, _err = normalize_portfolio_url(portfolio)
    return url
