from fastapi import APIRouter, Depends, HTTPException, status

from schema.action_cards_schema import ActionCardsRequest
from services.Action_Cards_services import get_action_cards_data
from services.logger_services import logger
from utils.auth_utils import verify_secret_key

router = APIRouter(
    tags=["Action Cards"],
    prefix="/api",
)


@router.post(
    "/action-cards",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret_key)],
)
def action_cards(payload: ActionCardsRequest):
    logger.info(
        "action_cards route: POST /api/action-cards — "
        f"business='{payload.business_name}'"
    )
    try:
        result = get_action_cards_data(payload.business_name)
        if result["status"] == "success":
            logger.info(
                "action_cards route: request completed successfully — "
                f"business='{result['business_name']}', source='{result['source']}'"
            )
        else:
            logger.warning(
                "action_cards route: request completed with error — "
                f"message={result.get('message')}"
            )
        return result
    except Exception as e:
        logger.exception(f"action_cards route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Action cards lookup failed: {str(e)}",
        )
