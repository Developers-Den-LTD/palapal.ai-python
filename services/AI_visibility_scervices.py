import json
import re

from openai import OpenAI

from core.config import settings
from schema.AI_visibility_schema import AIVisibilityRequest
from services.logger_services import logger
from services.model_loader import load_sentiment_model
from utils.scraped_result_paths import get_scraped_result_path, slugify_folder_name

TOTAL_QUESTIONS = 10
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

client = OpenAI(
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


def _generate_questions(business_type: str, business_loc: str, business_name: str) -> str:
    _log_section("Step 1 — Generating questions")
    logger.info(
        f"ai_visibility: calling Perplexity model='sonar' "
        f"type='{business_type}', loc='{business_loc}', business='{business_name}'"
    )

    try:
        response = client.chat.completions.create(
            model="sonar",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. Return only a numbered list of questions, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""Generate exactly 10 search questions a person might type when looking for a {business_type} in {business_loc}.

Rules:
- Questions should be natural, varied search queries
- Focus on {business_type} discovery in {business_loc}
- Examples of style: "best {business_type} in {business_loc}", "top rated {business_type} in {business_loc}", "where to find a good {business_type} in {business_loc}"
- Do NOT mention "{business_name}" in any question
- Return only the numbered list, no extra text""",
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


def _fetch_answers(business_type: str, business_loc: str, questions_text: str) -> str:
    _log_section("Step 2 — Fetching answers from Perplexity")
    logger.info(
        f"ai_visibility: calling Perplexity model='sonar' "
        f"type='{business_type}', loc='{business_loc}'"
    )

    try:
        response = client.chat.completions.create(
            model="sonar",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful local search assistant. "
                        "Answer each question with real, specific business names."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""Answer each of the following questions about {business_type}s in {business_loc}.
For each answer, you must list the top 9-10 specific business names.

Format your response exactly like this:
Q1: [question]
A1: [answer with business names]

Q2: [question]
A2: [answer with business names]

...and so on for all 10 questions.

Here are the questions:
{questions_text}""",
                },
            ],
        )
    except Exception as e:
        logger.exception(f"ai_visibility: answer fetch failed — {e}")
        raise

    answers_text = response.choices[0].message.content or ""
    logger.info(f"ai_visibility: answers received successfully ({len(answers_text)} chars)")
    _log_block("Perplexity answers", answers_text)
    return answers_text


def _calculate_citation_score(business_name: str, answers_text: str) -> dict:
    _log_section(f"Step 3 — Citation score (checking mentions of '{business_name}')")

    lines = answers_text.split("\n")
    mention_count = 0
    mentioned_in = []
    q_number = 0
    current_question = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Q") and ":" in stripped:
            q_number += 1
            current_question = stripped
        if stripped.startswith("A") and ":" in stripped:
            if business_name.lower() in stripped.lower():
                mention_count += 1
                mentioned_in.append(f"Q{q_number}: {current_question}")
                logger.info(
                    f"ai_visibility: MENTIONED in Q{q_number}: {stripped[:120]}"
                    f"{'...' if len(stripped) > 120 else ''}"
                )
            else:
                logger.info(f"ai_visibility: NOT mentioned in Q{q_number}")

    points_per_question = MAX_CITATION_SCORE / TOTAL_QUESTIONS
    score = mention_count * points_per_question
    percentage = (score / MAX_CITATION_SCORE) * 100

    logger.info(
        f"ai_visibility: citation results — "
        f"mentions={mention_count}/{TOTAL_QUESTIONS}, "
        f"score={score:.2f}/{MAX_CITATION_SCORE}, "
        f"percentage={percentage:.0f}%"
    )

    if mentioned_in:
        logger.info("ai_visibility: business appeared in:")
        for entry in mentioned_in:
            logger.info(f"ai_visibility:   - {entry}")
    else:
        logger.info(f"ai_visibility: '{business_name}' was not mentioned in any answer")

    return {
        "mentions": mention_count,
        "total_questions": TOTAL_QUESTIONS,
        "max_score": MAX_CITATION_SCORE,
        "score": round(score, 2),
        "percentage": round(percentage),
        "mentioned_in": mentioned_in,
    }


def _calculate_exposure_fairness(business_name: str, answers_text: str) -> dict:
    _log_section(f"Step 4 — Exposure fairness (position of '{business_name}')")

    lines = answers_text.split("\n")
    position_scores = []
    position_details = []
    q_num = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Q") and ":" in stripped:
            q_num += 1

        if stripped.startswith("A") and ":" in stripped:
            if business_name.lower() not in stripped.lower():
                continue

            answer_body = stripped.split(":", 1)[1].strip()
            raw_names = re.split(r",|;|\d+\.|•|-", answer_body)
            names = [name.strip() for name in raw_names if name.strip()]
            total_names = len(names)

            if total_names == 0:
                logger.warning(
                    f"ai_visibility: Q{q_num} mentions '{business_name}' but no names could be parsed"
                )
                continue

            business_index = None
            for index, name in enumerate(names):
                if business_name.lower() in name.lower():
                    business_index = index
                    break

            if business_index is None:
                logger.warning(
                    f"ai_visibility: Q{q_num} text matched '{business_name}' but name not found in parsed list"
                )
                continue

            if business_index == 0:
                position = "first"
            elif business_index == total_names - 1:
                position = "last"
            else:
                position = "middle"

            position_scores.append(position)
            position_details.append({
                "question": q_num,
                "position": position,
                "index": business_index + 1,
                "total_names": total_names,
            })

            logger.info(
                f"ai_visibility: Q{q_num}: '{business_name}' → "
                f"position {business_index + 1}/{total_names} → {position.upper()}"
            )

    if position_scores:
        first_count = position_scores.count("first")
        middle_count = position_scores.count("middle")
        last_count = position_scores.count("last")

        avg_position = max(
            ["first", "middle", "last"],
            key=lambda position: position_scores.count(position),
        )
        exposure_score = POSITION_POINTS[avg_position]

        logger.info("ai_visibility: position breakdown:")
        logger.info(f"ai_visibility:   first  : {first_count} time(s)")
        logger.info(f"ai_visibility:   middle : {middle_count} time(s)")
        logger.info(f"ai_visibility:   last   : {last_count} time(s)")
        logger.info(f"ai_visibility: average position : {avg_position.upper()}")
        logger.info(
            f"ai_visibility: exposure score : {exposure_score}/{MAX_EXPOSURE_SCORE}"
        )
    else:
        first_count = middle_count = last_count = 0
        avg_position = "not found"
        exposure_score = 0
        logger.warning(
            f"ai_visibility: '{business_name}' not found in any answer list — exposure score: 0"
        )

    return {
        "position_breakdown": {
            "first": first_count,
            "middle": middle_count,
            "last": last_count,
        },
        "average_position": avg_position,
        "max_score": MAX_EXPOSURE_SCORE,
        "score": exposure_score,
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
    scraped_result_path = get_scraped_result_path(business_name)

    logger.info(
        f"ai_visibility: business_name='{business_name}', folder_slug='{folder_slug}'"
    )
    logger.info(f"ai_visibility: looking for scraped data at {scraped_result_path}")

    if not scraped_result_path.exists():
        logger.warning(
            f"ai_visibility: scraped_result.json not found at {scraped_result_path}"
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

    with open(scraped_result_path, "r", encoding="utf-8") as file:
        scraped_data = json.load(file)

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
                prediction = model(display_text[:512])[0]
                sentiment = _normalize_sentiment_label(prediction["label"])
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
    answers_text = _fetch_answers(business_type, business_loc, questions_text)

    # ---------------- Calculate Citation ---------------- #
    citation_score = _calculate_citation_score(business_name, answers_text)
    
    
    # ---------------- Calculate Exposure Fairness ---------------- #
    exposure_fairness = _calculate_exposure_fairness(business_name, answers_text)
    
    
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

    result = {
        "status": "success",
        "business_name": business_name,
        "business_type": business_type,
        "business_location": business_loc,
        "questions": questions_text,
        "answers": answers_text,
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
        f"{citation_score['mentions']}/{TOTAL_QUESTIONS} mentions, "
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
