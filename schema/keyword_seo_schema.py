from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator


class KeywordItem(BaseModel):
    keyword: str = Field(
        ...,
        description="Exact search phrase to check on Google",
        min_length=1,
    )
    priority: str = Field(
        ...,
        description=(
            "Keyword importance (high, medium, low). "
            "Used only to break ties when choosing matched_keyword"
        ),
        min_length=1,
    )

    @field_validator("keyword", "priority")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("priority")
    @classmethod
    def _normalize_priority(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"high", "medium", "low"}:
            raise ValueError("priority must be one of: high, medium, low")
        return normalized


class KeywordSeoRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description=(
            "Client-side business identifier. When provided, S3 results are "
            "stored under businessname_businessid."
        ),
    )
    website_url: str = Field(
        ...,
        description="Business website URL used to match Google organic results",
        min_length=1,
    )
    country_code: str = Field(
        ...,
        description="Google search country code for Apify (e.g. gb, us)",
        min_length=2,
        max_length=2,
    )
    keywords: list[KeywordItem] = Field(
        ...,
        description=(
            "Keywords to check on Google in parallel (max 20). "
            "priority is metadata used only to break ties for matched_keyword"
        ),
        min_length=1,
        max_length=20,
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the result via POST when processing completes",
    )

    @field_validator("business_name", "website_url")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("country_code")
    @classmethod
    def _normalize_country_code(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if len(cleaned) != 2 or not cleaned.isalpha():
            raise ValueError("country_code must be a 2-letter code (e.g. gb, us)")
        return cleaned
