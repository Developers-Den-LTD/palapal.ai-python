from fastapi import APIRouter, Depends  
from services.logger_services import logger


router = APIRouter(
    tags=["Health Check"]
)

@router.get("/")
def home():
    logger.info("Home route accessed")
    
    return {
        "message": "Palapalai is working perfectly!",
        "version": "1.0.1",
        "status": "success"
    }

