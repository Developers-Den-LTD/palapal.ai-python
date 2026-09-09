from services.ai_provider_services import PROVIDER_MODELS, fetch_answers_from_all_providers
from services.AI_visibility_scervices import (
    MAX_CITATION_SCORE,
    _all_answer_records,
    _business_name_mentioned,
)
from services.logger_services import logger
from schema.citation_analysis_schema import CitationAnalysisRequest


def _calculate_citation_score(
    business_name: str,
    answer_records: list[dict],
) -> dict:
    total_answers = len(answer_records)
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
            "Answer": ", ".join(record["businesses"]),
        })

    mention_count = len(mentioned_in)
    if total_answers == 0:
        score = 0.0
        percentage = 0.0
    else:
        score = (mention_count / total_answers) * MAX_CITATION_SCORE
        percentage = (mention_count / total_answers) * 100

    return {
        "mentions": mention_count,
        "total_answers": total_answers,
        "total_questions": total_answers,
        "max_score": MAX_CITATION_SCORE,
        "score": round(score, 2),
        "percentage": round(percentage),
        "mentions_by_provider": provider_mentions,
        "mentioned_in": mentioned_in,
    }


def run_citation_analysis(payload: CitationAnalysisRequest) -> dict:
    business_name = payload.business_name.strip()
    business_id = payload.business_id
    business_type = payload.business_type.strip()
    business_loc = payload.business_loc.strip()
    questions = list(payload.custom_questions)

    logger.info(
        "citation_analysis: started — "
        f"business='{business_name}', business_id='{business_id}', "
        f"type='{business_type}', loc='{business_loc}', "
        f"questions={len(questions)}"
    )

    provider_results = fetch_answers_from_all_providers(
        business_type,
        business_loc,
        questions,
    )
    answer_records = _all_answer_records(provider_results, questions)
    citation_score = _calculate_citation_score(business_name, answer_records)

    successful_providers = sum(
        result["status"] == "success"
        for result in provider_results.values()
    )
    analysis_status = (
        "success"
        if successful_providers == len(PROVIDER_MODELS)
        else "partial"
    )

    logger.info(
        "citation_analysis: completed — "
        f"status={analysis_status}, "
        f"mentions={citation_score['mentions']}/{citation_score['total_answers']}, "
        f"score={citation_score['score']}/{MAX_CITATION_SCORE}"
    )

    return {
        "status": analysis_status,
        "business_name": business_name,
        "business_id": business_id,
        "business_type": business_type,
        "business_location": business_loc,
        "questions": questions,
        "answers": provider_results,
        "successful_providers": successful_providers,
        "total_providers": len(PROVIDER_MODELS),
        "citation_score": citation_score,
    }
