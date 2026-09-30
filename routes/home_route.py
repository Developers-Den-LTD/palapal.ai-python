from fastapi import APIRouter  
from services.logger_services import logger


router = APIRouter(
    tags=["Health Check"]
)

@router.get("/")
def home():
    logger.info("Home route accessed")
    
    return {
        "message": "Palapalai is working perfectly!",
        "version": "2.1.0",
        "status": "success"
    }

