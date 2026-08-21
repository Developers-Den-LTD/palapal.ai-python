from concurrent.futures import ThreadPoolExecutor, as_completed

from services.ai_provider_services import PROVIDER_MODELS
from services.logger_services import logger
from services.s3_service import fetch_ddi_score_by_business_id

MAX_COMPETITOR_WORKERS = 8

DEFAULT_MAX_SCORES = {
    "citation_score": 15,
    "exposure_fairness": 10,
    "sentiment_analysis": 15,
    "review_velocity": 15,
    "star_rating_decay": 15,
    "response_rate": 10,
    "pagespeed": 4,
    "llms_txt": 5,
    "json_ld": 5,
    "nap_consistency": 6,
}

TECH_KEYS = ("pagespeed", "llms_txt", "json_ld", "nap_consistency")

# Short badges for the head-to-head UI (G / O / A / P).
ENGINE_BADGES = {
    "gemini": "G",
    "openai": "O",
    "anthropic": "A",
    "perplexity": "P",
}
ENGINE_ORDER = tuple(PROVIDER_MODELS.keys())


def _fetch_one(business_id: str) -> tuple[str, dict | None]:
    result = fetch_ddi_score_by_business_id(business_id)
    return business_id, result


def _normalize_name_for_match(text: str) -> str:
    text = text.lower().strip()
    for ch in ("'", "'", "`", "´"):
        text = text.replace(ch, "")
    return text


def _business_name_mentioned(business_name: str, text: str) -> bool:
    normalized_name = _normalize_name_for_match(business_name)
    if not normalized_name:
        return False
    return normalized_name in _normalize_name_for_match(text)


def _resolve_business_name(ddi_result: dict | None, business_id: str) -> str:
    if not isinstance(ddi_result, dict):
        return business_id
    name = str(ddi_result.get("business_name") or "").strip()
    if name:
        return name
    ai = ddi_result.get("ai_visibility") or {}
    if isinstance(ai, dict):
        name = str(ai.get("business_name") or "").strip()
        if name:
            return name
    return business_id


def _provider_answers_for_question(provider_result: dict, question_number: int) -> list[str]:
    if not isinstance(provider_result, dict):
        return []
    for item in provider_result.get("answers") or []:
        if not isinstance(item, dict):
            continue
        if int(item.get("question_number") or 0) == question_number:
            businesses = item.get("businesses") or []
            return [str(name) for name in businesses if str(name).strip()]
    return []


def _mention_position(business_name: str, businesses: list[str]) -> int | None:
    for index, name in enumerate(businesses, start=1):
        if _business_name_mentioned(business_name, name):
            return index
    return None


def _build_head_to_head_queries(
    primary_id: str,
    competitor_ids: list[str],
    fetched: dict[str, dict | None],
) -> list[dict]:
    """
    Build UI-ready head-to-head rankings from the primary business's AI visibility
    answers. For each question, count how many of the 4 engines mention you and
    each competitor (e.g. \"3/4 engines\").
    """
    primary_ddi = fetched.get(primary_id)
    if not isinstance(primary_ddi, dict):
        return []

    ai = primary_ddi.get("ai_visibility") or {}
    if not isinstance(ai, dict) or ai.get("status") == "error":
        return []

    questions = ai.get("questions") or []
    answers = ai.get("answers") or {}
    if not isinstance(questions, list) or not questions or not isinstance(answers, dict):
        return []

    participants: list[dict] = [
        {
            "business_id": primary_id,
            "business_name": _resolve_business_name(primary_ddi, primary_id),
            "role": "you",
        }
    ]
    for competitor_id in competitor_ids:
        competitor_ddi = fetched.get(competitor_id)
        participants.append(
            {
                "business_id": competitor_id,
                "business_name": _resolve_business_name(competitor_ddi, competitor_id),
                "role": "competitor",
            }
        )

    total_engines = len(ENGINE_ORDER)
    head_to_head: list[dict] = []

    for question_number, question in enumerate(questions, start=1):
        query_text = str(question).strip()
        if not query_text:
            continue

        provider_lists: dict[str, list[str]] = {
            provider: _provider_answers_for_question(answers.get(provider) or {}, question_number)
            for provider in ENGINE_ORDER
        }

        rankings: list[dict] = []
        for participant in participants:
            engine_hits: dict[str, bool] = {}
            engine_badges: dict[str, bool] = {}
            positions: list[int] = []

            for provider in ENGINE_ORDER:
                position = _mention_position(
                    participant["business_name"],
                    provider_lists[provider],
                )
                mentioned = position is not None
                engine_hits[provider] = mentioned
                engine_badges[ENGINE_BADGES.get(provider, provider[:1].upper())] = mentioned
                if position is not None:
                    positions.append(position)

            engines_mentioned = sum(1 for hit in engine_hits.values() if hit)
            average_position = (
                round(sum(positions) / len(positions), 1) if positions else None
            )
            rankings.append(
                {
                    "business_id": participant["business_id"],
                    "business_name": participant["business_name"],
                    "role": participant["role"],
                    "engines_mentioned": engines_mentioned,
                    "engines_total": total_engines,
                    "engines_label": f"{engines_mentioned}/{total_engines} engines",
                    "engine_hits": engine_hits,
                    "engine_badges": engine_badges,
                    "average_position": average_position,
                }
            )

        rankings.sort(
            key=lambda row: (
                -int(row["engines_mentioned"]),
                float(row["average_position"])
                if row["average_position"] is not None
                else 999.0,
                str(row["business_name"]).lower(),
            )
        )
        for rank, row in enumerate(rankings, start=1):
            row["rank"] = rank

        head_to_head.append(
            {
                "question_number": question_number,
                "query": query_text,
                "total_engines": total_engines,
                "engines": list(ENGINE_ORDER),
                "engine_badges": {
                    provider: ENGINE_BADGES.get(provider, provider[:1].upper())
                    for provider in ENGINE_ORDER
                },
                "rankings": rankings,
            }
        )

    return head_to_head


def _extract_score(section: dict | None, *keys: str, default_max: float) -> dict:
    data = section or {}
    for key in keys:
        if not isinstance(data, dict):
            data = {}
            break
        data = data.get(key) or {}
    if not isinstance(data, dict):
        data = {}
    score = data.get("score", 0)
    max_score = data.get("max_score", default_max)
    return {
        "score": float(score) if score is not None else 0.0,
        "max_score": float(max_score) if max_score is not None else float(default_max),
    }


def _summarize(ddi_result: dict) -> dict:
    ai = ddi_result.get("ai_visibility") or {}
    reputation = ddi_result.get("reputation") or {}
    technical = ddi_result.get("technical_foundation") or {}

    return {
        "citation_score": _extract_score(
            ai, "citation_score", default_max=DEFAULT_MAX_SCORES["citation_score"]
        ),
        "exposure_fairness": _extract_score(
            ai, "exposure_fairness", default_max=DEFAULT_MAX_SCORES["exposure_fairness"]
        ),
        "sentiment_analysis": (ai.get("sentiment_analysis") or {}) if isinstance(ai.get("sentiment_analysis"), dict) else _extract_score(
            ai, "sentiment_analysis", default_max=DEFAULT_MAX_SCORES["sentiment_analysis"]
        ),
        "review_velocity": _extract_score(
            reputation, "review_velocity", default_max=DEFAULT_MAX_SCORES["review_velocity"]
        ),
        "star_rating_decay": _extract_score(
            reputation, "star_rating_decay", default_max=DEFAULT_MAX_SCORES["star_rating_decay"]
        ),
        "response_rate": _extract_score(
            reputation, "response_rate", default_max=DEFAULT_MAX_SCORES["response_rate"]
        ),
        "pagespeed": _extract_score(
            technical, "pagespeed_score", default_max=DEFAULT_MAX_SCORES["pagespeed"]
        ),
        "llms_txt": _extract_score(
            technical, "llms_txt", default_max=DEFAULT_MAX_SCORES["llms_txt"]
        ),
        "json_ld": _extract_score(
            technical, "json_ld", default_max=DEFAULT_MAX_SCORES["json_ld"]
        ),
        "nap_consistency": _extract_score(
            technical, "nap_consistency", default_max=DEFAULT_MAX_SCORES["nap_consistency"]
        ),
    }


def _pct(entry: dict, key: str) -> float:
    """Return score as a 0–100 percentage for a given key in a summarized entry."""
    block = entry.get(key) or {}
    score = float(block.get("score") or 0)
    max_score = float(block.get("max_score") or DEFAULT_MAX_SCORES.get(key) or 1)
    return round((score / max_score) * 100, 1) if max_score else 0.0


def _severity(your_pct: float, best_pct: float) -> str:
    diff = best_pct - your_pct
    if diff <= 0:
        return "success"
    if diff <= 20:
        return "warning"
    return "danger"


def _generate_insights(your_id: str, your_entry: dict, competitors: dict) -> list[dict]:
    """
    Generate three competitive insights:
      1. Market Dominance  — based on citation_score
      2. Sentiment Leader  — based on sentiment_analysis + response_rate
      3. Technical Opportunity — based on pagespeed + llms_txt + json_ld + nap_consistency
    """
    valid_competitors = {
        cid: data
        for cid, data in competitors.items()
        if data.get("status") != "not_found"
    }
    insights: list[dict] = []

    # ── 1. Market Dominance (citation_score) ──────────────────────────────────
    your_citation = _pct(your_entry, "citation_score")

    if valid_competitors:
        best_cid = max(valid_competitors, key=lambda cid: _pct(valid_competitors[cid], "citation_score"))
        best_citation = _pct(valid_competitors[best_cid], "citation_score")
        diff = round(best_citation - your_citation, 1)

        your_exposure = _pct(your_entry, "exposure_fairness")
        best_exposure = _pct(valid_competitors[best_cid], "exposure_fairness")

        if diff > 0:
            message = (
                f"A competitor appears {diff}% more often than you in AI search results. "
                f"Key strength: Higher citation score ({best_citation}% vs {your_citation}%). "
                f"Exposure fairness: you {your_exposure}% vs competitor {best_exposure}%."
            )
        else:
            message = (
                f"You lead in market visibility with a citation score of {your_citation}%. "
                f"Best competitor is at {best_citation}%. "
                f"Maintain this advantage by keeping your AI presence consistent."
            )
    else:
        diff = 0.0
        best_citation = 0.0
        message = f"Your citation score is {your_citation}%. No competitor data available for comparison."

    insights.append({
        "type": "market_dominance",
        "title": "Market Dominance",
        "severity": _severity(your_citation, best_citation),
        "message": message,
    })

    # ── 2. Sentiment Leader (sentiment_analysis + response_rate) ──────────────
    your_sentiment = _pct(your_entry, "sentiment_analysis")
    your_response = _pct(your_entry, "response_rate")

    if valid_competitors:
        best_sentiment_cid = max(
            valid_competitors,
            key=lambda cid: _pct(valid_competitors[cid], "sentiment_analysis"),
        )
        best_sentiment = _pct(valid_competitors[best_sentiment_cid], "sentiment_analysis")
        best_response = _pct(valid_competitors[best_sentiment_cid], "response_rate")

        sentiment_diff = round(best_sentiment - your_sentiment, 1)

        if sentiment_diff > 0:
            sentiment_line = (
                f"Your sentiment score is competitive ({your_sentiment}% positive) "
                f"but a competitor leads at {best_sentiment}%."
            )
        else:
            sentiment_line = (
                f"You lead in sentiment with {your_sentiment}% positive, "
                f"ahead of the best competitor at {best_sentiment}%."
            )

        if your_response == 0 and best_response == 0:
            response_line = "Response rate is 0% for you and all competitors — a first-mover opportunity to stand out."
        elif your_response < best_response:
            response_line = f"Focus on improving your response rate ({your_response}% vs competitor {best_response}%)."
        else:
            response_line = f"Your response rate ({your_response}%) is ahead of competitors ({best_response}%)."
    else:
        best_sentiment = 0.0
        sentiment_line = f"Your sentiment score is {your_sentiment}% positive."
        response_line = "No competitor data available for response rate comparison."

    insights.append({
        "type": "sentiment_leader",
        "title": "Sentiment Leader",
        "severity": _severity(your_sentiment, best_sentiment),
        "message": f"{sentiment_line} {response_line}",
    })

    # ── 3. Technical Opportunity (pagespeed + llms_txt + json_ld + nap_consistency) ──
    def _tech_pct(entry: dict) -> float:
        total_score = sum(float((entry.get(k) or {}).get("score") or 0) for k in TECH_KEYS)
        total_max = sum(float((entry.get(k) or {}).get("max_score") or DEFAULT_MAX_SCORES[k]) for k in TECH_KEYS)
        return round((total_score / total_max) * 100, 1) if total_max else 0.0

    your_tech = _tech_pct(your_entry)

    if valid_competitors:
        comp_tech_pcts = [_tech_pct(data) for data in valid_competitors.values()]
        avg_comp_tech = round(sum(comp_tech_pcts) / len(comp_tech_pcts), 1)
        best_comp_tech = max(comp_tech_pcts)

        if your_tech < avg_comp_tech:
            tech_line = (
                f"Your technical score is {your_tech}%; competitors average {avg_comp_tech}%. "
                "Improving your technical foundation will close this gap."
            )
        else:
            tech_line = (
                f"Your technical score is {your_tech}%, ahead of the competitor average of {avg_comp_tech}%. "
                "Keep investing in technical improvements."
            )

        # Call out zero scores across the board as first-mover opportunities
        zero_for_all = [
            k for k in TECH_KEYS
            if _pct(your_entry, k) == 0
            and all(_pct(data, k) == 0 for data in valid_competitors.values())
        ]
        if zero_for_all:
            keys_str = ", ".join(zero_for_all)
            tech_line += f" First-mover opportunity: {keys_str} is 0% for everyone."
    else:
        avg_comp_tech = 0.0
        best_comp_tech = 0.0
        tech_line = f"Your technical score is {your_tech}%. No competitor data available."

    insights.append({
        "type": "technical_opportunity",
        "title": "Technical Opportunity",
        "severity": _severity(your_tech, avg_comp_tech),
        "message": tech_line,
    })

    return insights


def get_competitor_analysis(
    business_id: str,
    competitor_ids: list[str],
) -> dict:
    all_ids = [business_id] + competitor_ids
    worker_count = min(MAX_COMPETITOR_WORKERS, len(all_ids))

    logger.info(
        f"competitor_analysis: fetching DDI scores — "
        f"business_id='{business_id}', competitors={competitor_ids}"
    )

    fetched: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_one, bid): bid for bid in all_ids}
        for future in as_completed(futures):
            bid, data = future.result()
            fetched[bid] = data

    def _build_entry(bid: str) -> dict:
        data = fetched.get(bid)
        if data is None:
            return {
                "status": "not_found",
                "message": f"No DDI score result found for business_id='{bid}'",
            }
        return _summarize(data)

    your_entry = _build_entry(business_id)
    competitors_result = {cid: _build_entry(cid) for cid in competitor_ids}

    insights = _generate_insights(business_id, your_entry, competitors_result)
    head_to_head_queries = _build_head_to_head_queries(
        business_id,
        competitor_ids,
        fetched,
    )

    logger.info(
        f"competitor_analysis: completed — "
        f"business_id='{business_id}', "
        f"competitors_found={sum(1 for v in competitors_result.values() if v.get('status') != 'not_found')}, "
        f"head_to_head_queries={len(head_to_head_queries)}"
    )

    return {
        business_id: your_entry,
        "competitors": competitors_result,
        "insights": insights,
        "head_to_head_queries": head_to_head_queries,
    }
