import re

_YELP_DOMAIN_RE = re.compile(
    r"^https?://(?:www\.|m\.)?yelp\.[a-z.]+\b",
    flags=re.IGNORECASE,
)
_TRIPADVISOR_DOMAIN_RE = re.compile(
    r"^https?://(?:www\.|m\.)?tripadvisor\.[a-z.]+\b",
    flags=re.IGNORECASE,
)


def normalize_yelp_url(url: str | None) -> str | None:
    """Convert regional Yelp domains (e.g. yelp.co.uk) to www.yelp.com."""
    if not url or not str(url).strip():
        return url
    return _YELP_DOMAIN_RE.sub("https://www.yelp.com", url.strip())


def normalize_tripadvisor_url(url: str | None) -> str | None:
    """Convert regional TripAdvisor domains (e.g. tripadvisor.co.uk) to www.tripadvisor.com."""
    if not url or not str(url).strip():
        return url
    return _TRIPADVISOR_DOMAIN_RE.sub("https://www.tripadvisor.com", url.strip())
