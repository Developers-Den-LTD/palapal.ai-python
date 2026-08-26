import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.page_audit_schema import PageAuditRequest
from services.logger_services import logger
from services.page_audit_services import run_page_audit
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["Page Audit"],
    prefix="/api",
)


async def _run_page_audit_and_notify(payload: PageAuditRequest) -> None:
    webhook_url = str(payload.webhook_url)
    urls = [str(u) for u in payload.urls]
    try:
        result = await run_page_audit(urls)
        logger.info(
            f"page_audit background: completed — "
            f"status={result.get('status')}, pages={len(result.get('urls') or [])}"
        )
        logger.info(
            "page_audit background: webhook payload — "
            f"url='{webhook_url}', "
            f"body={json.dumps(result, ensure_ascii=False, default=str)}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as exc:
        logger.exception(f"page_audit background: failed — {exc}")
        error_payload = {
            "status": "error",
            "message": str(exc),
            "urls": urls,
        }
        logger.info(
            "page_audit background: webhook payload — "
            f"url='{webhook_url}', "
            f"body={json.dumps(error_payload, ensure_ascii=False, default=str)}"
        )
        await post_to_webhook(webhook_url, error_payload)


@router.post("/page-audit")
async def page_audit(
    payload: PageAuditRequest,
    background_tasks: BackgroundTasks,
):
    urls = [str(u) for u in payload.urls]
    logger.info(
        f"page_audit route: POST /api/page-audit — "
        f"urls={urls}, webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_page_audit_and_notify, payload)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "Page audit started. Results will be sent to the webhook URL.",
            "webhook_url": str(payload.webhook_url),
            "url_count": len(urls),
            "urls": urls,
        },
    )
