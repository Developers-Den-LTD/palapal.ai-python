import re

import phonenumbers
from phonenumbers import NumberParseException

from services.logger_services import logger
from services.social_media_scrapper.tone_analysis import build_tone_analysis
from services.technical_foundation import (
    FALLBACK_PHONE_REGIONS,
    _addresses_match,
    _normalize_address,
)

LOG_TAG = "socialmedia [consistency]"


def _normalize_name(value: str) -> str:
    """Strip spaces, commas, punctuation, etc. and compare lowercase letters/digits only."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _raw_name_value(platform: str, platform_data: dict | None) -> str | None:
    if platform_data is None:
        return None

    if platform == "instagram":
        profile = platform_data.get("profile") or {}
        name = (profile.get("name") or "").strip()
        if name:
            return name
        username = (platform_data.get("username") or "").strip()
        if not username:
            username = (profile.get("username") or "").strip()
        return username or None

    if platform == "facebook":
        return ((platform_data.get("profile") or {}).get("name") or "").strip() or None

    if platform == "twitter":
        username = ((platform_data.get("profile") or {}).get("username") or "").strip()
        if not username:
            username = (platform_data.get("username") or "").strip()
        return username or None

    return None


def _raw_address_value(platform_data: dict | None) -> str | None:
    if platform_data is None:
        return None
    return ((platform_data.get("profile") or {}).get("address") or "").strip() or None


def _raw_phone_values(platform_data: dict | None) -> list[str]:
    if platform_data is None:
        return []
    phones = list((platform_data.get("contact") or {}).get("phones") or [])
    return [str(phone).strip() for phone in phones if str(phone).strip()]


def _platform_status(
    platform_data: dict | None,
    *,
    has_value: bool,
    applicable: bool = True,
) -> str:
    if not applicable:
        return "not_applicable"
    if platform_data is None:
        return "not_provided"
    if has_value:
        return "present"
    return "missing"


def _summarize_platform_statuses(statuses: dict[str, str]) -> dict:
    return {
        "platforms": statuses,
        "present_on": sorted(p for p, s in statuses.items() if s == "present"),
        "missing_on": sorted(p for p, s in statuses.items() if s == "missing"),
        "not_provided_on": sorted(p for p, s in statuses.items() if s == "not_provided"),
        "not_applicable_on": sorted(p for p, s in statuses.items() if s == "not_applicable"),
    }


def _check_result(*, success: bool, message: str, **fields) -> dict:
    return {"success": success, "message": message, **fields}


def _collect_name_data(
    instagram: dict | None,
    facebook: dict | None,
    twitter: dict | None,
) -> tuple[dict[str, str], dict]:
    platforms = {"instagram": instagram, "facebook": facebook, "twitter": twitter}
    values: dict[str, str] = {}
    statuses: dict[str, str] = {}

    for platform, data in platforms.items():
        raw = _raw_name_value(platform, data)
        statuses[platform] = _platform_status(data, has_value=bool(raw))
        if raw:
            values[platform] = raw

    return values, _summarize_platform_statuses(statuses)


def _collect_address_data(
    instagram: dict | None,
    facebook: dict | None,
) -> tuple[dict[str, str | None], dict]:
    platforms = {"instagram": instagram, "facebook": facebook, "twitter": None}
    values: dict[str, str | None] = {}
    statuses: dict[str, str] = {}

    for platform, data in platforms.items():
        applicable = platform != "twitter"
        raw = _raw_address_value(data) if applicable else None
        statuses[platform] = _platform_status(
            data, has_value=bool(raw), applicable=applicable
        )
        if data is not None and applicable:
            values[platform] = raw

    return values, _summarize_platform_statuses(statuses)


def _collect_phone_data(
    instagram: dict | None,
    facebook: dict | None,
) -> tuple[dict[str, list[str]], dict]:
    platforms = {"instagram": instagram, "facebook": facebook, "twitter": None}
    values: dict[str, list[str]] = {}
    statuses: dict[str, str] = {}

    for platform, data in platforms.items():
        applicable = platform != "twitter"
        phones = _raw_phone_values(data) if applicable else []
        statuses[platform] = _platform_status(
            data, has_value=bool(phones), applicable=applicable
        )
        if data is not None and applicable:
            values[platform] = phones

    return values, _summarize_platform_statuses(statuses)


def _check_name_consistency(
    values: dict[str, str],
    presence: dict,
) -> dict:
    if presence["missing_on"]:
        return _check_result(
            success=False,
            values=values,
            normalized={p: _normalize_name(n) for p, n in values.items()},
            field_presence=presence,
            message=f"Name missing on {', '.join(presence['missing_on'])}.",
        )

    if len(values) < 2:
        return _check_result(
            success=True,
            values=values,
            normalized={},
            field_presence=presence,
            message="Not enough platforms with name data to compare.",
        )

    normalized = {platform: _normalize_name(name) for platform, name in values.items()}
    if any(not value for value in normalized.values()):
        return _check_result(
            success=False,
            values=values,
            normalized=normalized,
            field_presence=presence,
            message="One or more platforms have an empty name after normalization.",
        )

    success = len(set(normalized.values())) == 1
    message = (
        "Name is an exact match across platforms after removing "
        "spaces, commas, and punctuation."
        if success
        else "Name mismatch detected across Instagram, Facebook, and/or Twitter."
    )
    return _check_result(
        success=success,
        values=values,
        normalized=normalized,
        field_presence=presence,
        message=message,
    )


def _check_address_consistency(
    values: dict[str, str | None],
    presence: dict,
) -> dict:
    if presence["missing_on"]:
        missing = ", ".join(presence["missing_on"])
        present = ", ".join(presence["present_on"]) or "none"
        return _check_result(
            success=False,
            values=values,
            field_presence=presence,
            message=f"Address missing on {missing}. Address present on {present}.",
        )

    if len(values) < 2:
        return _check_result(
            success=True,
            values=values,
            field_presence=presence,
            message="Not enough platforms with address data to compare.",
        )

    present = {platform: address for platform, address in values.items() if address}
    if not present:
        return _check_result(
            success=True,
            values=values,
            field_presence=presence,
            message="No address found on Instagram or Facebook.",
        )

    addresses = list(present.values())
    normalized = {p: _normalize_address(a) for p, a in present.items()}
    success = _addresses_match(addresses)
    message = (
        "Address is consistent between Instagram and Facebook."
        if success
        else "Address mismatch detected between Instagram and Facebook."
    )
    return _check_result(
        success=success,
        values=values,
        normalized=normalized,
        field_presence=presence,
        message=message,
    )


def _normalize_phone_e164(phone: str, region_hint: str | None = None) -> str | None:
    phone_text = str(phone or "").strip()
    if not phone_text:
        return None

    if phone_text.startswith("+"):
        try:
            parsed = phonenumbers.parse(phone_text, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except NumberParseException:
            pass

    regions = [region_hint] if region_hint else []
    regions.extend(r for r in FALLBACK_PHONE_REGIONS if r not in regions)

    for region in regions:
        if not region:
            continue
        try:
            parsed = phonenumbers.parse(phone_text, region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except NumberParseException:
            continue

    return None


def _detect_region_from_phone_list(phones: list[str]) -> str | None:
    for phone in phones:
        phone_text = str(phone or "").strip()
        if not phone_text.startswith("+"):
            continue
        try:
            parsed = phonenumbers.parse(phone_text, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.region_code_for_number(parsed)
        except NumberParseException:
            continue
    return None


def _normalize_phone_entries(
    phones: list[str],
    region_hint: str | None = None,
) -> dict[str, str]:
    return {
        phone: e164
        for phone in phones
        if (e164 := _normalize_phone_e164(phone, region_hint=region_hint))
    }


def _check_phone_consistency(
    values: dict[str, list[str]],
    presence: dict,
) -> dict:
    empty_match = {
        "matching_numbers": [],
        "instagram_only": values.get("instagram", []),
        "facebook_only": values.get("facebook", []),
    }

    if presence["missing_on"]:
        missing = ", ".join(presence["missing_on"])
        present = ", ".join(presence["present_on"]) or "none"
        return _check_result(
            success=False,
            values=values,
            field_presence=presence,
            message=f"Phone number missing on {missing}. Phone present on {present}.",
            **empty_match,
        )

    if len(values) < 2:
        return _check_result(
            success=True,
            values=values,
            field_presence=presence,
            message="Not enough platforms with phone data to compare.",
            **empty_match,
        )

    instagram_phones = values.get("instagram", [])
    facebook_phones = values.get("facebook", [])

    if not instagram_phones and not facebook_phones:
        return _check_result(
            success=True,
            values=values,
            field_presence=presence,
            message="No phone numbers found on Instagram or Facebook.",
            matching_numbers=[],
            instagram_only=[],
            facebook_only=[],
        )

    if not instagram_phones or not facebook_phones:
        missing = "Instagram" if not instagram_phones else "Facebook"
        return _check_result(
            success=False,
            values=values,
            field_presence=presence,
            message=f"Phone number missing on {missing}.",
            matching_numbers=[],
            instagram_only=instagram_phones,
            facebook_only=facebook_phones,
        )

    region_hint = _detect_region_from_phone_list(instagram_phones + facebook_phones)
    instagram_normalized = _normalize_phone_entries(instagram_phones, region_hint)
    facebook_normalized = _normalize_phone_entries(facebook_phones, region_hint)

    instagram_e164 = set(instagram_normalized.values())
    facebook_e164 = set(facebook_normalized.values())

    matching_numbers = [
        {
            "normalized": e164,
            "instagram": [
                raw for raw, norm in instagram_normalized.items() if norm == e164
            ],
            "facebook": [
                raw for raw, norm in facebook_normalized.items() if norm == e164
            ],
        }
        for e164 in sorted(instagram_e164 & facebook_e164)
    ]

    instagram_only = [
        phone
        for phone in instagram_phones
        if instagram_normalized.get(phone) not in facebook_e164
    ]
    facebook_only = [
        phone
        for phone in facebook_phones
        if facebook_normalized.get(phone) not in instagram_e164
    ]

    success = bool(matching_numbers)
    message = (
        "At least one phone number is consistent between Instagram and Facebook."
        if success
        else "No matching phone numbers found between Instagram and Facebook."
    )
    return _check_result(
        success=success,
        values=values,
        matching_numbers=matching_numbers,
        instagram_only=instagram_only,
        facebook_only=facebook_only,
        field_presence=presence,
        message=message,
    )


def _platform_entry(*, consistent: bool, value: str | list[str] | None) -> dict:
    return {"consistent": consistent, "value": value}


def _build_name_response(values: dict[str, str]) -> dict:
    normalized = {p: _normalize_name(n) for p, n in values.items()}
    response: dict[str, dict] = {}

    for platform in ("instagram", "facebook", "twitter"):
        raw = values.get(platform)
        if not raw:
            response[platform] = _platform_entry(consistent=False, value=None)
            continue

        norm = normalized[platform]
        others = {p: v for p, v in normalized.items() if p != platform and v}
        if not others:
            consistent = True
        else:
            consistent = all(norm == other for other in others.values())

        response[platform] = _platform_entry(consistent=consistent, value=raw)

    return response


def _build_address_response(
    values: dict[str, str | None],
    address_result: dict,
) -> dict:
    response: dict[str, dict] = {}
    overall_ok = address_result["success"]

    for platform in ("instagram", "facebook"):
        raw = values.get(platform)
        if not raw:
            response[platform] = _platform_entry(consistent=False, value=None)
        else:
            response[platform] = _platform_entry(consistent=overall_ok, value=raw)

    return response


def _build_phone_response(
    values: dict[str, list[str]],
    phone_result: dict,
) -> dict:
    matching_e164 = {
        entry["normalized"]
        for entry in phone_result.get("matching_numbers", [])
    }

    region_hint = _detect_region_from_phone_list(
        values.get("instagram", []) + values.get("facebook", [])
    )
    instagram_normalized = _normalize_phone_entries(
        values.get("instagram", []), region_hint
    )
    facebook_normalized = _normalize_phone_entries(
        values.get("facebook", []), region_hint
    )

    response: dict[str, dict] = {}

    for platform, normalized_map in (
        ("instagram", instagram_normalized),
        ("facebook", facebook_normalized),
    ):
        phones = values.get(platform, [])
        if not phones:
            response[platform] = _platform_entry(consistent=False, value=None)
            continue

        platform_e164 = set(normalized_map.values())
        has_match = bool(platform_e164 & matching_e164) if matching_e164 else False

        if has_match:
            matched_raw = [
                raw
                for raw, e164 in normalized_map.items()
                if e164 in matching_e164
            ]
            value: str | list[str] = (
                matched_raw[0] if len(matched_raw) == 1 else matched_raw
            )
        else:
            value = phones[0] if len(phones) == 1 else phones

        response[platform] = _platform_entry(consistent=has_match, value=value)

    return response


async def check_social_media_consistency(scrape_result: dict) -> dict:
    """Compare NAME, ADDRESS, and PHONE across scraped social platforms, and analyse tone."""
    instagram = scrape_result.get("instagram")
    facebook = scrape_result.get("facebook")
    twitter = scrape_result.get("twitter")

    name_values, name_presence = _collect_name_data(instagram, facebook, twitter)
    address_values, address_presence = _collect_address_data(instagram, facebook)
    phone_values, phone_presence = _collect_phone_data(instagram, facebook)

    name_result = _check_name_consistency(name_values, name_presence)
    address_result = _check_address_consistency(address_values, address_presence)
    phone_result = _check_phone_consistency(phone_values, phone_presence)

    overall_success = (
        name_result["success"]
        and address_result["success"]
        and phone_result["success"]
    )

    tone_analysis = await build_tone_analysis(instagram, facebook, twitter)

    logger.info(
        f"{LOG_TAG}: overall={overall_success}, "
        f"name={name_result['success']}, "
        f"address={address_result['success']}, "
        f"phone={phone_result['success']}"
    )

    return {
        "Name": _build_name_response(name_values),
        "Address": _build_address_response(address_values, address_result),
        "Phone": _build_phone_response(phone_values, phone_result),
        "tone_analysis": tone_analysis,
    }
