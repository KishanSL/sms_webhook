"""
Configuration settings for the Homelab HTTPS SMS Webhook Service.
Loads configuration from environment variables or .env file.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Server network settings
    HOST: str = "0.0.0.0"
    PORT: int = 8443
    HTTPS_ENABLED: bool = True

    # Self-Signed TLS / SSL Certificate settings
    CERT_DIR: str = str(BASE_DIR / "certs")
    SSL_CERTFILE: str = str(BASE_DIR / "certs" / "cert.pem")
    SSL_KEYFILE: str = str(BASE_DIR / "certs" / "key.pem")
    AUTO_GENERATE_CERT: bool = True
    CERT_VALID_DAYS: int = 825  # standard max validity for modern TLS

    # Database settings
    DATA_DIR: str = str(BASE_DIR / "data")
    DB_PATH: str = str(BASE_DIR / "data" / "webhooks.db")

    # Security: Optional API secret or Webhook Token.
    # If set, incoming webhooks must include header 'X-Webhook-Secret', 'X-Api-Key',
    # or query param '?secret=...'. Leave empty/None to allow all incoming webhooks without auth.
    WEBHOOK_SECRET: str | None = None

    # Max body size in bytes (default: 10 MB)
    MAX_BODY_SIZE_BYTES: int = 10 * 1024 * 1024

    # GnuCash-web integration.
    # The integration is off (no Post buttons, no account dropdowns) unless both
    # GNUCASH_URL and GNUCASH_API_TOKEN are set.
    GNUCASH_URL: str | None = None       # e.g. http://192.168.1.10:8097 (frontend proxies /api)
    GNUCASH_API_TOKEN: str | None = None  # gcw_… token created in gnucash-web
    # Label shown in the To column when nothing suggests an account. It is a UI placeholder
    # only: sms_webhook never creates an account in gnucash-web, and Post stays disabled.
    GNUCASH_UNCATEGORISED_LABEL: str = "Uncategorised"

    @property
    def gnucash_enabled(self) -> bool:
        return bool(self.GNUCASH_URL and self.GNUCASH_API_TOKEN)

    # App info
    APP_NAME: str = "Homelab HTTPS SMS Webhook Service"
    APP_VERSION: str = "1.6.2"

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
