import json

import httpx

from services.logger_services import logger


async def post_to_webhook(webhook_url: str, payload: dict) -> None:
    logger.info(
        f"webhook_poster: posting payload to {webhook_url} — "
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json", "x-webhook-secret": "some-random-secret-string"},
            )
            if response.status_code >= 400:
                logger.error(
                    f"""
            Webhook failed
            URL: {webhook_url}
            Status: {response.status_code}
            Headers: {dict(response.headers)}
            Body: {response.text}
                    """
                )

                raise RuntimeError(
                    f"Webhook POST failed: status={response.status_code}"
                )
    except httpx.ConnectError:
        logger.error(
            f"webhook_poster: webhook URL is incorrect or unreachable — '{webhook_url}'"
        )
        raise

    logger.info(f"webhook_poster: result delivered to {webhook_url}")
