from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl, field_validator


class CitationAnalysisRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business to check for citations",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Client-side business identifier",
    )
    business_type: str = Field(
        ...,
        description="Type of business (e.g. Hotel)",
        min_length=1,
    )
    business_loc: str = Field(
        ...,
        description="City or region (e.g. Manchester)",
        min_length=1,
    )
    custom_questions: list[str] = Field(
        ...,
        description="Custom search queries used for citation analysis (auto questions are not used)",
        min_length=1,
        max_length=20,
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the citation analysis result via POST when processing completes",
    )

    @field_validator("custom_questions")
    @classmethod
    def _clean_custom_questions(cls, value: list[str]) -> list[str]:
        cleaned = [question.strip() for question in value if question and question.strip()]
        if not cleaned:
            raise ValueError("custom_questions must contain at least one non-empty question")
        if len(cleaned) > 20:
            raise ValueError("A maximum of 20 custom questions is allowed")
        return cleaned
