from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from services.logger_services import logger

router = APIRouter(
    prefix="/logs",
    tags=["Logs"],
)

# Resolve against project root so the path works regardless of process CWD
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE_PATH = _PROJECT_ROOT / "logs" / "app.log"
DOWNLOAD_FILENAME = "app.log"


@router.get("/download", status_code=status.HTTP_200_OK)
async def download_logs():
    """
    Download the application log file.
    Opening this URL in a browser triggers a file download.
    """
    # Flush handlers so the file on disk includes the latest buffered lines
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass

    if not LOG_FILE_PATH.is_file():
        logger.error(f"Log file not found at {LOG_FILE_PATH}")
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(
        path=str(LOG_FILE_PATH),
        filename=DOWNLOAD_FILENAME,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={
            "Content-Disposition": f'attachment; filename="{DOWNLOAD_FILENAME}"',
        },
    )
