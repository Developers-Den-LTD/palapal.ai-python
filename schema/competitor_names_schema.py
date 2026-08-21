from typing import Union

from pydantic import BaseModel, Field


class CompetitorNamesRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Business name used when calculating DDI score (matches DDI_score folder and S3 key)",
        min_length=1,
    )
    business_id: Union[str, int] = Field(
        ...,
        description=(
            "Client-side business identifier. DDI results are resolved using "
            "businessname_businessid."
        ),
    )
