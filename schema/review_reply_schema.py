from typing import Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class ReplyTemplate(BaseModel):
    """
    Style guide for AI review replies.

    Required: tone, Prompt.
    Optional preference fields improve consistency when provided.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = Field(
        None,
        description="Optional label for this template",
    )
    tone: str = Field(
        ...,
        min_length=1,
        description="Overall voice (e.g. warm and professional)",
    )
    writing_style: Optional[str] = Field(
        None,
        description="How replies should read (e.g. conversational, first-person)",
    )
    response_length: Optional[str] = Field(
        None,
        description="Target length (e.g. 2-4 sentences, short, concise)",
    )
    preferred_wording: Optional[list[str]] = Field(
        None,
        description="Phrases to prefer when natural",
    )
    sign_off: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("sign_off", "sign_offs", "Sign-off", "Sign-offs"),
        description="Closing line / sign-off to use when appropriate",
    )
    response_structure: Optional[str] = Field(
        None,
        description="Preferred order of parts (e.g. greeting → thanks → invite back → sign-off)",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        alias="Prompt",
        description="Freeform messaging instructions for every reply",
    )

    @field_validator("tone", "prompt")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator(
        "title",
        "writing_style",
        "response_length",
        "sign_off",
        "response_structure",
    )
    @classmethod
    def _strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("preferred_wording", mode="before")
    @classmethod
    def _normalize_preferred_wording(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else None
        if not isinstance(value, list):
            raise ValueError("preferred_wording must be a list of strings or a string")
        phrases: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            phrases.append(text)
        return phrases or None


class ReviewComment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    uuid: Optional[str] = Field(None, validation_alias=AliasChoices("uuid", "UUID"))
    comment: Optional[str] = None
    rating: Optional[Union[int, float]] = Field(
        None,
        ge=0,
        le=5,
        description="Star rating from 0 to 5. 0 means no rating was provided.",
    )
    author: Optional[str] = None
    date: Optional[str] = None


class ReviewReplyRequest(BaseModel):
    business_name: str = Field(..., min_length=1)
    business_id: Optional[Union[str, int]] = None
    date: Optional[str] = None
    template: Optional[ReplyTemplate] = None
    comments: list[ReviewComment] = Field(..., min_length=1)


class EditDraftComment(BaseModel):
    """Payload item for saving an edited draft onto a review's AI_Draft field."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    uuid: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("uuid", "UUID"),
        description="Review uuid from scraped_result.json",
    )
    edit_draft: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("edit_draft", "editDraft", "Edit_Draft"),
        description=(
            "Edited or custom draft reply. When this API is hit, this value "
            "replaces the stored AI_Draft on the matching review."
        ),
    )
    AI_Draft: Optional[str] = Field(
        None,
        description=(
            "Optional original AI draft (for client context). "
            "Not used for saving when edit_draft is provided. "
            "Kept as a fallback save value for older clients."
        ),
    )
    # Accepted for convenience from the UI payload; ignored when saving.
    comment: Optional[str] = None
    rating: Optional[Union[int, float]] = Field(None, ge=0, le=5)
    author: Optional[str] = None
    date: Optional[str] = None

    @field_validator("uuid")
    @classmethod
    def _strip_uuid(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("uuid cannot be empty")
        return cleaned

    @field_validator("edit_draft", "AI_Draft", mode="before")
    @classmethod
    def _coerce_draft_text(cls, value):
        if value is None:
            return None
        return str(value)

    def resolved_edit_draft(self) -> str:
        """
        Value that will be written to stored AI_Draft.
        Prefer edit_draft; fall back to AI_Draft for older payloads.
        """
        if self.edit_draft is not None:
            return self.edit_draft
        if self.AI_Draft is not None:
            return self.AI_Draft
        raise ValueError(
            "edit_draft is required (or provide AI_Draft for legacy clients)"
        )


class EditDraftRequest(BaseModel):
    business_name: str = Field(..., min_length=1)
    business_id: Union[str, int] = Field(
        ...,
        description="Required business id used to locate scraped_result.json",
    )
    comments: list[EditDraftComment] = Field(..., min_length=1)

    @field_validator("business_name")
    @classmethod
    def _strip_business_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("business_name cannot be empty")
        return cleaned

    @field_validator("business_id")
    @classmethod
    def _validate_business_id(cls, value: Union[str, int]) -> Union[str, int]:
        if value is None:
            raise ValueError("business_id is required")
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("business_id is required")
            return cleaned
        return value
