from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class ReviewExtensionPlatformsRequest(BaseModel):
    business_name: str = Field(
        ...,
        min_length=1,
        description="Business name used for scraping_results/<slug>[_business_id]/",
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Client-side business identifier used in the storage folder name",
    )
    facebook_url: Optional[HttpUrl] = Field(
        None,
        description=(
            "Facebook page reviews URL, e.g. "
            "https://www.facebook.com/McDonaldsPK/reviews"
        ),
    )
    trustpilot_url: Optional[HttpUrl] = Field(
        None,
        description=(
            "Trustpilot company URL or domain, e.g. "
            "https://www.trustpilot.com/review/mcdonalds.com"
        ),
    )
    feefo_url: Optional[HttpUrl] = Field(
        None,
        description=(
            "Feefo brand reviews URL, e.g. "
            "https://www.feefo.com/en-GB/reviews/tesco-mobile"
        ),
    )
    max_reviews: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum number of reviews to scrape per platform",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the scrape result via POST when processing completes",
    )

    @field_validator("facebook_url", "trustpilot_url", "feefo_url", mode="before")
    @classmethod
    def empty_url_as_none(cls, value):
        """Treat missing/blank URLs as omitted so only provided platforms run."""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def require_at_least_one_platform_url(self):
        if not self.facebook_url and not self.trustpilot_url and not self.feefo_url:
            raise ValueError(
                "At least one of facebook_url, trustpilot_url, or feefo_url is required"
            )
        return self
