import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.social_content_schema import (
    GenerateRequest,
    GuidelineSuggestRequest,
    IdeasRequest,
)
from services.logger_services import logger
from services.social_content_services import (
    generate_content,
    suggest_guidelines,
    suggest_ideas,
)
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["Social Content"],
    prefix="/api",
)


@router.post("/social-content/guidelines/suggest")
def social_content_guidelines_suggest(payload: GuidelineSuggestRequest):
    logger.info(
        "social_content route: POST /api/social-content/guidelines/suggest — "
        f"business='{payload.business.business_name}', "
        f"business_id='{payload.business.business_id}', "
        f"has_current={payload.current_guidelines is not None}"
    )
    try:
        result = suggest_guidelines(payload)
    except ValueError as exc:
        logger.warning(f"social_content route: guidelines suggest rejected — {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except RuntimeError as exc:
        logger.error(f"social_content route: guidelines suggest unavailable — {exc}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception(f"social_content route: guidelines suggest failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Guideline suggestion failed: {str(exc)}",
        )

    logger.info(
        "social_content route: guidelines suggest returned — "
        f"business='{payload.business.business_name}', "
        f"model='{result['model']}', warnings={len(result['warnings'])}"
    )
    return result


@router.post("/social-content/ideas")
def social_content_ideas(payload: IdeasRequest):
    logger.info(
        "social_content route: POST /api/social-content/ideas — "
        f"business='{payload.business.business_name}', "
        f"business_id='{payload.business.business_id}', "
        f"content_type='{payload.content_type}', "
        f"platforms={payload.target_platforms}"
    )
    try:
        result = suggest_ideas(payload)
    except ValueError as exc:
        logger.warning(f"social_content route: ideas rejected — {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except RuntimeError as exc:
        logger.error(f"social_content route: ideas unavailable — {exc}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception(f"social_content route: ideas failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Idea generation failed: {str(exc)}",
        )

    logger.info(
        "social_content route: ideas returned — "
        f"business='{payload.business.business_name}', status={result['status']}, "
        f"ideas={len(result['ideas'])}, model='{result['model']}'"
    )
    return result


async def _run_social_content_generate_and_notify(
    payload: GenerateRequest,
    job_id: str,
) -> None:
    """
    Run image generation off the event loop so concurrent API requests can
    still be accepted while a job is in progress.
    """
    webhook_url = str(payload.webhook_url)
    try:
        result = await asyncio.to_thread(generate_content, payload, job_id)
        logger.info(
            "social_content background: completed — "
            f"job_id='{job_id}', status={result['status']}, "
            f"provider_used='{result.get('provider_used')}', "
            f"fallback_used={result.get('fallback_used')}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as exc:
        logger.exception(f"social_content background: failed — job_id='{job_id}', error={exc}")
        try:
            await post_to_webhook(
                webhook_url,
                {
                    "status": "error",
                    "event": "social_content_failed",
                    "message": str(exc),
                    "job_id": job_id,
                    "business_name": payload.business.business_name,
                    "business_id": payload.business.business_id,
                },
            )
        except Exception as webhook_exc:
            logger.exception(
                "social_content background: failed to notify webhook after error — "
                f"job_id='{job_id}', error={webhook_exc}"
            )


@router.post("/social-content/generate")
async def social_content_generate(
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
):
    job_id = uuid.uuid4().hex
    logger.info(
        "social_content route: POST /api/social-content/generate — "
        f"job_id='{job_id}', "
        f"business='{payload.business.business_name}', "
        f"business_id='{payload.business.business_id}', "
        f"content_type='{payload.idea.content_type if payload.idea else payload.content_type}', "
        f"from_idea={payload.idea is not None}, "
        f"platforms={payload.target_platforms}, "
        f"aspect_ratio='{payload.aspect_ratio}', "
        f"provider='{payload.provider}', quality='{payload.quality}', "
        f"has_reference={payload.reference_image_url is not None}, "
        f"reference_use={payload.reference_use}, "
        f"overlay_areas={payload.overlay_areas}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_social_content_generate_and_notify, payload, job_id)
    logger.info(
        "social_content route: accepted — background job queued for webhook "
        f"{payload.webhook_url}, job_id='{job_id}'"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "job_id": job_id,
            "message": (
                "Social content generation started. "
                "Results will be sent to the webhook URL."
            ),
            "webhook_url": str(payload.webhook_url),
            "business_name": payload.business.business_name,
            "business_id": payload.business.business_id,
        },
    )
