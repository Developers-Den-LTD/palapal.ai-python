from typing import Optional

from pydantic import BaseModel, Field


class BusinessSearchRequest(BaseModel):
    business_name: str = Field(..., description="The name of the business (e.g., McDonald's)", min_length=1)
    location: str = Field(..., description="The city or region (e.g., Manchester)", min_length=1)
    exact_place: Optional[str] = Field(None, description="Optional specific branch, street, or landmark")


class WebsiteCrawlRequest(BaseModel):
    start_url: str = Field(
        ...,
        description="Website URL to start crawling from (e.g. https://example.com)",
        min_length=1,
    )
    max_pages: int = Field(
        100,
        ge=1,
        le=500,
        description="Maximum number of pages to visit on the same domain",
    )
    delay_seconds: float = Field(
        0.0,
        ge=0.0,
        le=5.0,
        description="Pause between request batches in seconds (usually keep at 0)",
    )
    max_workers: int = Field(
        8,
        ge=1,
        le=20,
        description="Number of pages to fetch in parallel when sitemap is unavailable",
    )
    prefer_sitemap: bool = Field(
        True,
        description="Try sitemap.xml first for much faster URL discovery",
    )
