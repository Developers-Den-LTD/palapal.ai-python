from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SocialMediaRequest(BaseModel):
    """
    Social media scrape request.
    Provide instagram_url, facebook_url, and/or twitter_url — at least one is required.
    """

    instagram_url: Optional[HttpUrl] = Field(
        None,
        description="Instagram profile URL, e.g. https://www.instagram.com/kfc/",
    )
    facebook_url: Optional[HttpUrl] = Field(
        None,
        description="Facebook page URL, e.g. https://www.facebook.com/kfc/",
    )
    twitter_url: Optional[HttpUrl] = Field(
        None,
        description="X (Twitter) profile URL, e.g. https://x.com/kfc",
    )
    business_id: Union[str, int] = Field(
        ...,
        description="Client-side business identifier used in the saved JSON filename",
    )
    posts_limit: int = Field(
        10,
        ge=1,
        le=50,
        description="How many posts to fetch per platform for tone-of-voice analysis",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the scrape result via POST when processing completes",
    )

    @model_validator(mode="after")
    def require_at_least_one_url(self):
        if not self.instagram_url and not self.facebook_url and not self.twitter_url:
            raise ValueError(
                "At least one of instagram_url, facebook_url, or twitter_url is required"
            )
        return self


class SocialMediaConsistencyRequest(BaseModel):
    """
    Social media consistency check request.

    Pass the scraped platform blocks (instagram, facebook, twitter).
    You can send the full saved scrape JSON — extra fields are ignored.
    At least one platform block is required.
    """

    model_config = ConfigDict(extra="ignore")

    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Optional business identifier echoed back in the response",
    )

    status: Optional[str] = None
    scraped_at: Optional[str] = None
    instagram: Optional[dict[str, Any]] = Field(
        None,
        description="Instagram scrape block (username, profile, contact, etc.)",
    )
    facebook: Optional[dict[str, Any]] = Field(
        None,
        description="Facebook scrape block (profile, contact, etc.)",
    )
    twitter: Optional[dict[str, Any]] = Field(
        None,
        description="Twitter scrape block (username, profile, etc.)",
    )

    @model_validator(mode="after")
    def require_at_least_one_platform(self):
        if not self.instagram and not self.facebook and not self.twitter:
            raise ValueError(
                "At least one of instagram, facebook, or twitter is required"
            )
        return self

