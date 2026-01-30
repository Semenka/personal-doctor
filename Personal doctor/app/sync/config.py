from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SyncConfig:
    data_dir: Path
    oura_access_token: str | None
    timezone: ZoneInfo
    database_url: str | None
    openalex_mailto: str | None
    oura_base_url: str = "https://api.ouraring.com/v2/usercollection"


def load_config() -> SyncConfig:
    data_dir = Path(os.getenv("HEALTH_DATA_DIR", "data/ingested"))
    token = os.getenv("OURA_ACCESS_TOKEN")
    tz_name = os.getenv("HEALTH_TIMEZONE", "Europe/Paris")
    database_url = os.getenv("DATABASE_URL")
    openalex_mailto = os.getenv("OPENALEX_MAILTO")
    return SyncConfig(
        data_dir=data_dir,
        oura_access_token=token,
        timezone=ZoneInfo(tz_name),
        database_url=database_url,
        openalex_mailto=openalex_mailto,
    )
