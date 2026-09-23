"""
Compare Keyword SEO results for a primary business vs competitors.

Loads Result.json from S3 by business_id. Keywords may differ across businesses:
shared keywords are compared head-to-head; unique keywords are listed separately.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger_services import logger
from services.s3_service import fetch_keyword_seo_by_business_id

MAX_COMPARISON_WORKERS = 8
MAX_SEARCH_VISIBILITY_SCORE = 15


def _normalize_keyword(text: str) -> str:
    return " ".join((text or "").lower().split())


def _fetch_one(business_id: str) -> tuple[str, dict | None]:
    return business_id, fetch_keyword_seo_by_business_id(business_id)


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _attempt_public(attempt: dict) -> dict:
    """Public fields for one keyword attempt."""
    return {
        "keyword": str(attempt.get("keyword") or "").strip(),
        "priority": str(attempt.get("priority") or "").strip() or None,
        "found": bool(attempt.get("found")),
        "position": _safe_int(attempt.get("position")),
        "score": _safe_float(attempt.get("score")),
        "max_score": _safe_float(
            attempt.get("max_score"),
            float(MAX_SEARCH_VISIBILITY_SCORE),
        ),
        "band": str(attempt.get("band") or "").strip() or None,
        "url": attempt.get("url"),
        "title": attempt.get("title"),
    }


def _attempts_by_keyword(raw: dict) -> dict[str, dict]:
    mapped: dict[str, dict] = {}
    for attempt in raw.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        key = _normalize_keyword(str(attempt.get("keyword") or ""))
        if key and key not in mapped:
            mapped[key] = _attempt_public(attempt)
    return mapped


def _build_summary(raw: dict, attempts_map: dict[str, dict]) -> dict:
    attempts = list(attempts_map.values())
    checked = len(attempts)
    found_items = [a for a in attempts if a.get("found")]
    found_count = len(found_items)
    scores = [_safe_float(a.get("score")) for a in attempts]
    avg_score = round(sum(scores) / checked, 2) if checked else 0.0
    positions = [
        p for p in (_safe_int(a.get("position")) for a in found_items) if p is not None
    ]
    best_position = min(positions) if positions else None

    matched = raw.get("matched_keyword")
    matched_public = None
    if isinstance(matched, dict) and str(matched.get("keyword") or "").strip():
        matched_public = _attempt_public(matched)

    return {
        "keywords_checked": checked,
        "keywords_found": found_count,
        "found_rate_pct": round((found_count / checked) * 100, 1) if checked else 0.0,
        "avg_score": avg_score,
        "max_score": MAX_SEARCH_VISIBILITY_SCORE,
        "best_position": best_position,
        "matched_keyword": matched_public,
    }


def _summarize_entry(business_id: str, raw: dict | None) -> dict:
    if raw is None:
        return {
            "status": "not_found",
            "business_id": business_id,
            "message": (
                f"No Keyword SEO result found for business_id='{business_id}'"
            ),
        }

    attempts_map = _attempts_by_keyword(raw)
    return {
        "status": "success" if raw.get("status") != "error" else "error",
        "business_id": business_id,
        "business_name": str(raw.get("business_name") or "").strip() or None,
        "website_url": raw.get("website_url"),
        "country_code": raw.get("country_code"),
        "summary": _build_summary(raw, attempts_map),
        "attempts": list(attempts_map.values()),
        "_attempts_map": attempts_map,  # internal; stripped before response
    }


def _position_rank(position: int | None, found: bool) -> int:
    """Lower is better. Missing / not found sorts last."""
    if not found or position is None:
        return 10**9
    return int(position)


def _compare_shared(
    your_map: dict[str, dict],
    competitor_maps: dict[str, dict[str, dict]],
) -> tuple[list[dict], dict]:
    your_keys = set(your_map.keys())
    all_competitor_keys: set[str] = set()
    for cmap in competitor_maps.values():
        all_competitor_keys |= set(cmap.keys())

    shared_keys = sorted(your_keys & all_competitor_keys)

    shared_rows: list[dict] = []
    you_wins = 0
    competitor_wins = 0
    ties = 0

    for key in shared_keys:
        you_attempt = your_map[key]
        competitor_side: dict[str, dict] = {}
        best_comp_rank = 10**9
        best_comp_id: str | None = None

        for cid, cmap in competitor_maps.items():
            attempt = cmap.get(key)
            if attempt is None:
                continue
            competitor_side[cid] = attempt
            rank = _position_rank(attempt.get("position"), bool(attempt.get("found")))
            if rank < best_comp_rank:
                best_comp_rank = rank
                best_comp_id = cid

        you_rank = _position_rank(
            you_attempt.get("position"),
            bool(you_attempt.get("found")),
        )

        if you_rank < best_comp_rank:
            winner = "you"
            you_wins += 1
            position_gap = (
                None
                if best_comp_rank >= 10**9 or you_rank >= 10**9
                else best_comp_rank - you_rank
            )
        elif best_comp_rank < you_rank:
            winner = "competitor"
            competitor_wins += 1
            position_gap = (
                None
                if best_comp_rank >= 10**9 or you_rank >= 10**9
                else you_rank - best_comp_rank
            )
        else:
            # Both missing, or same position
            if you_rank >= 10**9 and best_comp_rank >= 10**9:
                winner = "none"
                position_gap = None
            else:
                winner = "tie"
                ties += 1
                position_gap = 0

        shared_rows.append(
            {
                "keyword": you_attempt.get("keyword") or key,
                "you": you_attempt,
                "competitors": competitor_side,
                "winner": winner,
                "best_competitor_id": best_comp_id if winner == "competitor" else None,
                "position_gap": position_gap,
            }
        )

    # Prefer better you positions first for UI readability
    shared_rows.sort(
        key=lambda row: (
            _position_rank(
                (row.get("you") or {}).get("position"),
                bool((row.get("you") or {}).get("found")),
            ),
            str(row.get("keyword") or "").lower(),
        )
    )

    totals = {
        "shared_count": len(shared_rows),
        "you_wins": you_wins,
        "competitor_wins": competitor_wins,
        "ties": ties,
    }
    return shared_rows, totals


def _unique_keyword_rows(
    source_map: dict[str, dict],
    exclude_keys: set[str],
) -> list[dict]:
    rows = [
        attempt
        for key, attempt in source_map.items()
        if key not in exclude_keys
    ]
    rows.sort(
        key=lambda a: (
            _position_rank(a.get("position"), bool(a.get("found"))),
            str(a.get("keyword") or "").lower(),
        )
    )
    return rows


def _severity_from_diff(your_metric: float, competitor_metric: float) -> str:
    """Higher metric is better (found_rate, avg_score, win counts)."""
    diff = competitor_metric - your_metric
    if diff <= 0:
        return "success"
    if diff <= 20:
        return "warning"
    return "danger"


def _generate_insights(
    you: dict,
    competitors: dict[str, dict],
    comparison: dict,
) -> list[dict]:
    insights: list[dict] = []
    valid_competitors = {
        cid: data
        for cid, data in competitors.items()
        if data.get("status") == "success"
    }

    you_summary = you.get("summary") or {}
    totals = comparison.get("totals") or {}
    shared_keywords = comparison.get("shared_keywords") or []
    you_only = comparison.get("you_only_keywords") or []
    competitor_only = comparison.get("competitor_only_keywords") or {}

    # 1. Shared keyword lead
    shared_count = int(totals.get("shared_count") or 0)
    you_wins = int(totals.get("you_wins") or 0)
    competitor_wins = int(totals.get("competitor_wins") or 0)

    if shared_count == 0:
        insights.append(
            {
                "type": "shared_keyword_lead",
                "title": "Shared Keyword Lead",
                "severity": "warning",
                "message": (
                    "No overlapping keywords between you and the selected competitors. "
                    "Align keyword tracking to enable head-to-head ranking comparison."
                ),
            }
        )
    else:
        if you_wins > competitor_wins:
            severity = "success"
            message = (
                f"You outrank competitors on {you_wins} of {shared_count} shared "
                f"keywords (competitors ahead on {competitor_wins})."
            )
        elif competitor_wins > you_wins:
            severity = "danger" if competitor_wins - you_wins >= 2 else "warning"
            message = (
                f"Competitors outrank you on {competitor_wins} of {shared_count} "
                f"shared keywords (you lead on {you_wins}). Focus on closing those gaps."
            )
        else:
            severity = "warning"
            message = (
                f"Shared keyword rankings are even ({you_wins} wins each across "
                f"{shared_count} overlapping terms)."
            )
        insights.append(
            {
                "type": "shared_keyword_lead",
                "title": "Shared Keyword Lead",
                "severity": severity,
                "message": message,
            }
        )

    # 2. Overall visibility (found rate + avg score)
    your_found_rate = _safe_float(you_summary.get("found_rate_pct"))
    your_avg_score = _safe_float(you_summary.get("avg_score"))

    if valid_competitors:
        best_cid = max(
            valid_competitors,
            key=lambda cid: _safe_float(
                (valid_competitors[cid].get("summary") or {}).get("found_rate_pct")
            ),
        )
        best_summary = valid_competitors[best_cid].get("summary") or {}
        best_found_rate = _safe_float(best_summary.get("found_rate_pct"))
        best_avg_score = _safe_float(best_summary.get("avg_score"))
        best_name = (
            str(valid_competitors[best_cid].get("business_name") or "").strip()
            or best_cid
        )

        severity = _severity_from_diff(your_found_rate, best_found_rate)
        if your_found_rate >= best_found_rate and your_avg_score >= best_avg_score:
            message = (
                f"Your overall visibility leads or matches the strongest competitor "
                f"({best_name}): found-rate {your_found_rate}% vs {best_found_rate}%, "
                f"avg score {your_avg_score}/{MAX_SEARCH_VISIBILITY_SCORE} vs "
                f"{best_avg_score}/{MAX_SEARCH_VISIBILITY_SCORE}."
            )
            severity = "success"
        else:
            message = (
                f"Visibility gap vs {best_name}: your found-rate {your_found_rate}% "
                f"(avg score {your_avg_score}) vs competitor {best_found_rate}% "
                f"(avg score {best_avg_score}). Expand coverage and improve rankings."
            )
    else:
        severity = "warning"
        message = (
            f"Your found-rate is {your_found_rate}% with avg score "
            f"{your_avg_score}/{MAX_SEARCH_VISIBILITY_SCORE}. "
            "No competitor Keyword SEO data available for comparison."
        )

    insights.append(
        {
            "type": "visibility_gap",
            "title": "Overall Visibility",
            "severity": severity,
            "message": message,
        }
    )

    # 3. Unique keyword opportunity (competitor-only strong ranks)
    strong_comp_uniques: list[tuple[str, dict]] = []
    for cid, rows in competitor_only.items():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            pos = _safe_int(row.get("position"))
            if row.get("found") and pos is not None and pos <= 3:
                strong_comp_uniques.append((cid, row))

    if strong_comp_uniques:
        strong_comp_uniques.sort(
            key=lambda item: _safe_int(item[1].get("position")) or 10**9
        )
        cid, row = strong_comp_uniques[0]
        comp_name = (
            str((competitors.get(cid) or {}).get("business_name") or "").strip()
            or cid
        )
        keyword = row.get("keyword") or "a competitor keyword"
        pos = row.get("position")
        insights.append(
            {
                "type": "unique_keyword_opportunity",
                "title": "Unique Keyword Opportunity",
                "severity": "danger",
                "message": (
                    f"{comp_name} ranks #{pos} for \"{keyword}\" — a term you are "
                    f"not tracking. Consider adding it to your keyword set "
                    f"({len(strong_comp_uniques)} strong competitor-only term(s) found)."
                ),
            }
        )
    elif any(competitor_only.values()):
        total_comp_only = sum(len(v or []) for v in competitor_only.values())
        insights.append(
            {
                "type": "unique_keyword_opportunity",
                "title": "Unique Keyword Opportunity",
                "severity": "warning",
                "message": (
                    f"Competitors track {total_comp_only} keyword(s) you do not. "
                    "Review competitor-only terms to close coverage gaps."
                ),
            }
        )
    elif you_only:
        insights.append(
            {
                "type": "unique_keyword_opportunity",
                "title": "Unique Keyword Opportunity",
                "severity": "success",
                "message": (
                    f"You track {len(you_only)} keyword(s) competitors do not. "
                    "Maintain coverage on these unique terms."
                ),
            }
        )
    else:
        insights.append(
            {
                "type": "unique_keyword_opportunity",
                "title": "Unique Keyword Opportunity",
                "severity": "warning",
                "message": (
                    "No unique keyword gaps detected between you and competitors "
                    "(or no competitor data available)."
                ),
            }
        )

    # 4. Top ranking strength / gap on shared keywords
    ranked_shared = [
        row
        for row in shared_keywords
        if bool((row.get("you") or {}).get("found"))
        and _safe_int((row.get("you") or {}).get("position")) is not None
    ]
    if ranked_shared:
        best_row = min(
            ranked_shared,
            key=lambda row: _safe_int((row.get("you") or {}).get("position")) or 10**9,
        )
        you_att = best_row.get("you") or {}
        keyword = you_att.get("keyword")
        pos = you_att.get("position")
        score = you_att.get("score")
        winner = best_row.get("winner")
        if winner == "you":
            severity = "success"
            message = (
                f"Your strongest shared keyword is \"{keyword}\" at position {pos} "
                f"(score {score}/{MAX_SEARCH_VISIBILITY_SCORE}), ahead of competitors."
            )
        elif winner == "competitor":
            severity = "warning"
            gap = best_row.get("position_gap")
            gap_text = f" by {gap} position(s)" if gap is not None else ""
            message = (
                f"Your best shared keyword \"{keyword}\" is at position {pos}, "
                f"but a competitor still ranks higher{gap_text}."
            )
        else:
            severity = "warning"
            message = (
                f"Your best shared keyword is \"{keyword}\" at position {pos} "
                f"(score {score}/{MAX_SEARCH_VISIBILITY_SCORE})."
            )
        insights.append(
            {
                "type": "top_ranking_strength",
                "title": "Top Ranking Strength",
                "severity": severity,
                "message": message,
            }
        )
    else:
        insights.append(
            {
                "type": "top_ranking_strength",
                "title": "Top Ranking Strength",
                "severity": "danger",
                "message": (
                    "You have no found rankings on shared keywords. "
                    "Improve organic visibility on overlapping search terms."
                ),
            }
        )

    return insights


def _strip_internal(entry: dict) -> dict:
    cleaned = dict(entry)
    cleaned.pop("_attempts_map", None)
    return cleaned


def get_keyword_seo_comparison(
    business_id: str,
    competitor_ids: list[str],
) -> dict:
    business_id = str(business_id).strip()
    competitor_ids = [str(cid).strip() for cid in competitor_ids if str(cid).strip()]

    # Avoid comparing a business against itself
    competitor_ids = [cid for cid in competitor_ids if cid != business_id]

    all_ids = [business_id] + competitor_ids
    worker_count = min(MAX_COMPARISON_WORKERS, max(len(all_ids), 1))

    logger.info(
        "keyword_seo_comparison: fetching Keyword SEO results — "
        f"business_id='{business_id}', competitors={competitor_ids}"
    )

    fetched: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_one, bid): bid for bid in all_ids}
        for future in as_completed(futures):
            bid, data = future.result()
            fetched[bid] = data

    you = _summarize_entry(business_id, fetched.get(business_id))
    competitors = {
        cid: _summarize_entry(cid, fetched.get(cid)) for cid in competitor_ids
    }

    your_map = you.get("_attempts_map") or {}
    competitor_maps = {
        cid: (entry.get("_attempts_map") or {})
        for cid, entry in competitors.items()
        if entry.get("status") == "success"
    }

    shared_rows, shared_totals = _compare_shared(your_map, competitor_maps)

    your_keys = set(your_map.keys())
    all_comp_keys: set[str] = set()
    for cmap in competitor_maps.values():
        all_comp_keys |= set(cmap.keys())

    you_only = _unique_keyword_rows(your_map, all_comp_keys)
    competitor_only: dict[str, list[dict]] = {}
    for cid, cmap in competitor_maps.items():
        competitor_only[cid] = _unique_keyword_rows(cmap, your_keys)

    comparison = {
        "shared_keywords": shared_rows,
        "you_only_keywords": you_only,
        "competitor_only_keywords": competitor_only,
        "totals": {
            **shared_totals,
            "you_only_count": len(you_only),
            "competitor_only_count": sum(len(v) for v in competitor_only.values()),
        },
    }

    you_public = _strip_internal(you)
    competitors_public = {
        cid: _strip_internal(entry) for cid, entry in competitors.items()
    }

    key_insights = _generate_insights(you_public, competitors_public, comparison)

    status = "success"
    if you_public.get("status") == "not_found":
        status = "partial" if any(
            c.get("status") == "success" for c in competitors_public.values()
        ) else "not_found"
    elif any(c.get("status") == "not_found" for c in competitors_public.values()):
        status = "partial"

    logger.info(
        "keyword_seo_comparison: completed — "
        f"business_id='{business_id}', status={status}, "
        f"shared={comparison['totals']['shared_count']}, "
        f"you_only={comparison['totals']['you_only_count']}, "
        f"competitor_only={comparison['totals']['competitor_only_count']}, "
        f"insights={len(key_insights)}"
    )

    return {
        "status": status,
        "business_id": business_id,
        "you": you_public,
        "competitors": competitors_public,
        "comparison": comparison,
        "key_insights": key_insights,
    }
