from fastapi import Header, HTTPException, status
from core.config import settings

def verify_secret_key(x_api_key: str = Header(None)):
    """
    Verifies API secret key sent in request headers.
    Header name: X-API-KEY
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key name"
        )

    if x_api_key != settings.API_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )

    return True
