import json
from pathlib import Path

from dataclasses import fields

from .models import DailyData


def load_daily_data(path: str | Path) -> DailyData:
    payload = json.loads(Path(path).read_text())
    return DailyData(**payload)


def load_daily_payload(payload: dict) -> DailyData:
    allowed = {field.name for field in fields(DailyData)}
    filtered = {key: value for key, value in payload.items() if key in allowed}
    return DailyData(**filtered)
