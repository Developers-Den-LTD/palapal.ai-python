from typing import List

from pydantic import BaseModel, Field, HttpUrl


class PageAuditRequest(BaseModel):
    urls: List[HttpUrl] = Field(
        ...,
        min_length=1,
        description="Full page URLs to audit",
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the audit result via POST when processing completes",
    )
