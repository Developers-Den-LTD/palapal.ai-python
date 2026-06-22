from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks

from schema.technical_foundation_schema import TechnicalFoundationRequest
from services.logger_services import logger
from services.technical_foundation import check_technical_foundation
from utils.auth_utils import verify_secret_key

router = APIRouter(
    tags=["Technical Foundation"],
    prefix="/api",
)


def run_technical_foundation(website_url: str):
    try:
        logger.info(
            f"technical_foundation background: started — website='{website_url}'"
        )

        result = check_technical_foundation(website_url)

        if result["status"] == "success":
            logger.info(
                "technical_foundation background: completed successfully — "
                f"website='{result.get('website')}', "
                f"score={result.get('DDI_technical_foundation_Result')}"
            )
        else:
            logger.warning(
                "technical_foundation background: completed with error — "
                f"website='{result.get('website')}', "
                f"message={result.get('message')}"
            )

    except Exception as e:
        logger.exception(
            f"technical_foundation background: failed — website='{website_url}', error={e}"
        )


@router.post(
    "/technical-foundation",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_secret_key)],
)
def technical_foundation(
    payload: TechnicalFoundationRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "technical_foundation route: POST /api/technical-foundation — request received"
    )

    background_tasks.add_task(
        run_technical_foundation,
        payload.website_url,
    )

    return {
        "status": "accepted",
        "message": "Technical foundation analysis started in background.",
        "website_url": payload.website_url,
    }