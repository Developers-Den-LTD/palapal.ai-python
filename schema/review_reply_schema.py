from typing import Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ReplyTemplate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    tone: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1, alias="Prompt")


class ReviewComment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    uuid: Optional[str] = Field(None, validation_alias=AliasChoices("uuid", "UUID"))
    comment: Optional[str] = None
    rating: Optional[Union[int, float]] = Field(None, ge=1, le=5)
    author: Optional[str] = None
    date: Optional[str] = None


class ReviewReplyRequest(BaseModel):
    business_name: str = Field(..., min_length=1)
    business_id: Optional[Union[str, int]] = None
    date: Optional[str] = None
    template: Optional[ReplyTemplate] = None
    comments: list[ReviewComment] = Field(..., min_length=1)
