"""Model IDs, prices, caps and platform specs for AI social content."""

IMAGE_MODELS = {
    "gemini": {"standard": "gemini-3.1-flash-image", "premium": "gemini-3-pro-image"},
    "openai": {"standard": "gpt-image-2.5-flare", "premium": "gpt-image-2.5-sunburst"},
}
# Text (guidelines, ideas, captions) always runs on OpenAI; the provider toggle only picks the image model.
TEXT_MODEL = "gpt-5.4-mini"
TEXT_MODEL_FALLBACKS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]

GEMINI_IMAGE_SIZES = {"standard": "1K", "premium": "2K"}  # 2K costs the same as 1K on Nano Banana Pro
OPENAI_IMAGE_QUALITY = {"standard": "medium", "premium": "high"}
# OpenAI custom sizes: edges multiples of 16, ratio 1:3 to 3:1, 655,360 to 8,294,400 pixels.
OPENAI_IMAGE_SIZES = {
    "1:1": "1024x1024",
    "4:5": "1024x1280",
    "9:16": "1008x1792",
    "16:9": "1792x1008",
}

MAX_IMAGE_ATTEMPTS = 2  # on the chosen model; then 1 attempt on the other provider
IMAGE_TIMEOUT_SECONDS = 180  # OpenAI notes complex prompts can take up to 2 minutes
TEXT_TIMEOUT_SECONDS = 90
MAX_REFERENCE_BYTES = 10 * 1024 * 1024  # under OpenAI's 50 MB and Gemini's 20 MB inline limit
MAX_REFERENCE_REDIRECTS = 3
REFERENCE_TIMEOUT_SECONDS = 15
PRESIGNED_URL_SECONDS = 7 * 24 * 3600  # SigV4 maximum
IDEAS_COUNT = 5
MAX_ENRICHED_KEYWORDS = 10
MAX_REVIEW_HIGHLIGHTS = 3
MAX_HIGHLIGHT_CHARS = 300

REVIEW_PLATFORMS = ("google_maps", "yelp", "tripadvisor", "facebook", "trustpilot", "feefo")
KEYWORD_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

EMOJI_CAPS = {"none": 0, "minimal": 1, "moderate": 3, "liberal": None}
POST_LENGTH_HINTS = {
    "short": "1-2 sentences",
    "medium": "3-4 sentences",
    "long": "a short paragraph of 5-7 sentences",
}
DEFAULT_GUIDELINES = {
    "brand_tone": "friendly and welcoming",
    "post_length": "medium",
    "emoji_usage": "minimal",
}

# Image posts. max_hashtags is a hard platform cap for Instagram (5, enforced since
# December 2025) and YouTube (more than 15 and all are ignored); elsewhere it is the
# common 3-5 guidance. Google Business posts do not use hashtags. Pinterest standard
# pins allow 500 description characters (800 is the ads limit). TikTok photo posts
# have a 90-rune title and a 4,000-rune description.
PLATFORM_SPECS = {
    "facebook": {"max_chars": 63206, "title_max_chars": None, "max_hashtags": 5},
    "instagram": {"max_chars": 2200, "title_max_chars": None, "max_hashtags": 5},
    "tiktok": {"max_chars": 4000, "title_max_chars": 90, "max_hashtags": 5},
    "x": {"max_chars": 280, "title_max_chars": None, "max_hashtags": 2},
    "youtube": {"max_chars": 5000, "title_max_chars": 100, "max_hashtags": 15},
    "pinterest": {"max_chars": 500, "title_max_chars": 100, "max_hashtags": 5},
    "linkedin": {"max_chars": 3000, "title_max_chars": None, "max_hashtags": 5},
    "google_business": {"max_chars": 1500, "title_max_chars": None, "max_hashtags": 0},
}
X_TARGET_CHARS = 260  # headroom under 280 for URLs and emoji weighting
X_URL_WEIGHT = 23

CONTENT_TYPE_RECIPES = {
    "general": "Everyday post about the business: a hook, one clear point, a soft call to action.",
    "promotional": "Promote a service or product: the benefit first, what makes it special, a call to action.",
    "offer": "A time-limited offer: the offer exactly as given in the facts, when it ends, how to claim it.",
    "announcement": "News from the business: what is new, why it matters to customers, what to do next.",
    "educational": "A useful tip related to the business's services: the tip, why it works, an invitation to learn more.",
    "seasonal": "Tie the business to the current season or holiday: the seasonal hook, the link to the business, a call to action.",
    "event": "Promote an event: what, when and where exactly as given in the facts, why attend, how to join.",
    "engagement": "Invite interaction: a question or poll-style prompt customers can answer in the comments.",
}

REFERENCE_USE_INSTRUCTIONS = {
    "character": "Use the main character or subject from the reference image.",
    "background": "Use a background similar to the reference image.",
    "colours": "Match the colour palette of the reference image.",
    "style": "Match the visual style of the reference image.",
    "composition": "Follow the composition and framing of the reference image.",
}
OVERLAY_AREA_NAMES = {
    "top": "top",
    "bottom": "bottom",
    "left": "left side",
    "right": "right side",
    "center": "centre",
    "top_left": "top-left corner",
    "top_right": "top-right corner",
    "bottom_left": "bottom-left corner",
    "bottom_right": "bottom-right corner",
}
NO_TEXT_INSTRUCTION = "No text, letters, numbers, logos or watermarks in the image."
