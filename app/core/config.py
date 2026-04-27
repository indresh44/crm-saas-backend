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

    # ZeptoMail REST API — SMTP is blocked outbound on most cloud hosts
    # (Linode included), so we use HTTPS to api.zeptomail.in.
    # Leave ZEPTOMAIL_TOKEN empty to fall back to the stdout stub
    # (useful in dev / CI / tests).
    ZEPTOMAIL_API_URL: str = "https://api.zeptomail.in/v1.1/email"
    ZEPTOMAIL_TOKEN: str = ""
    EMAIL_FROM_ADDRESS: str = "noreply@sellnsettle.com"
    EMAIL_FROM_NAME: str = "SellNSettle"

    # Admin panel (local-only). Both flags must be set for admin endpoints
    # to be registered. In production neither is set, so /api/admin/* returns
    # 404 even with a valid JWT — the routes literally don't exist.
    # See Docs/plans/admin-panel.md for the full architecture.
    ENABLE_ADMIN_ROUTES: bool = False
    SUPER_ADMIN_EMAILS: str = ""  # comma-separated list

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
