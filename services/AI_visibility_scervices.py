import re

from openai import OpenAI

from core.config import settings
from schema.AI_visibility_schema import AIVisibilityRequest
from services.logger_services import logger

TOTAL_QUESTIONS = 10
MAX_CITATION_SCORE = 15
MAX_EXPOSURE_SCORE = 13

POSITION_POINTS = {
    "first": 13,
    "middle": 7,
    "last": 3,
}

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
For each answer, list the top 3-5 specific business names.

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

    citation_score = _calculate_citation_score(business_name, answers_text)
    exposure_fairness = _calculate_exposure_fairness(business_name, answers_text)

    result = {
        "status": "success",
        "business_name": business_name,
        "business_type": business_type,
        "business_location": business_loc,
        "questions": questions_text,
        "answers": answers_text,
        "citation_score": citation_score,
        "exposure_fairness": exposure_fairness,
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

    return result
