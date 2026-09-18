from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator


class AIKeywordGenerationRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business",
        min_length=1,
    )
    industry: str = Field(
        ...,
        description="Industry or business category (e.g. restaurant, dental clinic)",
        min_length=1,
    )
    services: list[str] = Field(
        ...,
        description="Services the business offers",
        min_length=1,
    )
    website_url: str = Field(
        ...,
        description="Business website URL used to extract titles, headings, text, etc.",
        min_length=1,
    )
    location: str = Field(
        ...,
        description="City or region (e.g. Manchester)",
        min_length=1,
    )
    existing_content: Optional[str] = Field(
        None,
        description="Optional extra business content supplied by the client",
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Optional client-side business identifier",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the keyword generation result via POST when processing completes",
    )

    @field_validator(
        "business_name",
        "industry",
        "website_url",
        "location",
    )
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("services")
    @classmethod
    def _clean_services(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and str(item).strip()]
        if not cleaned:
            raise ValueError("services must contain at least one non-empty service")
        return cleaned

    @field_validator("existing_content")
    @classmethod
    def _clean_existing_content(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
