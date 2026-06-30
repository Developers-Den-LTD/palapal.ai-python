
import os
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from utils.auth_utils import verify_secret_key
from services.logger_services import logger

router = APIRouter(
    prefix="/logs",
    tags=["Logs"]
)

LOG_FILE_PATH = "logs/app.log"

@router.get("/download", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_secret_key)])
async def download_logs():
    """
    Downloads the application log file.
    Requires X-API-KEY header.
    """
    if not os.path.exists(LOG_FILE_PATH):
        logger.error(f"Log file not found at {LOG_FILE_PATH}")
        raise HTTPException(status_code=404, detail="Log file not found")
    
    return FileResponse(
        path=LOG_FILE_PATH,
        filename="app.log",
        media_type="text/plain"
    )
