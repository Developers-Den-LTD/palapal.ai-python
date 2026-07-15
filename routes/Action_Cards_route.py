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
        f"business='{payload.business_name}', id='{payload.business_id}'"
    )
    try:
        # 1. Fetch data using the business name as before
        result = get_action_cards_data(payload.business_name)
        
        # 2. Append the incoming business_id to the output dictionary
        if isinstance(result, dict):
            result["business_id"] = payload.business_id
        
        if result.get("status") == "success":
            logger.info(
                "action_cards route: request completed successfully — "
                f"business='{result.get('business_name')}', id='{result.get('business_id')}', source='{result.get('source')}'"
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