from pydantic import BaseModel, Field


class RedditDiscussionRequest(BaseModel):
    business_name: str = Field(
        ...,
        min_length=1,
        description="Exact business name to search for on Reddit",
    )
    business_loc: str = Field(
        ...,
        min_length=1,
        description="City or region used to scope Reddit discussion search",
    )
