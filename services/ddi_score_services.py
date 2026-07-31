import asyncio

from schema.AI_visibility_schema import AIVisibilityRequest
from schema.ddi_score_schema import DDIScoreRequest
from services.AI_visibility_scervices import (
    MAX_DDI_AI_VISIBILITY_SCORE,
    analyze_ai_visibility,
)
from services.Review_velocity_services import (
    MAX_DDI_REPUTATION_SCORE,
    analyze_reputation_score,
)
from services.technical_foundation import (
    MAX_DDI_TECHNICAL_FOUNDATION_SCORE,
    check_technical_foundation,
)
from services.logger_services import logger
from services.s3_service import upload_ddi_score_result_to_s3, get_ddi_score_s3_key
from core.config import settings

MAX_DDI_FINAL_SCORE = (
    MAX_DDI_AI_VISIBILITY_SCORE
    + MAX_DDI_REPUTATION_SCORE
    + MAX_DDI_TECHNICAL_FOUNDATION_SCORE
)


def _log_section(title: str) -> None:
    logger.info(f"ddi_score: {'=' * 60}")
    logger.info(f"ddi_score: {title}")
    logger.info(f"ddi_score: {'=' * 60}")


def _extract_score(result: dict | None, score_key: str) -> float:
    if not result:
        return 0.0
    value = result.get(score_key, 0)
    return float(value) if value is not None else 0.0


def _format_error(exc: BaseException) -> dict:
    return {
        "status": "error",
        "message": str(exc),
    }


async def calculate_ddi_score(payload: DDIScoreRequest) -> dict:
    _log_section("DDI Score — starting parallel analysis")
    logger.info(
        f"ddi_score: business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"type='{payload.business_type}', loc='{payload.business_loc}', "
        f"website='{payload.website_url}'"
    )

    ai_payload = AIVisibilityRequest(
        business_name=payload.business_name,
        business_id=payload.business_id,
        business_type=payload.business_type,
        business_loc=payload.business_loc,
    )

    ai_task = asyncio.to_thread(analyze_ai_visibility, ai_payload)

    reputation_task = asyncio.to_thread(
        analyze_reputation_score,
        payload.business_name,
        payload.business_id,
    )
    
    technical_task = asyncio.to_thread(
        check_technical_foundation,
        payload.website_url,
        payload.business_name,
        payload.business_id,
    )

    ai_result, reputation_result, technical_result = await asyncio.gather(
        ai_task,
        reputation_task,
        technical_task,
        return_exceptions=True,
    )

    errors: dict[str, str] = {}

    if isinstance(ai_result, BaseException):
        logger.exception(f"ddi_score: AI visibility failed — {ai_result}")
        errors["ai_visibility"] = str(ai_result)
        ai_result = None

    if isinstance(reputation_result, BaseException):
        logger.exception(f"ddi_score: reputation score failed — {reputation_result}")
        errors["reputation"] = str(reputation_result)
        reputation_result = None

    if isinstance(technical_result, BaseException):
        logger.exception(
            f"ddi_score: technical foundation failed — {technical_result}"
        )
        errors["technical_foundation"] = str(technical_result)
        technical_result = None

    ai_score = _extract_score(ai_result, "DDI_AI_visibility_result")
    reputation_score = _extract_score(
        reputation_result, "DDI_Reputation_Score_Result"
    )
    technical_score = _extract_score(
        technical_result, "DDI_technical_foundation_Result"
    )

    ddi_final_score = round(ai_score + reputation_score + technical_score, 2)

    if errors:
        status = "partial" if any(
            r is not None for r in (ai_result, reputation_result, technical_result)
        ) else "error"
    else:
        status = "success"

    _log_section("DDI Score — Final Summary")
    logger.info(
        f"ddi_score: ai_visibility={ai_score}/{MAX_DDI_AI_VISIBILITY_SCORE}, "
        f"reputation={reputation_score}/{MAX_DDI_REPUTATION_SCORE}, "
        f"technical_foundation={technical_score}/{MAX_DDI_TECHNICAL_FOUNDATION_SCORE}, "
        f"final={ddi_final_score}/{MAX_DDI_FINAL_SCORE}, status={status}"
    )

    response = {
        "status": status,
        "business_name": payload.business_name,
        "business_id": payload.business_id,
        "business_type": payload.business_type,
        "business_location": payload.business_loc,
        "website_url": payload.website_url,
        "breakdown": {
            "ai_visibility": {
                "score": ai_score,
                "max_score": MAX_DDI_AI_VISIBILITY_SCORE,
            },
            "reputation": {
                "score": reputation_score,
                "max_score": MAX_DDI_REPUTATION_SCORE,
            },
            "technical_foundation": {
                "score": technical_score,
                "max_score": MAX_DDI_TECHNICAL_FOUNDATION_SCORE,
            },
        },
        "DDI_final_score": ddi_final_score,
        "max_DDI_final_score": MAX_DDI_FINAL_SCORE,
        "ai_visibility": ai_result if ai_result is not None else _format_error(
            Exception(errors.get("ai_visibility", "Unknown error"))
        ),
        "reputation": reputation_result
        if reputation_result is not None
        else _format_error(
            Exception(errors.get("reputation", "Unknown error"))
        ),
        "technical_foundation": technical_result
        if technical_result is not None
        else _format_error(
            Exception(errors.get("technical_foundation", "Unknown error"))
        ),
    }

    if errors:
        response["errors"] = errors

    s3_key = get_ddi_score_s3_key(payload.business_name, payload.business_id)
    if upload_ddi_score_result_to_s3(
        payload.business_name,
        response,
        payload.business_id,
    ):
        logger.info(
            "ddi_score: stored result in S3 — "
            f"bucket={settings.AWS_S3_BUCKET}, key={s3_key}"
        )
    else:
        logger.error(
            "ddi_score: failed to store result in S3 — "
            f"business='{payload.business_name}', key={s3_key}"
        )

    return response
