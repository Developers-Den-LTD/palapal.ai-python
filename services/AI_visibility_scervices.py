import re

from openai import OpenAI

from core.config import settings
from schema.AI_visibility_schema import AIVisibilityRequest
from services.ai_provider_services import (
    PROVIDER_MODELS,
    fetch_answers_from_all_providers,
)
from services.logger_services import logger
from services.model_loader import load_sentiment_model
from services.s3_service import load_scraped_result_data
from utils.scraped_result_paths import slugify_folder_name

TOTAL_QUESTIONS = 10
TOTAL_AI_ANSWERS = TOTAL_QUESTIONS * len(PROVIDER_MODELS)
MAX_CITATION_SCORE = 15
MAX_EXPOSURE_SCORE = 10
MAX_SENTIMENT_SCORE = 15
MAX_DDI_AI_VISIBILITY_SCORE = MAX_CITATION_SCORE + MAX_EXPOSURE_SCORE + MAX_SENTIMENT_SCORE

POSITION_POINTS = {
    "first": 10,
    "middle": 5,
    "last": 2,
}

REVIEW_PLATFORMS = ("google_maps", "yelp", "tripadvisor")

perplexity_client = OpenAI(
    api_key=settings.Perplexity_API,
    base_url="https://api.perplexity.ai",
)


def _log_section(title: str) -> None:
    logger.info(f"ai_visibility: {'=' * 60}")
    logger.info(f"ai_visibility: {title}")
    logger.info(f"ai_visibility: {'=' * 60}")


def _log_block(title: str, content: str) -> None:
    _log_section(title)
    for line in content.splitlines():
        logger.info(f"ai_visibility: {line}" if line else "ai_visibility:")


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


def _generate_questions(business_type: str, business_loc: str, business_name: str) -> str:
    _log_section("Step 1 — Generating questions")
    logger.info(
        f"ai_visibility: calling Perplexity "
        f"model='{PROVIDER_MODELS['perplexity']}' "
        f"type='{business_type}', loc='{business_loc}', business='{business_name}'"
    )

    try:
        response = perplexity_client.chat.completions.create(
            model=PROVIDER_MODELS["perplexity"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a local search query expert. Your job is to generate "
                        "realistic, high-intent discovery questions that a real person "
                        "would type when looking for the best options in a category and "
                        "location. Return only a numbered list of exactly 10 questions. "
                        "No introductions, explanations, or extra text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""Generate exactly 10 search questions a real person would type when looking for a {business_type} in {business_loc}.

CONTEXT:
These questions will be used to test how often AI assistants recommend top businesses in this category and location. Each question should reflect a mainstream, quality-focused search — the kind someone asks when they want the best or most trusted options, not a narrow filter or budget hunt.

GOOD QUESTION TYPES (use a mix of these):
- Best / top / highest-rated discovery: "best {business_type} in {business_loc}", "top rated {business_type} in {business_loc}"
- Recommendation intent: "which {business_type} do you recommend in {business_loc}", "what are the best {business_type} options in {business_loc}"
- Trust / reputation: "most popular {business_type} in {business_loc}", "highest rated {business_type} near {business_loc}"
- Decision-making: "where should I go for a good {business_type} in {business_loc}", "which {business_type} is worth visiting in {business_loc}"
- Comparison / roundup style: "what are the top {business_type} places in {business_loc}", "best places for {business_type} in {business_loc}"
- Conversational / natural user queries (include exactly 1 or 2 of these): casual, full-sentence questions like someone chatting with an AI assistant — e.g. "I'm planning a trip to {business_loc}. Which {business_type} should I go to?", "I'm visiting {business_loc} soon — where's a good {business_type}?", "Can you suggest a {business_type} in {business_loc}?"

STRICT RULES:
1. Every question MUST be about finding a {business_type} in or near {business_loc}
2. Exactly 1 or 2 questions must be conversational, natural full-sentence queries (like a normal person talking to ChatGPT) — not keyword-style searches
3. The remaining 8 or 9 questions should be varied search-style discovery queries
4. Questions must sound natural — like real Google or ChatGPT searches, not marketing copy
5. Focus on quality, reputation, and discovery — NOT price, budget, or deals
6. Vary the wording across all 10 questions; do not repeat the same template with tiny changes
7. Do NOT mention "{business_name}" or any specific business name
8. Do NOT use question words or angles that narrow the search to a niche attribute

NEVER GENERATE QUESTIONS LIKE THESE (examples of what to avoid):
- Price/budget: "cheap {business_type} in {business_loc}", "affordable {business_type} in {business_loc}", "budget-friendly {business_type} in {business_loc}", "{business_type} with good deals in {business_loc}"
- Niche attributes: "pet friendly {business_type} in {business_loc}", "vegan {business_type} in {business_loc}", "24-hour {business_type} in {business_loc}", "kid-friendly {business_type} in {business_loc}", "{business_type} with free parking in {business_loc}"
- Overly specific filters that exclude most businesses in the category

OUTPUT FORMAT:
Return only a numbered list from 1 to 10. One question per line. No other text.""",
                },
            ],
        )
    except Exception as e:
        logger.exception(f"ai_visibility: question generation failed — {e}")
        raise

    questions_text = response.choices[0].message.content or ""
    logger.info(f"ai_visibility: questions generated successfully ({len(questions_text)} chars)")
    _log_block("Generated questions", questions_text)
    return questions_text

def _parse_questions(questions_text: str) -> list[str]:
    questions = []

    for line in questions_text.splitlines():
        question = re.sub(r"^\s*\d+\s*[.)-]\s*", "", line).strip()
        if question:
            questions.append(question)

    if len(questions) != TOTAL_QUESTIONS:
        raise ValueError(
            f"Expected {TOTAL_QUESTIONS} generated questions, "
            f"but received {len(questions)}"
        )

    return questions


def _all_answer_records(
    provider_results: dict[str, dict],
    questions: list[str],
) -> list[dict]:
    records = []

    for provider in PROVIDER_MODELS:
        provider_result = provider_results.get(provider, {})
        answers = {
            item["question_number"]: item
            for item in provider_result.get("answers", [])
        }

        for question_number, question in enumerate(questions, start=1):
            answer = answers.get(question_number, {})
            records.append({
                "provider": provider,
                "question_number": question_number,
                "question": question,
                "businesses": answer.get("businesses", []),
            })

    return records


def _calculate_citation_score(
    business_name: str,
    answer_records: list[dict],
) -> dict:
    _log_section(f"Step 3 — Citation score (checking mentions of '{business_name}')")

    mentioned_in = []
    provider_mentions = {provider: 0 for provider in PROVIDER_MODELS}

    for record in answer_records:
        mentioned = any(
            _business_name_mentioned(business_name, name)
            for name in record["businesses"]
        )
        if not mentioned:
            continue

        provider = record["provider"]
        provider_mentions[provider] += 1
        mentioned_in.append({
            "provider": provider,
            "question_number": record["question_number"],
            "question": record["question"],
        })

    mention_count = len(mentioned_in)
    score = (mention_count / TOTAL_AI_ANSWERS) * MAX_CITATION_SCORE
    percentage = (mention_count / TOTAL_AI_ANSWERS) * 100

    logger.info(
        f"ai_visibility: citation results — "
        f"mentions={mention_count}/{TOTAL_AI_ANSWERS}, "
        f"score={score:.2f}/{MAX_CITATION_SCORE}, "
        f"percentage={percentage:.0f}%"
    )

    return {
        "mentions": mention_count,
        "total_answers": TOTAL_AI_ANSWERS,
        "total_questions": TOTAL_AI_ANSWERS,
        "max_score": MAX_CITATION_SCORE,
        "score": round(score, 2),
        "percentage": round(percentage),
        "mentions_by_provider": provider_mentions,
        "mentioned_in": mentioned_in,
    }


def _calculate_exposure_fairness(
    business_name: str,
    answer_records: list[dict],
) -> dict:
    _log_section(f"Step 4 — Exposure fairness (position of '{business_name}')")

    positions = []
    position_details = []

    for record in answer_records:
        businesses = record["businesses"]
        business_index = next(
            (
                index
                for index, name in enumerate(businesses)
                if _business_name_mentioned(business_name, name)
            ),
            None,
        )

        if business_index is None:
            continue

        if business_index == 0:
            position = "first"
        elif business_index == len(businesses) - 1:
            position = "last"
        else:
            position = "middle"

        positions.append(position)
        position_details.append({
            "provider": record["provider"],
            "question_number": record["question_number"],
            "question": record["question"],
            "position": position,
            "index": business_index + 1,
            "total_names": len(businesses),
        })
        logger.info(
            f"ai_visibility: exposure mention — "
            f"provider={record['provider'].upper()}, "
            f"Q{record['question_number']}: {record['question']} | "
            f"position={business_index + 1}/{len(businesses)} "
            f"({position.upper()})"
        )

    first_count = positions.count("first")
    middle_count = positions.count("middle")
    last_count = positions.count("last")

    if positions:
        average_position = max(
            POSITION_POINTS,
            key=lambda position: positions.count(position),
        )
        exposure_score = sum(
            POSITION_POINTS[position]
            for position in positions
        ) / len(positions)
    else:
        average_position = "not found"
        exposure_score = 0
        logger.info(
            f"ai_visibility: exposure — '{business_name}' was not mentioned "
            f"in any of the {TOTAL_AI_ANSWERS} answers"
        )

    logger.info(
        f"ai_visibility: exposure results — first={first_count}, "
        f"middle={middle_count}, last={last_count}, "
        f"score={exposure_score:.2f}/{MAX_EXPOSURE_SCORE}"
    )
    if position_details:
        mentioned_answers = ", ".join(
            f"{detail['provider'].upper()} A{detail['question_number']}"
            for detail in position_details
        )
        logger.info(
            f"ai_visibility: exposure business mentioned in answers — "
            f"{mentioned_answers}"
        )

    return {
        "position_breakdown": {
            "first": first_count,
            "middle": middle_count,
            "last": last_count,
        },
        "answers_checked": TOTAL_AI_ANSWERS,
        "mentions_checked": len(positions),
        "average_position": average_position,
        "max_score": MAX_EXPOSURE_SCORE,
        "score": round(exposure_score, 2),
        "details": position_details,
    }


def _normalize_sentiment_label(raw_label: str) -> str:
    label = raw_label.lower().strip()

    if "positive" in label:
        return "positive"
    if "negative" in label:
        return "negative"
    if "neutral" in label:
        return "neutral"

    return "neutral"


def _sentiment_from_rating(rating) -> str | None:
    if not isinstance(rating, (int, float)):
        return None
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    if rating <= 2:
        return "negative"
    return None


def _calculate_sentiment_score(sentiment_data: dict) -> tuple[int, dict]:
    positive_pct = sentiment_data["positive_percentage"]
    negative_pct = sentiment_data["negative_percentage"]
    total_comments = sentiment_data["total_comments"]

    if positive_pct >= 90:
        base_score = 15
    elif positive_pct >= 80:
        base_score = 12
    elif positive_pct >= 70:
        base_score = 10
    elif positive_pct >= 60:
        base_score = 8
    elif positive_pct >= 50:
        base_score = 6
    elif positive_pct >= 40:
        base_score = 4
    elif positive_pct >= 30:
        base_score = 2
    else:
        base_score = 0

    if negative_pct > 30:
        penalty = 6
    elif negative_pct > 20:
        penalty = 4
    elif negative_pct > 10:
        penalty = 2
    else:
        penalty = 0

    if total_comments >= 60:
        confidence = 1.0
    elif total_comments >= 30:
        confidence = 0.9
    elif total_comments >= 15:
        confidence = 0.7
    else:
        confidence = 0.5

    final_score = (base_score - penalty) * confidence
    final_score = round(final_score)
    final_score = max(0, min(MAX_SENTIMENT_SCORE, final_score))

    breakdown = {
        "base_score": base_score,
        "penalty": penalty,
        "confidence": confidence,
        "final_score": final_score,
        "positive_pct": positive_pct,
        "negative_pct": negative_pct,
        "total_comments": total_comments,
    }

    return final_score, breakdown


def _calculate_sentiment_analysis(business_name: str) -> dict:
    _log_section("Step 5 — Sentiment analysis (scraped reviews)")

    model = load_sentiment_model()

    folder_slug = slugify_folder_name(business_name)

    logger.info(
        f"ai_visibility: business_name='{business_name}', folder_slug='{folder_slug}'"
    )

    try:
        scraped_data = load_scraped_result_data(business_name)
    except FileNotFoundError:
        logger.warning(
            f"ai_visibility: scraped_result.json not found locally or in S3 "
            f"for '{business_name}'"
        )
        return {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "total_comments": 0,
            "positive_percentage": 0.0,
            "negative_percentage": 0.0,
            "neutral_percentage": 0.0,
            "score": 0,
            "max_score": MAX_SENTIMENT_SCORE,
            "score_breakdown": {
                "base_score": 0,
                "penalty": 0,
                "confidence": 0.0,
                "final_score": 0,
                "positive_pct": 0.0,
                "negative_pct": 0.0,
                "total_comments": 0,
            },
            "message": f"No scraped result is present against this business name: '{business_name}'.",
        }

    positive = 0
    negative = 0
    neutral = 0
    total_comments = 0

    for platform in REVIEW_PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])

        for review in reviews:
            total_comments += 1
            comment = review.get("comment")
            display_text = ""

            if comment and str(comment).strip():
                display_text = str(comment).strip()
                try:
                    prediction = model(
                        display_text,
                        truncation=True,
                        max_length=512,
                    )[0]
                    sentiment = _normalize_sentiment_label(prediction["label"])
                except Exception as e:
                    logger.warning(
                        f"ai_visibility: [{platform}] sentiment inference failed — {e}"
                    )
                    sentiment = "neutral"
                    display_text = (
                        f"{display_text[:100]}... "
                        "[sentiment inference failed — counted as neutral]"
                    )
            else:
                rating_sentiment = _sentiment_from_rating(review.get("rating"))
                if rating_sentiment is None:
                    sentiment = "neutral"
                    display_text = "[No comment text — counted as neutral]"
                else:
                    sentiment = rating_sentiment
                    display_text = f"[No comment — inferred from {review.get('rating')} star rating]"

            if sentiment == "positive":
                positive += 1
            elif sentiment == "negative":
                negative += 1
            else:
                neutral += 1

            logger.info(
                f"ai_visibility: [{platform}] {sentiment.upper()} — "
                f"{display_text[:100]}{'...' if len(display_text) > 100 else ''}"
            )
            print(f"[{platform}] {sentiment.upper()}: {display_text[:120]}")

    logger.info("ai_visibility: sentiment results:")
    logger.info(f"ai_visibility:   positive : {positive}")
    logger.info(f"ai_visibility:   negative : {negative}")
    logger.info(f"ai_visibility:   neutral  : {neutral}")
    logger.info(f"ai_visibility:   total    : {total_comments}")

    print("\nSentiment Results:")
    print(f"  Positive : {positive}")
    print(f"  Negative : {negative}")
    print(f"  Neutral  : {neutral}")
    print(f"  Total    : {total_comments}\n")

    sentiment_data = {
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "total_comments": total_comments,
        "positive_percentage": round((positive / total_comments) * 100, 1) if total_comments else 0.0,
        "negative_percentage": round((negative / total_comments) * 100, 1) if total_comments else 0.0,
        "neutral_percentage": round((neutral / total_comments) * 100, 1) if total_comments else 0.0,
    }

    final_score, score_breakdown = _calculate_sentiment_score(sentiment_data)

    logger.info("ai_visibility: sentiment score breakdown:")
    logger.info(f"ai_visibility:   base_score : {score_breakdown['base_score']} / {MAX_SENTIMENT_SCORE}")
    logger.info(f"ai_visibility:   penalty    : -{score_breakdown['penalty']}")
    logger.info(f"ai_visibility:   confidence : {score_breakdown['confidence']}")
    logger.info(f"ai_visibility:   final_score: {score_breakdown['final_score']} / {MAX_SENTIMENT_SCORE}")

    print("Sentiment Score:")
    print(f"  Base Score:  {score_breakdown['base_score']} / {MAX_SENTIMENT_SCORE}")
    print(f"  Penalty:    -{score_breakdown['penalty']}")
    print(f"  Confidence:  {score_breakdown['confidence']}")
    print(f"  Final Score: {score_breakdown['final_score']} / {MAX_SENTIMENT_SCORE}\n")

    return {
        **sentiment_data,
        "score": final_score,
        "max_score": MAX_SENTIMENT_SCORE,
        "score_breakdown": score_breakdown,
    }






def analyze_ai_visibility(payload: AIVisibilityRequest) -> dict:
    business_name = payload.business_name.strip()
    business_type = payload.business_type.strip()
    business_loc = payload.business_loc.strip()

    _log_section("AI Visibility analysis started")
    logger.info(
        f"ai_visibility: business='{business_name}', "
        f"type='{business_type}', loc='{business_loc}'"
    )

    questions_text = _generate_questions(business_type, business_loc, business_name)
    questions = _parse_questions(questions_text)

    _log_section("Step 2 — Fetching answers from all AI providers")
    provider_results = fetch_answers_from_all_providers(
        business_type,
        business_loc,
        questions,
    )
    answer_records = _all_answer_records(provider_results, questions)

    # ---------------- Calculate Citation ---------------- #
    citation_score = _calculate_citation_score(business_name, answer_records)
    
    
    # ---------------- Calculate Exposure Fairness ---------------- #
    exposure_fairness = _calculate_exposure_fairness(
        business_name,
        answer_records,
    )
    
    
    # ---------------- Calculate Sentiment Analysis ---------------- #
    sentiment_analysis = _calculate_sentiment_analysis(business_name)


    citation_result = citation_score["score"]
    exposure_fairness_result = exposure_fairness["score"]
    sentiment_analysis_result = sentiment_analysis["score"]
    ddi_ai_visibility_result = round(
        citation_result + exposure_fairness_result + sentiment_analysis_result,
        2,
    )

    _log_section("DDI AI Visibility — Final Score Summary")
    logger.info(f"ai_visibility: citation result = {citation_result}")
    logger.info(f"ai_visibility: exposure fairness result = {exposure_fairness_result}")
    logger.info(f"ai_visibility: sentiment analysis result = {sentiment_analysis_result}")
    logger.info(f'ai_visibility: "DDI_AI_visibility_result": {ddi_ai_visibility_result},')
    logger.info(
        f'ai_visibility: "max_DDI_AI_visibility_score": {MAX_DDI_AI_VISIBILITY_SCORE}'
    )

    successful_providers = sum(
        result["status"] == "success"
        for result in provider_results.values()
    )
    analysis_status = (
        "success"
        if successful_providers == len(PROVIDER_MODELS)
        else "partial"
    )

    result = {
        "status": analysis_status,
        "business_name": business_name,
        "business_type": business_type,
        "business_location": business_loc,
        "questions": questions,
        "answers": provider_results,
        "successful_providers": successful_providers,
        "total_providers": len(PROVIDER_MODELS),
        "citation_score": citation_score,
        "exposure_fairness": exposure_fairness,
        "sentiment_analysis": sentiment_analysis,
        "DDI_AI_visibility_result": ddi_ai_visibility_result,
        "max_DDI_AI_visibility_score": MAX_DDI_AI_VISIBILITY_SCORE,
    }

    _log_section("AI Visibility analysis completed")
    logger.info(
        f"ai_visibility: final results for '{business_name}' | "
        f"{business_type} | {business_loc}"
    )
    logger.info(
        f"ai_visibility: citation — "
        f"{citation_score['mentions']}/{TOTAL_AI_ANSWERS} mentions, "
        f"score={citation_score['score']}/{MAX_CITATION_SCORE}, "
        f"percentage={citation_score['percentage']}%"
    )
    logger.info(
        f"ai_visibility: exposure — "
        f"position={exposure_fairness['average_position'].upper()}, "
        f"score={exposure_fairness['score']}/{MAX_EXPOSURE_SCORE}"
    )
    logger.info(
        f"ai_visibility: sentiment — "
        f"positive={sentiment_analysis['positive']}, "
        f"negative={sentiment_analysis['negative']}, "
        f"neutral={sentiment_analysis['neutral']}, "
        f"score={sentiment_analysis['score']}/{MAX_SENTIMENT_SCORE}"
    )
    logger.info(
        f"ai_visibility: DDI_AI_visibility_result — "
        f"{ddi_ai_visibility_result}/{MAX_DDI_AI_VISIBILITY_SCORE}"
    )

    print("\ncitation result =", citation_result)
    print("exposure fairness result =", exposure_fairness_result)
    print("sentiment analysis result =", sentiment_analysis_result)
    print(f'    "DDI_AI_visibility_result": {ddi_ai_visibility_result},')
    print(f'    "max_DDI_AI_visibility_score": {MAX_DDI_AI_VISIBILITY_SCORE}\n')

    return result
