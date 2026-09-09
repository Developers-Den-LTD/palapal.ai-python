from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.citation_analysis_schema import CitationAnalysisRequest
from services.citation_analysis_services import run_citation_analysis
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["Citation Analysis"],
    prefix="/api",
)


async def _run_citation_analysis_and_notify(payload: CitationAnalysisRequest) -> None:
    webhook_url = str(payload.webhook_url)
    try:
        result = run_citation_analysis(payload)
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

    background_tasks.add_task(_run_citation_analysis_and_notify, payload)
    logger.info(
        "citation_analysis route: accepted — background job queued for webhook "
        f"{payload.webhook_url}"
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
        },
    )
