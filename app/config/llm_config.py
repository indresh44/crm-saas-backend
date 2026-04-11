from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    # primary_model: str = Field(default="gemini/gemini-3-flash-preview", alias="LLM_PRIMARY_MODEL")
    primary_model: str = Field(default="openai/gpt-5.4-nano", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="ollama", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="gemini/gemini-2.5-flash", alias="LLM_PRIMARY_MODEL")
    summarization_model: str = Field(
        default="gemini/gemini-3-flash-preview",
        alias="LLM_SUMMARIZATION_MODEL",
    )
    max_tokens: int = Field(default=1500, alias="LLM_MAX_TOKENS")
    temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    timeout: int = Field(default=20, alias="LLM_TIMEOUT")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


llm_settings = LLMSettings()
