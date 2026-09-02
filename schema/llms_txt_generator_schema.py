"""Request body for POST /api/generate-llms-txt."""

from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl


class LlmsTxtGeneratorRequest(BaseModel):
    # Website to crawl (e.g. https://example.com).
    website_url: str = Field(
        ...,
        description="Root website URL to crawl (e.g. https://kfc.com.pk)",
        min_length=1,
    )
    # Optional display name for the llms.txt H1 heading.
    business_name: Optional[str] = Field(
        None,
        description="Optional business name for the llms.txt H1 title",
    )
    # Optional ID used only for output folder naming on disk.
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Optional client identifier for output folder naming",
    )
    # If set, job runs in background and result is POSTed to this URL.
    webhook_url: Optional[HttpUrl] = Field(
        None,
        description="Optional URL that receives the generation result via POST when complete",
    )


class LlmsTxtFromUrlsRequest(BaseModel):
    website_url: str = Field(
        ...,
        description="Root website URL (e.g. https://example.com)",
        min_length=1,
    )
    urls: list[str] = Field(
        ...,
        min_length=1,
        description="Selected page URLs to extract and use for llms.txt",
    )
    business_name: Optional[str] = Field(
        None,
        description="Optional business name for the llms.txt H1 title",
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Optional client identifier for output folder naming",
    )
    max_workers: int = Field(
        8,
        ge=1,
        le=20,
        description="Number of pages to extract in parallel",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the generation result via POST when processing completes",
    )


class LlmsTxtCrawlRequest(BaseModel):
    # Website to crawl (domain or full URL).
    website_url: str = Field(
        ...,
        description="Website URL or domain to crawl (e.g. https://example.com)",
        min_length=1,
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the crawl result via POST when processing completes",
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
