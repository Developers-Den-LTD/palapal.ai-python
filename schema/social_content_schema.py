from typing import Literal, get_args

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from schema.review_reply_schema import _normalize_writing_style

ContentType = Literal[
    "general",
    "promotional",
    "offer",
    "announcement",
    "educational",
    "seasonal",
    "event",
    "engagement",
]
Platform = Literal[
    "facebook",
    "instagram",
    "tiktok",
    "x",
    "youtube",
    "pinterest",
    "linkedin",
    "google_business",
]
AspectRatio = Literal["1:1", "4:5", "9:16", "16:9"]
ReferenceUse = Literal["character", "background", "colours", "style", "composition"]
OverlayArea = Literal[
    "top",
    "bottom",
    "left",
    "right",
    "center",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
]
Provider = Literal["gemini", "openai"]
Quality = Literal["standard", "premium"]
PostLength = Literal["short", "medium", "long"]
EmojiUsage = Literal["none", "minimal", "moderate", "liberal"]

CONTENT_TYPES = get_args(ContentType)
PLATFORMS = get_args(Platform)
ASPECT_RATIOS = get_args(AspectRatio)
REFERENCE_USES = get_args(ReferenceUse)
OVERLAY_AREAS = get_args(OverlayArea)
PROVIDERS = get_args(Provider)


def _clean_text_list(
    values: list[str],
    *,
    max_item_length: int,
    field_name: str,
    strip_hash: bool = False,
) -> list[str]:
    """Strip, drop empties and de-duplicate case-insensitively, keeping order."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if strip_hash:
            text = text.lstrip("#").strip()
        if not text:
            continue
        if len(text) > max_item_length:
            raise ValueError(
                f"Each {field_name} item must be at most {max_item_length} characters"
            )
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _lowercase_list(value):
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value]
    return value


def _dedupe(values: list) -> list:
    seen = set()
    return [item for item in values if not (item in seen or seen.add(item))]


class BusinessContext(BaseModel):
    business_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Name of the business",
    )
    business_id: str | int = Field(
        ...,
        description=(
            "Client-side business identifier. Results are resolved using "
            "businessname_businessid; required here so businesses with the same "
            "name never share stored results"
        ),
    )
    industry: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Industry or business category (e.g. restaurant, dental clinic)",
    )
    services: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Services the business offers",
    )
    location: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="City or region (e.g. Manchester)",
    )
    description: str | None = Field(
        None,
        max_length=1000,
        description="Optional short description of what the business is about",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Approved keywords from AI Keyword Generation. When empty, keywords "
            "are loaded from the stored Keyword SEO result if one exists"
        ),
    )
    brand_hashtags: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="The brand's recurring hashtags, with or without '#'",
    )
    recent_captions: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 of the brand's past captions, used as voice examples",
    )

    @field_validator("business_name", "industry", "location")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("business_id")
    @classmethod
    def _require_business_id(cls, value: str | int) -> str | int:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("business_id cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("services")
    @classmethod
    def _clean_services(cls, value: list[str]) -> list[str]:
        cleaned = _clean_text_list(value, max_item_length=200, field_name="services")
        if not cleaned:
            raise ValueError("services must contain at least one non-empty service")
        return cleaned

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        return _clean_text_list(value, max_item_length=100, field_name="keywords")

    @field_validator("brand_hashtags")
    @classmethod
    def _clean_brand_hashtags(cls, value: list[str]) -> list[str]:
        return _clean_text_list(
            value, max_item_length=100, field_name="brand_hashtags", strip_hash=True
        )

    @field_validator("recent_captions")
    @classmethod
    def _clean_recent_captions(cls, value: list[str]) -> list[str]:
        return _clean_text_list(
            value, max_item_length=2200, field_name="recent_captions"
        )


class HashtagPreferences(BaseModel):
    max_count: int | None = Field(
        None,
        ge=0,
        le=30,
        description="Most hashtags per caption; the platform's own cap still applies",
    )
    always_include: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Hashtags every caption must carry, with or without '#'",
    )
    never_include: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Hashtags no caption may carry, with or without '#'",
    )

    @field_validator("always_include", "never_include")
    @classmethod
    def _clean_hashtags(cls, value: list[str]) -> list[str]:
        return _clean_text_list(
            value, max_item_length=100, field_name="hashtag", strip_hash=True
        )


class CtaPreferences(BaseModel):
    preferred: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Preferred call-to-action phrases (e.g. Book now)",
    )
    required: bool = Field(False, description="Every caption must end with a CTA")

    @field_validator("preferred")
    @classmethod
    def _clean_preferred(cls, value: list[str]) -> list[str]:
        return _clean_text_list(value, max_item_length=100, field_name="cta")


class SocialContentGuidelines(BaseModel):
    """
    Brand guidelines for AI social content (#14).

    Collected in the frontend, stored by the backend and sent on every call.
    Every field is optional; missing fields fall back to defaults.
    """

    guideline_id: str | None = Field(
        None,
        description="Backend identifier once saved; null in a fresh AI suggestion",
    )
    brand_tone: str | None = Field(
        None,
        max_length=300,
        description="Overall voice (e.g. warm and upbeat)",
    )
    writing_style: str | None = Field(
        None,
        description="How captions should read, 1 to 3 words max (e.g. conversational)",
    )
    target_audience: str | None = Field(
        None,
        max_length=300,
        description="Who the posts speak to (e.g. young families in Leeds)",
    )
    post_length: PostLength | None = Field(None, description="Preferred caption length")
    emoji_usage: EmojiUsage | None = Field(
        None,
        description="none / minimal / moderate / liberal (max 0 / 1 / 3 / no cap per caption)",
    )
    hashtags: HashtagPreferences | None = Field(None, description="Hashtag preferences")
    cta: CtaPreferences | None = Field(None, description="Call-to-action preferences")
    restricted_words: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Words that must never appear in captions or hashtags",
    )
    required_terms: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Terms every caption must include",
    )

    @field_validator("guideline_id", mode="before")
    @classmethod
    def _coerce_guideline_id(cls, value):
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("brand_tone", "target_audience")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("writing_style")
    @classmethod
    def _limit_writing_style(cls, value: str | None) -> str | None:
        return _normalize_writing_style(value)

    @field_validator("restricted_words")
    @classmethod
    def _clean_restricted_words(cls, value: list[str]) -> list[str]:
        return _clean_text_list(value, max_item_length=40, field_name="restricted_words")

    @field_validator("required_terms")
    @classmethod
    def _clean_required_terms(cls, value: list[str]) -> list[str]:
        return _clean_text_list(value, max_item_length=100, field_name="required_terms")


class Idea(BaseModel):
    """A post idea returned by /ideas and accepted back by /generate."""

    idea_id: str = Field(..., min_length=1, max_length=64, description="Idea identifier")
    title: str = Field(..., min_length=1, max_length=120, description="Short idea title")
    content_type: ContentType = Field(..., description="One of the 8 content types in #13")
    brief: str = Field(..., min_length=1, max_length=500, description="What the post is about")
    image_concept: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="What the image should show",
    )
    why_it_helps: str | None = Field(
        None,
        max_length=300,
        description="Display only; never sent to the image model",
    )
    suggested_platforms: list[Platform] = Field(
        default_factory=list,
        max_length=8,
        description="Platforms this idea suits best",
    )

    @field_validator("title", "brief", "image_concept")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned

    @field_validator("suggested_platforms", mode="before")
    @classmethod
    def _lowercase_platforms(cls, value):
        return _lowercase_list(value)

    @field_validator("suggested_platforms")
    @classmethod
    def _dedupe_platforms(cls, value: list[str]) -> list[str]:
        return _dedupe(value)


class GuidelineSuggestRequest(BaseModel):
    business: BusinessContext = Field(..., description="Business profile")
    current_guidelines: SocialContentGuidelines | None = Field(
        None,
        description="When present, the AI suggests improvements instead of a rewrite",
    )


class IdeasRequest(BaseModel):
    business: BusinessContext = Field(..., description="Business profile")
    guidelines: SocialContentGuidelines | None = Field(
        None,
        description="Saved brand guidelines (#14); defaults apply when missing",
    )
    content_type: ContentType | None = Field(
        None,
        description="When set, all 5 ideas use this type; otherwise they spread across types",
    )
    target_platforms: list[Platform] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Platforms the user posts to",
    )
    facts: str | None = Field(
        None,
        max_length=500,
        description="Offer or event facts (e.g. 20% off lattes this Saturday)",
    )

    @field_validator("target_platforms", mode="before")
    @classmethod
    def _lowercase_platforms(cls, value):
        return _lowercase_list(value)

    @field_validator("target_platforms")
    @classmethod
    def _dedupe_platforms(cls, value: list[str]) -> list[str]:
        return _dedupe(value)

    @field_validator("facts")
    @classmethod
    def _strip_facts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class GenerateRequest(BaseModel):
    business: BusinessContext = Field(..., description="Business profile")
    guidelines: SocialContentGuidelines | None = Field(
        None,
        description="Saved brand guidelines (#14); defaults apply when missing",
    )
    idea: Idea | None = Field(None, description="An idea returned by /ideas")
    brief: str | None = Field(
        None,
        max_length=500,
        description="One-line brief, used instead of an idea",
    )
    content_type: ContentType | None = Field(
        None,
        description="Required with a brief; ignored when an idea is sent",
    )
    target_platforms: list[Platform] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Platforms to write captions for",
    )
    aspect_ratio: AspectRatio = Field("1:1", description="Image shape")
    facts: str | None = Field(
        None,
        max_length=500,
        description="The only prices, dates and numbers the captions may use",
    )
    provider: Provider = Field("gemini", description="Image model provider; text always runs on OpenAI")
    quality: Quality = Field(
        "standard",
        description="premium switches to the provider's best image model",
    )
    reference_image_url: HttpUrl | None = Field(
        None,
        description="Optional https link to a JPEG, PNG or WebP reference photo (max 10 MB)",
    )
    reference_use: list[ReferenceUse] = Field(
        default_factory=list,
        max_length=5,
        description="What to take from the reference photo; required with a reference",
    )
    reference_note: str | None = Field(
        None,
        max_length=300,
        description="Optional note about the reference (e.g. the dog, in our cafe)",
    )
    overlay_areas: list[OverlayArea] = Field(
        default_factory=list,
        max_length=9,
        description=(
            "Optional. Areas the frontend will cover with logo or text; "
            "the image keeps them visually plain. Empty means no area is kept clear"
        ),
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the generation result via POST when processing completes",
    )

    @field_validator("target_platforms", "reference_use", "overlay_areas", mode="before")
    @classmethod
    def _lowercase_lists(cls, value):
        return _lowercase_list(value)

    @field_validator("target_platforms", "reference_use", "overlay_areas")
    @classmethod
    def _dedupe_lists(cls, value: list[str]) -> list[str]:
        return _dedupe(value)

    @field_validator("brief", "facts", "reference_note")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _check_cross_fields(self) -> "GenerateRequest":
        if (self.idea is None) == (self.brief is None):
            raise ValueError("Send exactly one of idea or brief")
        if self.brief is not None and self.content_type is None:
            raise ValueError("content_type is required when a brief is sent")
        if self.reference_image_url is not None:
            if self.reference_image_url.scheme != "https":
                raise ValueError("reference_image_url must use https")
            if not self.reference_use:
                raise ValueError(
                    "reference_use must list what to take from the reference photo"
                )
        elif self.reference_use:
            raise ValueError("reference_use is only allowed with reference_image_url")
        return self
