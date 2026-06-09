from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Setting min_length=1 ensures the keys cannot be empty strings
    Apify_API: str = Field(min_length=1)
    Pagespeed_API: str = Field(min_length=1)
    Perplexity_API: str = Field(min_length=1)

    API_SECRET_KEY: str = Field(min_length=1)

    # Simple configuration to read the .env file
    model_config = SettingsConfigDict(
        env_file=".env", 
        extra="ignore", 
        env_ignore_case=True
    )

# Instantiate settings
settings = Settings()