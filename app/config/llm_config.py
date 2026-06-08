from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    primary_model: str = Field(default="gemini/gemini-3-flash-preview", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="gemini/gemini-3.5-flash", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="openai/gpt-5.4-nano", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="ollama", alias="LLM_PRIMARY_MODEL")
    # primary_model: str = Field(default="gemini/gemini-2.5-flash", alias="LLM_PRIMARY_MODEL")
    summarization_model: str = Field(
        default="gemini/gemini-3-flash-preview",
        alias="LLM_SUMMARIZATION_MODEL",
    )
    # Per-enquiry computed intelligence (requirement_summary, demand_tags,
    # activity_summary). Verify exact LiteLLM model string vs Google docs —
    # the listed default reflects the current cheapest Gemini flash-lite SKU.
    summary_model: str = Field(
        default="gemini/gemini-3.1-flash-lite",
        alias="LLM_SUMMARY_MODEL",
    )
    summary_max_output_tokens: int = Field(
        default=200,
        alias="SUMMARY_MAX_OUTPUT_TOKENS",
    )
    # Was 1500 — that capped agent answers mid-word on long listings
    # ("...Customer: Amit Patel * **Tim"). 8192 is a comfortable ceiling for
    # any reasonable single response and still bounds runaway generation.
    # Override per-env via LLM_MAX_TOKENS if needed.
    max_tokens: int = Field(default=8192, alias="LLM_MAX_TOKENS")
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
