from pydantic import BaseModel, Field, field_validator


class KeywordSeoComparisonRequest(BaseModel):
    business_id: str = Field(..., description="UUID of the primary business")
    competitor_ids: list[str] = Field(
        ...,
        description="List of competitor business UUIDs to compare against",
        min_length=1,
    )

    @field_validator("business_id")
    @classmethod
    def _strip_business_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("business_id cannot be empty")
        return cleaned

    @field_validator("competitor_ids")
    @classmethod
    def _normalize_competitor_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            cid = str(item).strip()
            if not cid:
                raise ValueError("competitor_ids cannot contain empty values")
            if cid not in seen:
                seen.add(cid)
                cleaned.append(cid)
        if not cleaned:
            raise ValueError("At least one competitor_id is required")
        return cleaned
