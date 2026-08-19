from typing import Optional, Union
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from schema.video_prompt_schema import VideoBeat


def _normalize_image_url(url: str) -> str:
    url = str(url).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"image_urls must be http(s) URLs, got: {url}")
    encoded_path = quote(parts.path, safe="/")
    return urlunsplit(
        (parts.scheme, parts.netloc, encoded_path, parts.query, parts.fragment)
    )


class VideoGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    business_name: str = Field(..., min_length=1)
    business_id: Optional[Union[str, int]] = None
    master_prompt: str = Field(..., min_length=1)
    beats: list[VideoBeat] = Field(default_factory=list)
    image_urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Reference images for the first 8-second clip. Up to 3 are sent to Veo.",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the final video result via POST when processing completes",
    )
    target_seconds: Optional[int] = Field(
        None,
        ge=8,
        le=148,
        description="Requested total duration. Used for metadata only; beats drive the chain.",
    )

    @field_validator("image_urls")
    @classmethod
    def normalize_image_urls(cls, urls: list[str]) -> list[str]:
        return [_normalize_image_url(url) for url in urls]

    @field_validator("beats")
    @classmethod
    def validate_and_sort_beats(cls, beats: list[VideoBeat]) -> list[VideoBeat]:
        for beat in beats:
            if not str(beat.prompt or "").strip():
                raise ValueError(f"Beat {beat.beat_number} is missing a prompt")
        return sorted(beats, key=lambda beat: beat.beat_number)
