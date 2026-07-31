from typing import Optional, Union

from pydantic import BaseModel, Field


class ActionCardsRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Business name used when calculating DDI score (matches DDI_score folder and S3 key)",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description=(
            "Client-side business identifier. When provided, DDI results are "
            "resolved using businessname_businessid."
        ),
    )