from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://crm_user:crm_password@localhost:5432/crm_db"
    # TODO: Unify with stored account webhook_verify_token when resolving account at verification
    whatsapp_webhook_verify_token: str = "crm-whatsapp-webhook-verify-token"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
