from fastapi import APIRouter, HTTPException, status

from schema.video_prompt_schema import VideoPromptRequest
from services.logger_services import logger
from services.video_prompt_services import generate_video_prompts

router = APIRouter(
    tags=["Video Prompt"],
    prefix="/api",
)


@router.post(
    "/video-prompts",
    status_code=status.HTTP_200_OK,
)
def create_video_prompts(payload: VideoPromptRequest):
    logger.info(
        "video_prompt route: POST /api/video-prompts — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"target_seconds={payload.target_seconds}"
    )
    try:
        return generate_video_prompts(payload)
    except ValueError as exc:
        logger.warning(f"video_prompt route: validation/generation issue — {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(f"video_prompt route: failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate video prompts: {exc}",
        ) from exc
