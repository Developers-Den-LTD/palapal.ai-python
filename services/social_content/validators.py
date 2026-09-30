"""
Pure caption checks for AI social content.

Each check returns a list of {"rule", "detail"} violations; nothing rewrites text.
The generate pipeline runs them per platform, sends failing platforms to one
repair call, and returns whatever still fails as that platform's warnings.
"""

import re

import regex

from schema.social_content_schema import SocialContentGuidelines
from services.social_content.constants import (
    EMOJI_CAPS,
    PLATFORM_SPECS,
    X_TARGET_CHARS,
    X_URL_WEIGHT,
)

_EMOJI_CLUSTER = regex.compile(r"\X")
# Flags are pairs of regional indicators, which are not Extended_Pictographic.
_PICTOGRAPHIC = regex.compile(r"[\p{Extended_Pictographic}\p{Regional_Indicator}]")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_INLINE_HASHTAG = re.compile(r"#(\w+)")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_PRICE = re.compile(
    r"[$£€¥₹]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:usd|gbp|eur|pkr|rs|aed)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(
    # Dots only with a year, so decimals like 12.50 are not read as dates.
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"
    r"|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_WORD}\b"
    rf"|\b{_MONTH_WORD}\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_MONTH_TOKEN = re.compile(_MONTH_WORD, re.IGNORECASE)


def _violation(rule: str, detail: str) -> dict:
    return {"rule": rule, "detail": detail}


def count_emoji(text: str) -> int:
    """Count grapheme clusters containing an emoji character (a ZWJ family or a flag counts once)."""
    return sum(
        1 for cluster in _EMOJI_CLUSTER.findall(text or "") if _PICTOGRAPHIC.search(cluster)
    )


def _x_char_weight(char: str) -> int:
    code = ord(char)
    if (
        code <= 4351
        or 8192 <= code <= 8205
        or 8208 <= code <= 8223
        or 8242 <= code <= 8247
    ):
        return 1
    return 2


def x_weighted_length(text: str) -> int:
    """X/Twitter weighted length: a URL counts 23, characters outside Latin ranges count 2."""
    text = text or ""
    total = 0
    position = 0
    for match in _URL.finditer(text):
        total += sum(_x_char_weight(char) for char in text[position : match.start()])
        total += X_URL_WEIGHT
        position = match.end()
    total += sum(_x_char_weight(char) for char in text[position:])
    return total


def compose_post_text(text: str, hashtags: list[str]) -> str:
    """The text as it will be posted: caption, then the hashtag line."""
    tags = " ".join(f"#{tag}" for tag in hashtags)
    if not tags:
        return text or ""
    return f"{text or ''}\n\n{tags}"


def _normalize_tag(tag: str) -> str:
    return str(tag or "").lstrip("#").strip().lower()


def check_length(platform: str, text: str, hashtags: list[str], title: str | None) -> list[dict]:
    spec = PLATFORM_SPECS[platform]
    violations: list[dict] = []
    full_text = compose_post_text(text, hashtags)

    if platform == "x":
        weighted = x_weighted_length(full_text)
        if weighted > X_TARGET_CHARS:
            violations.append(
                _violation("length", f"{weighted} weighted characters, target is {X_TARGET_CHARS}")
            )
    elif len(full_text) > spec["max_chars"]:
        violations.append(
            _violation("length", f"{len(full_text)} characters, limit is {spec['max_chars']}")
        )

    title_limit = spec["title_max_chars"]
    if title_limit and title and len(title) > title_limit:
        violations.append(
            _violation("title_length", f"{len(title)} characters, limit is {title_limit}")
        )
    return violations


def hashtag_cap(platform: str, guidelines: SocialContentGuidelines | None) -> int:
    platform_cap = PLATFORM_SPECS[platform]["max_hashtags"]
    preferences = guidelines.hashtags if guidelines else None
    if preferences and preferences.max_count is not None:
        return min(preferences.max_count, platform_cap)
    return platform_cap


def check_hashtags(
    platform: str,
    text: str,
    hashtags: list[str],
    guidelines: SocialContentGuidelines | None,
) -> list[dict]:
    violations: list[dict] = []
    all_tags = {_normalize_tag(tag) for tag in hashtags} | {
        tag.lower() for tag in _INLINE_HASHTAG.findall(text or "")
    }
    all_tags.discard("")
    cap = hashtag_cap(platform, guidelines)

    if len(all_tags) > cap:
        violations.append(_violation("hashtag_count", f"{len(all_tags)} hashtags, limit is {cap}"))

    preferences = guidelines.hashtags if guidelines else None
    if not preferences:
        return violations

    if cap > 0:
        for tag in preferences.always_include:
            if _normalize_tag(tag) not in all_tags:
                violations.append(_violation("hashtag_required", f"#{tag} is missing"))
    for tag in preferences.never_include:
        if _normalize_tag(tag) in all_tags:
            violations.append(_violation("hashtag_banned", f"#{tag} is not allowed"))
    return violations


def check_emoji(text: str, guidelines: SocialContentGuidelines | None) -> list[dict]:
    usage = (guidelines.emoji_usage if guidelines else None) or "minimal"
    cap = EMOJI_CAPS[usage]
    if cap is None:
        return []
    count = count_emoji(text)
    if count > cap:
        return [_violation("emoji", f"{count} emoji, limit is {cap} ({usage})")]
    return []


def check_restricted(text: str, hashtags: list[str], words: list[str]) -> list[dict]:
    violations: list[dict] = []
    lowered_tags = [_normalize_tag(tag) for tag in hashtags] + [
        tag.lower() for tag in _INLINE_HASHTAG.findall(text or "")
    ]
    for word in words:
        needle = word.strip().lower()
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text or "", re.IGNORECASE):
            violations.append(_violation("restricted_word", f"'{word}' appears in the caption"))
            continue
        compact = needle.replace(" ", "")
        if any(compact in tag for tag in lowered_tags):
            violations.append(_violation("restricted_word", f"'{word}' appears in a hashtag"))
    return violations


def check_required_terms(text: str, terms: list[str]) -> list[dict]:
    lowered = (text or "").lower()
    return [
        _violation("required_term", f"'{term}' is missing")
        for term in terms
        if term.strip() and term.strip().lower() not in lowered
    ]


def check_cta(text: str, cta: str | None, guidelines: SocialContentGuidelines | None) -> list[dict]:
    preferences = guidelines.cta if guidelines else None
    if not preferences or not preferences.required:
        return []
    if cta and cta.strip():
        return []
    lowered = (text or "").lower()
    if any(phrase.lower() in lowered for phrase in preferences.preferred):
        return []
    return [_violation("cta", "A call to action is required")]


def _numbers_in(text: str) -> set[str]:
    numbers = {
        match.replace(",", "").lstrip("0") or "0" for match in _NUMBER.findall(text or "")
    }
    for match in _MONTH_TOKEN.findall(text or ""):
        numbers.add(str(_MONTHS[match[:3].lower()]))
    return {number[:-2] if number.endswith(".0") else number for number in numbers}


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def check_invented_facts(text: str, allowed_text: str) -> list[dict]:
    """Prices, percentages, phones, URLs and dates must come from the given facts, brief or profile."""
    violations: list[dict] = []
    allowed_lower = (allowed_text or "").lower()
    allowed_numbers = _numbers_in(allowed_text)
    allowed_digit_runs = {_digits(match) for match in _PHONE.findall(allowed_text or "")}

    for url in _URL.findall(text or ""):
        cleaned = url.rstrip(".,!?)").lower()
        if cleaned.rstrip("/") not in allowed_lower:
            violations.append(_violation("invented_fact", f"URL '{cleaned}' was not given"))

    text_without_urls = _URL.sub(" ", text or "")
    for phone in _PHONE.findall(text_without_urls):
        digits = _digits(phone)
        if len(digits) >= 7 and digits not in allowed_digit_runs:
            violations.append(_violation("invented_fact", f"Phone '{phone.strip()}' was not given"))

    for pattern, label in ((_PRICE, "Price"), (_PERCENT, "Percentage"), (_DATE, "Date")):
        for match in pattern.findall(text_without_urls):
            if not _numbers_in(match) <= allowed_numbers:
                violations.append(_violation("invented_fact", f"{label} '{match.strip()}' was not given"))
    return violations


def validate_caption(
    platform: str,
    caption: dict,
    guidelines: SocialContentGuidelines | None,
    allowed_text: str,
) -> list[dict]:
    text = caption.get("text") or ""
    hashtags = caption.get("hashtags") or []
    restricted = guidelines.restricted_words if guidelines else []
    required = guidelines.required_terms if guidelines else []
    return (
        check_length(platform, text, hashtags, caption.get("title"))
        + check_hashtags(platform, text, hashtags, guidelines)
        + check_emoji(text, guidelines)
        + check_restricted(text, hashtags, restricted)
        + check_required_terms(text, required)
        + check_cta(text, caption.get("cta"), guidelines)
        + check_invented_facts(text, allowed_text)
    )


def to_camel_hashtag(phrase: str) -> str:
    """'best pizza in leeds' -> 'BestPizzaInLeeds'; drops characters hashtags cannot hold."""
    words = re.findall(r"\w+", phrase or "")
    return "".join(word[:1].upper() + word[1:] for word in words)


def build_hashtag_candidates(
    keywords: list[str],
    location: str,
    brand_hashtags: list[str],
    guidelines: SocialContentGuidelines | None,
) -> list[str]:
    preferences = guidelines.hashtags if guidelines else None
    always = list(preferences.always_include) if preferences else []
    banned = {_normalize_tag(tag) for tag in (preferences.never_include if preferences else [])}
    restricted = [word.strip().lower().replace(" ", "") for word in (guidelines.restricted_words if guidelines else [])]

    raw = always + list(brand_hashtags) + [to_camel_hashtag(location)] + [
        to_camel_hashtag(keyword) for keyword in keywords
    ]
    candidates: list[str] = []
    seen: set[str] = set()
    for tag in raw:
        tag = tag.lstrip("#").strip()
        key = tag.lower()
        if not tag or key in seen or key in banned:
            continue
        if any(word and word in key for word in restricted):
            continue
        seen.add(key)
        candidates.append(tag)
    return candidates
