from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://crm_user:crm_password@localhost:5432/crm_db"
    # TODO: Unify with stored account webhook_verify_token when resolving account at verification
    whatsapp_webhook_verify_token: str = "crm-whatsapp-webhook-verify-token"
    R2_ENDPOINT_URL: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""
    R2_PUBLIC_URL: str = ""
    JWT_SECRET_KEY: str = "CHANGE-THIS-TO-A-REAL-SECRET-KEY-IN-PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    FORCE_FORM_ONBOARDING: bool = False
    FRONTEND_BASE_URL: str = "http://localhost:3000"
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30
    PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS: int = 60

    # ZeptoMail SMTP — leave ZEPTOMAIL_SMTP_PASSWORD empty to fall back to
    # the stdout stub (useful in dev / CI / tests).
    ZEPTOMAIL_SMTP_HOST: str = "smtp.zeptomail.in"
    ZEPTOMAIL_SMTP_PORT: int = 587
    ZEPTOMAIL_SMTP_USERNAME: str = "emailapikey"
    ZEPTOMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM_ADDRESS: str = "noreply@sellnsettle.com"
    EMAIL_FROM_NAME: str = "SellNSettle"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
