import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.citation_analysis_schema import CitationAnalysisRequest
from services.citation_analysis_services import (
    run_citation_analysis,
    validate_custom_questions,
)
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["Citation Analysis"],
    prefix="/api",
)


async def _run_citation_analysis_and_notify(payload: CitationAnalysisRequest) -> None:
    """
    Run multi-provider citation work off the event loop so concurrent
    API requests can still be accepted while a job is in progress.
    """
    webhook_url = str(payload.webhook_url)
    try:
        result = await asyncio.to_thread(run_citation_analysis, payload)
        logger.info(
            "citation_analysis background: completed — "
            f"status={result['status']}, "
            f"citation_score={result['citation_score']['score']}/"
            f"{result['citation_score']['max_score']}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as e:
        logger.exception(f"citation_analysis background: failed — {e}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(e),
                "business_name": payload.business_name,
                "business_id": payload.business_id,
                "business_type": payload.business_type,
                "business_location": payload.business_loc,
            },
        )


@router.post("/citation-analysis")
async def citation_analysis(
    payload: CitationAnalysisRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "citation_analysis route: POST /api/citation-analysis — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"type='{payload.business_type}', "
        f"loc='{payload.business_loc}', "
        f"questions={len(payload.custom_questions)}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    validation = await asyncio.to_thread(
        validate_custom_questions,
        payload.custom_questions,
        payload.business_type,
        payload.business_loc,
    )

    if not validation.get("all_valid"):
        logger.warning(
            "citation_analysis route: rejected invalid custom questions — "
            f"business='{payload.business_name}', "
            f"invalid_count={sum(1 for r in validation.get('results', []) if not r.get('valid'))}"
        )
        status_code = status.HTTP_400_BAD_REQUEST
        if validation.get("validation_error"):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        raise HTTPException(
            status_code=status_code,
            detail={
                "status": "invalid_queries",
                "message": (
                    "One or more custom questions are invalid. "
                    "Citation analysis was not started."
                ),
                "results": validation.get("results", []),
                "validator_model": validation.get("validator_model"),
            },
        )

    background_tasks.add_task(_run_citation_analysis_and_notify, payload)
    logger.info(
        "citation_analysis route: accepted — queries validated, "
        f"background job queued for webhook {payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "Citation analysis started. Results will be sent to the webhook URL.",
            "webhook_url": str(payload.webhook_url),
            "business_name": payload.business_name,
            "business_type": payload.business_type,
            "business_loc": payload.business_loc,
            "business_id": payload.business_id,
            "question_count": len(payload.custom_questions),
            "validation": {
                "all_valid": True,
                "results": validation.get("results", []),
                "validator_model": validation.get("validator_model"),
            },
        },
    )
