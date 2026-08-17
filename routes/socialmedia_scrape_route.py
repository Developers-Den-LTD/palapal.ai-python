from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from schema.socialmedia_schema import SocialMediaConsistencyRequest, SocialMediaRequest
from services.logger_services import logger
from services.social_media_scrapper.consistency import check_social_media_consistency
from services.socialmedia_services import (
    resolve_platform_handles,
    scrape_social_media,
    SocialMediaScrapeContext,
)
from services.webhook_poster import post_to_webhook
from utils.scraped_result_paths import build_scrape_storage_slug

router = APIRouter(tags=["Social Media"], prefix="/api")


async def _run_social_media_scrape(
    payload: SocialMediaRequest,
    handles: SocialMediaScrapeContext,
) -> None:
    """Background worker: scrape platforms, then POST the result (or error) to webhook."""
    webhook_url = str(payload.webhook_url)
    try:
        logger.info(
            "socialmedia [route] background: started — "
            f"\nbusiness_id='{payload.business_id}', "
            f"\ninstagram_url='{payload.instagram_url}', "
            f"\nfacebook_url='{payload.facebook_url}', "
            f"\ntwitter_url='{payload.twitter_url}', "
            f"\nposts_limit={payload.posts_limit}, "
            f"\nwebhook_url='{webhook_url}'"
        )
        result = await scrape_social_media(payload, handles)
        logger.info(
            "socialmedia [route] background: completed — "
            f"storage_slug='{result.get('storage_slug')}', "
            f"saved_to='{result.get('saved_to')}'"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as exc:
        logger.exception(f"socialmedia [route] background: failed — {exc}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(exc),
                "business_id": str(payload.business_id),
                "instagram_url": str(payload.instagram_url) if payload.instagram_url else None,
                "facebook_url": str(payload.facebook_url) if payload.facebook_url else None,
                "twitter_url": str(payload.twitter_url) if payload.twitter_url else None,
            },
        )


@router.post("/socialmedia-scrape", status_code=status.HTTP_202_ACCEPTED)
def socialmedia_scrape(
    payload: SocialMediaRequest,
    background_tasks: BackgroundTasks,
):
    """
    Scrape Instagram, Facebook, and/or X (Twitter) in the background.

    Results (including consistency) are POSTed to webhook_url when scraping completes.

    - Instagram: apify/instagram-scraper + seemuapps/instagram-contact-scraper
    - Facebook:  apify/facebook-pages-scraper + apify/facebook-posts-scraper
    - Twitter:   igolaizola/x-twitter-scraper-ppe (handle extracted from profile URL)
    """
    logger.info(
        "socialmedia [route]: POST /api/socialmedia-scrape — "
        f"\nbusiness_id='{payload.business_id}', "
        f"\ninstagram_url='{payload.instagram_url}', "
        f"\nfacebook_url='{payload.facebook_url}', "
        f"\ntwitter_url='{payload.twitter_url}', "
        f"\nposts_limit={payload.posts_limit}, "
        f"\nwebhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    try:
        handles = resolve_platform_handles(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    slug = build_scrape_storage_slug(handles.storage_slug_name, payload.business_id)
    background_tasks.add_task(_run_social_media_scrape, payload, handles)

    logger.info(
        "socialmedia [route]: accepted for background processing — "
        f"storage_slug='{slug}', business_id='{payload.business_id}', "
        f"webhook_url='{payload.webhook_url}'"
    )

    return {
        "status": "accepted",
        "message": "Social media scrape started. Results will be sent to the webhook URL.",
        "business_id": str(payload.business_id),
        "instagram_url": str(payload.instagram_url) if payload.instagram_url else None,
        "facebook_url": str(payload.facebook_url) if payload.facebook_url else None,
        "twitter_url": str(payload.twitter_url) if payload.twitter_url else None,
        "posts_limit": payload.posts_limit,
        "webhook_url": str(payload.webhook_url),
        "saved_to": f"socialmedia/{slug}.json",
    }


@router.post("/socialmedia-consistency", status_code=status.HTTP_200_OK)
async def socialmedia_consistency(payload: SocialMediaConsistencyRequest):
    """
    Check NAME, ADDRESS, and PHONE consistency across scraped social data.

    Send the instagram / facebook / twitter blocks from a scrape result.
    The full saved JSON can be posted as-is — extra fields are ignored.

    Name:   Instagram profile.name (fallback username), Facebook profile.name, Twitter username
    Address: Instagram + Facebook profile.address
    Phone:  Instagram + Facebook contact.phones (normalized E.164 match)
    """
    logger.info(
        "socialmedia [route]: POST /api/socialmedia-consistency — "
        f"business_id='{payload.business_id}', "
        f"instagram={'yes' if payload.instagram else 'no'}, "
        f"facebook={'yes' if payload.facebook else 'no'}, "
        f"twitter={'yes' if payload.twitter else 'no'}"
    )

    consistency = await check_social_media_consistency(
        {
            "instagram": payload.instagram,
            "facebook": payload.facebook,
            "twitter": payload.twitter,
        }
    )

    name_ok = all(
        entry["consistent"]
        for entry in consistency["Name"].values()
        if entry["value"] is not None
    )
    address_ok = all(
        entry["consistent"]
        for entry in consistency["Address"].values()
        if entry["value"] is not None
    )
    phone_ok = all(
        entry["consistent"]
        for entry in consistency["Phone"].values()
        if entry["value"] is not None
    )

    logger.info(
        "socialmedia [route]: consistency check completed — "
        f"name={name_ok}, address={address_ok}, phone={phone_ok}"
    )

    return {
        "status": "success",
        "business_id": str(payload.business_id) if payload.business_id is not None else None,
        **consistency,
    }
