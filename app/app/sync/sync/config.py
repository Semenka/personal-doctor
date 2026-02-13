from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class SyncConfig:
    data_dir: Path
    oura_access_token: str | None
    timezone: ZoneInfo
    database_url: str | None
    openalex_mailto: str | None
    gdrive_credentials_dir: str | None
    anthropic_api_key: str | None
    email_to: str | None
    smtp_host: str | None
    smtp_port: int | None
    smtp_user: str | None
    smtp_password: str | None
    oura_base_url: str = "https://api.ouraring.com/v2/usercollection"


def load_config() -> SyncConfig:
    data_dir = Path(os.getenv("HEALTH_DATA_DIR", "data/ingested"))
    token = os.getenv("OURA_ACCESS_TOKEN")
    tz_name = os.getenv("HEALTH_TIMEZONE", "Europe/Paris")
    database_url = os.getenv("DATABASE_URL")
    openalex_mailto = os.getenv("OPENALEX_MAILTO")
    gdrive_credentials_dir = os.getenv("GDRIVE_CREDENTIALS_DIR")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    email_to = os.getenv("EMAIL_TO")
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT", "465")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    return SyncConfig(
        data_dir=data_dir,
        oura_access_token=token,
        timezone=ZoneInfo(tz_name),
        database_url=database_url,
        openalex_mailto=openalex_mailto,
        gdrive_credentials_dir=gdrive_credentials_dir,
        anthropic_api_key=anthropic_api_key,
        email_to=email_to,
        smtp_host=smtp_host,
        smtp_port=int(smtp_port_str) if smtp_port_str else 587,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )
