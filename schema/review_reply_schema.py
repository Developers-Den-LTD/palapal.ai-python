from typing import Optional, Union
import re

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_validator


def _normalize_string_list(value, *, split_arrows: bool = False):
    """
    Normalize list[str] fields.
    Accepts a list, a single string, or (optionally) an arrow-separated legacy string.
    """
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if split_arrows and ("→" in text or "->" in text):
            parts = re.split(r"\s*(?:→|->)\s*", text)
            value = parts
        else:
            value = [text]

    if not isinstance(value, list):
        raise ValueError("Value must be a list of strings or a string")

    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items or None


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
    response_structure: Optional[list[str]] = Field(
        None,
        description=(
            "Ordered reply parts as a list, e.g. "
            '["greeting", "thanks", "specific mention", "invite back", "sign-off"]'
        ),
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
        return _normalize_string_list(value, split_arrows=False)

    @field_validator("response_structure", mode="before")
    @classmethod
    def _normalize_response_structure(cls, value):
        # Accept legacy "a → b → c" strings and convert to a list.
        return _normalize_string_list(value, split_arrows=True)


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
    webhook_url: HttpUrl = Field(
        ...,
        description=(
            "URL that receives a small completion indicator via POST "
            "after replies are generated and scraped_result.json is updated"
        ),
    )


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


SUGGESTED_TONES = ("Casual", "Professional", "Friendly", "Apology")


class SuggestedTemplate(BaseModel):
    """Template preferences inferred from a user's edited reply."""

    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    tone: str = Field(
        ...,
        description="One of: Casual, Professional, Friendly, Apology",
    )
    writing_style: Optional[str] = None
    response_length: Optional[str] = None
    preferred_wording: Optional[list[str]] = None
    sign_off: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("sign_off", "sign_offs"),
    )
    response_structure: Optional[list[str]] = None
    prompt: str = Field(..., min_length=1, alias="Prompt")

    @field_validator("tone")
    @classmethod
    def _normalize_tone(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("tone cannot be empty")
        for allowed in SUGGESTED_TONES:
            if cleaned.lower() == allowed.lower():
                return allowed
        # Soft map common variants
        lowered = cleaned.lower()
        if "apolog" in lowered:
            return "Apology"
        if "casual" in lowered or "causal" in lowered:
            return "Casual"
        if "friend" in lowered or "warm" in lowered:
            return "Friendly"
        if "profession" in lowered or "formal" in lowered:
            return "Professional"
        raise ValueError(
            "tone must be one of: Casual, Professional, Friendly, Apology"
        )

    @field_validator("prompt")
    @classmethod
    def _require_prompt(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("Prompt cannot be empty")
        return cleaned

    @field_validator(
        "title",
        "writing_style",
        "response_length",
        "sign_off",
    )
    @classmethod
    def _strip_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("preferred_wording", mode="before")
    @classmethod
    def _normalize_wording(cls, value):
        return _normalize_string_list(value, split_arrows=False)

    @field_validator("response_structure", mode="before")
    @classmethod
    def _normalize_structure(cls, value):
        return _normalize_string_list(value, split_arrows=True)


class CurrentTemplateInput(BaseModel):
    """Optional existing template sent for context when suggesting updates."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    title: Optional[str] = None
    tone: Optional[str] = None
    writing_style: Optional[str] = None
    response_length: Optional[str] = None
    preferred_wording: Optional[list[str]] = None
    sign_off: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("sign_off", "sign_offs", "Sign-off", "Sign-offs"),
    )
    response_structure: Optional[list[str]] = None
    prompt: Optional[str] = Field(None, alias="Prompt")

    @field_validator("preferred_wording", mode="before")
    @classmethod
    def _normalize_wording(cls, value):
        try:
            return _normalize_string_list(value, split_arrows=False)
        except ValueError:
            return None

    @field_validator("response_structure", mode="before")
    @classmethod
    def _normalize_structure(cls, value):
        try:
            return _normalize_string_list(value, split_arrows=True)
        except ValueError:
            return None


class SuggestTemplateRequest(BaseModel):
    business_name: str = Field(..., min_length=1)
    business_id: Optional[Union[str, int]] = None
    current_template: Optional[CurrentTemplateInput] = None
    original_AI_Draft: str = Field(
        ...,
        validation_alias=AliasChoices(
            "original_AI_Draft",
            "original_ai_draft",
            "AI_Draft",
        ),
        description="Original AI-generated reply before user edits",
    )
    edit_draft: str = Field(
        ...,
        validation_alias=AliasChoices("edit_draft", "edited_draft", "editDraft"),
        description="User-edited reply to analyze for template preferences",
    )
    comment: Optional[str] = None
    rating: Optional[Union[int, float]] = Field(None, ge=0, le=5)
    author: Optional[str] = None
    uuid: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("uuid", "UUID"),
    )

    @field_validator("business_name")
    @classmethod
    def _strip_business_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("business_name cannot be empty")
        return cleaned

    @field_validator("original_AI_Draft", "edit_draft", mode="before")
    @classmethod
    def _coerce_drafts(cls, value) -> str:
        if value is None:
            return ""
        return str(value)
