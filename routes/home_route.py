from fastapi import APIRouter, Depends  
from services.logger_services import logger
from utils.auth_utils import verify_secret_key  # Import your function here


router = APIRouter(
    tags=["Health Check"]
)

@router.get("/", dependencies=[Depends(verify_secret_key)])
def home():
    logger.info("Home route accessed")
    
    return {
        "message": "Palapalai is working perfectly!",
        "version": "1.0.1",
        "status": "success"
    }

