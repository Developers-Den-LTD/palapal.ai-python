from typing import Optional, Union

from pydantic import BaseModel, Field


class VideoPromptRequest(BaseModel):
    business_name: str = Field(..., min_length=1)
    business_id: Optional[Union[str, int]] = None
    cta_text: str = Field(..., min_length=1)
    cta_url: str = Field(..., min_length=1)
    target_seconds: int = Field(
        ...,
        ge=8,
        le=148,
        description="Desired total video duration. Beats are generated from this value.",
    )


class VideoBeat(BaseModel):
    beat_number: int
    purpose: str
    prompt: str


class VideoPromptResponse(BaseModel):
    status: str
    business_name: str
    business_id: Optional[Union[str, int]]
    model: str
    target_seconds: int
    intro_seconds: int
    chaining_seconds: int
    total_segments: int
    beat_count: int
    positive_reviews_used: int
    positive_review_snippets: list[str]
    master_prompt: str
    beats: list[VideoBeat]
