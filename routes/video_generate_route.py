from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.video_generate_schema import VideoGenerateRequest
from services.logger_services import logger
from services.video_generate_services import VEO_MODEL, generate_business_video
from services.webhook_poster import post_to_webhook
from utils.scraped_result_paths import build_scrape_storage_slug

router = APIRouter(
    tags=["Video Generate"],
    prefix="/api",
)

_running_jobs: set[str] = set()


async def _run_video_generate_and_notify(payload: VideoGenerateRequest) -> None:
    job_key = build_scrape_storage_slug(payload.business_name, payload.business_id)
    webhook_url = str(payload.webhook_url)
    try:
        result = await generate_business_video(payload)
        logger.info(
            f"video_generate background: completed — status={result['status']}, "
            f"segments={result.get('segments_completed')}/"
            f"{result.get('segments_requested')}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as exc:
        logger.exception(f"video_generate background: failed — {exc}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(exc),
                "business_name": payload.business_name,
                "business_id": payload.business_id,
                "model": VEO_MODEL,
            },
        )
    finally:
        _running_jobs.discard(job_key)


@router.post("/video-generate")
async def video_generate(
    payload: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
):
    job_key = build_scrape_storage_slug(payload.business_name, payload.business_id)
    logger.info(
        "video_generate route: POST /api/video-generate — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"beats={len(payload.beats)}, "
        f"images={len(payload.image_urls)}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    if job_key in _running_jobs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A video generation job is already running for this business. "
                "Wait for the webhook before starting another."
            ),
        )

    _running_jobs.add(job_key)
    background_tasks.add_task(_run_video_generate_and_notify, payload)
    logger.info(
        f"video_generate route: accepted — background job queued for webhook "
        f"{payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": (
                "Video generation started with Veo 3.1 Fast Generate Preview "
                "(720p, audio, 8-second clips). Results will be sent to the webhook URL."
            ),
            "webhook_url": str(payload.webhook_url),
            "business_name": payload.business_name,
            "business_id": payload.business_id,
            "model": VEO_MODEL,
            "resolution": "720p",
            "clip_seconds": 8,
            "segments_requested": 1 + len(payload.beats),
        },
    )
