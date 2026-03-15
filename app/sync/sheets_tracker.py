"""Google Sheets-based action tracker — works from any device.

Creates a persistent "Health Action Tracker" spreadsheet in the me/health/
GDrive folder. Each day's actions are appended as rows with native checkboxes.
The user can tick them from any device (phone, tablet, laptop) via the
Google Sheets app or browser.

All API calls are wrapped in try/except so failures fall back to local JSON.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build

from .config import SyncConfig
from .connectors.gdrive import _get_credentials, _resolve_or_create_folder

logger = logging.getLogger("personal-doctor.sheets")

SHEET_TAB_NAME = "Actions"
TRACKER_SHEET_TITLE = "Health Action Tracker"
HEALTH_FOLDER_PATH = "me/health"

# Local cache file to avoid re-discovering the Sheet every call
_CACHE_FILENAME = ".tracker_sheet_id"


# ─── Helpers ────────────────────────────────────────────────────────────────

def _actions_cache_dir(config: SyncConfig) -> Path:
    d = config.data_dir / "actions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sheet_id_cache_path(config: SyncConfig) -> Path:
    return _actions_cache_dir(config) / _CACHE_FILENAME


def _build_sheets_service(config: SyncConfig):
    creds = _get_credentials(config)
    return build("sheets", "v4", credentials=creds)


def _build_drive_service(config: SyncConfig):
    creds = _get_credentials(config)
    return build("drive", "v3", credentials=creds)


# ─── Sheet creation / discovery ─────────────────────────────────────────────

def get_or_create_tracker_sheet(config: SyncConfig) -> str:
    """Return the spreadsheet ID, creating the Sheet + folder if needed.

    Caches the ID locally so subsequent calls within the same day are instant.
    """
    cache_path = _sheet_id_cache_path(config)

    # Try cached ID first
    if cache_path.exists():
        sheet_id = cache_path.read_text().strip()
        if sheet_id:
            try:
                sheets = _build_sheets_service(config)
                sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
                return sheet_id
            except Exception:
                logger.warning("Cached tracker sheet ID invalid, will re-discover.")

    # Search for existing sheet in health folder
    drive = _build_drive_service(config)
    folder_id = _resolve_or_create_folder(drive, HEALTH_FOLDER_PATH)

    query = (
        f"'{folder_id}' in parents "
        f"and name = '{TRACKER_SHEET_TITLE}' "
        f"and mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and trashed = false"
    )
    results = drive.files().list(q=query, fields="files(id)", pageSize=1).execute()
    existing = results.get("files", [])

    if existing:
        sheet_id = existing[0]["id"]
        cache_path.write_text(sheet_id)
        logger.info(f"Found existing tracker sheet: {sheet_id}")
        return sheet_id

    # Create a brand-new spreadsheet
    sheets = _build_sheets_service(config)
    body = {
        "properties": {"title": TRACKER_SHEET_TITLE},
        "sheets": [{
            "properties": {
                "title": SHEET_TAB_NAME,
                "gridProperties": {"frozenRowCount": 1},
            },
        }],
    }
    spreadsheet = sheets.spreadsheets().create(
        body=body, fields="spreadsheetId"
    ).execute()
    sheet_id = spreadsheet["spreadsheetId"]

    # Move spreadsheet from root into the health folder
    file_meta = drive.files().get(fileId=sheet_id, fields="parents").execute()
    previous_parents = ",".join(file_meta.get("parents", []))
    drive.files().update(
        fileId=sheet_id,
        addParents=folder_id,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()

    # Write header row
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_TAB_NAME}!A1:E1",
        valueInputOption="RAW",
        body={"values": [["Date", "#", "Action Title", "Done", "Done At"]]},
    ).execute()

    # Apply formatting (bold header, column widths)
    _format_tracker_sheet(sheets, sheet_id)

    cache_path.write_text(sheet_id)
    logger.info(f"Created new tracker sheet: {sheet_id}")
    return sheet_id


def _format_tracker_sheet(sheets, sheet_id: str) -> None:
    """Apply formatting to the tracker sheet (bold header, column widths)."""
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tab_id = meta["sheets"][0]["properties"]["sheetId"]

    requests = [
        # Bold + blue-tinted header row
        {
            "repeatCell": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {
                            "red": 0.9, "green": 0.93, "blue": 0.97,
                        },
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        # Column widths: Date=120, #=40, Title=350, Done=60, Done At=200
        *[
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": tab_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
            for i, width in enumerate([120, 40, 350, 60, 200])
        ],
    ]

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": requests},
    ).execute()


# ─── URL helpers ────────────────────────────────────────────────────────────

def get_tracker_sheet_url(config: SyncConfig) -> Optional[str]:
    """Return the Google Sheets URL (creates sheet if needed), or None on error."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    except Exception as exc:
        logger.warning(f"Could not get tracker sheet URL: {exc}")
        return None


def get_tracker_sheet_url_cached(config: SyncConfig) -> Optional[str]:
    """Return the Google Sheets URL from local cache only (no API call)."""
    cache_path = _sheet_id_cache_path(config)
    if cache_path.exists():
        sheet_id = cache_path.read_text().strip()
        if sheet_id:
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    return None


# ─── Write actions ──────────────────────────────────────────────────────────

def add_daily_actions(
    config: SyncConfig, day: str, actions: List[Dict[str, Any]]
) -> bool:
    """Append today's actions to the Sheet. Idempotent: skips if date exists."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        sheets = _build_sheets_service(config)

        # Check whether rows for this date already exist
        result = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!A:A",
        ).execute()
        existing_dates = [
            row[0] for row in result.get("values", []) if row
        ]
        if day in existing_dates:
            logger.info(f"Actions for {day} already in Sheet, skipping.")
            return True

        # Build rows: [Date, #, Title, FALSE, ""]
        rows = []
        for action in actions:
            rows.append([
                day,
                action["idx"] + 1,       # 1-based for readability
                action["title"],
                False,                    # Checkbox unchecked
                "",                       # Done At (empty)
            ])

        if not rows:
            return True

        sheets.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!A:E",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

        # Add checkbox data-validation to the Done column for new rows
        _add_checkboxes_for_new_rows(sheets, sheet_id, len(rows))

        logger.info(f"Added {len(rows)} actions for {day} to Sheet.")
        return True

    except Exception as exc:
        logger.warning(f"Failed to add actions to Sheet: {exc}")
        return False


def _add_checkboxes_for_new_rows(
    sheets, sheet_id: str, num_new_rows: int
) -> None:
    """Set boolean data-validation (checkbox UI) on the Done column for new rows."""
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
        tab_id = meta["sheets"][0]["properties"]["sheetId"]

        # Find the total row count so we can target the last N rows
        result = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!A:A",
        ).execute()
        total_rows = len(result.get("values", []))
        start_row = total_rows - num_new_rows  # 0-based

        requests = [{
            "setDataValidation": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": start_row,
                    "endRowIndex": total_rows,
                    "startColumnIndex": 3,   # column D
                    "endColumnIndex": 4,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "showCustomUi": True,
                },
            }
        }]

        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        logger.warning(f"Failed to add checkboxes: {exc}")


# ─── Read actions ───────────────────────────────────────────────────────────

def _parse_done(val: Any) -> bool:
    """Interpret the Done column value as a boolean."""
    if isinstance(val, bool):
        return val
    return str(val).upper() == "TRUE"


def read_action_status(
    config: SyncConfig, day: str
) -> List[Dict[str, Any]]:
    """Read action status for a given day from the Sheet."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        sheets = _build_sheets_service(config)

        result = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!A:E",
        ).execute()

        actions = []
        for row in result.get("values", [])[1:]:   # skip header
            if len(row) >= 3 and row[0] == day:
                done = _parse_done(row[3]) if len(row) > 3 else False
                done_at = row[4] if len(row) > 4 and row[4] else None
                actions.append({
                    "idx": int(row[1]) - 1,   # back to 0-based
                    "title": row[2],
                    "done": done,
                    "done_at": done_at,
                })
        return actions

    except Exception as exc:
        logger.warning(f"Failed to read actions from Sheet: {exc}")
        return []


def read_action_history(
    config: SyncConfig, num_days: int = 7
) -> List[Dict[str, Any]]:
    """Read the last N days of action data from the Sheet, most recent first."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        sheets = _build_sheets_service(config)

        result = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!A:E",
        ).execute()

        today = date.today()
        target_dates = {
            (today - timedelta(days=i)).isoformat() for i in range(num_days)
        }

        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for row in result.get("values", [])[1:]:
            if len(row) >= 3 and row[0] in target_dates:
                day_str = row[0]
                done = _parse_done(row[3]) if len(row) > 3 else False
                done_at = row[4] if len(row) > 4 and row[4] else None

                by_date.setdefault(day_str, []).append({
                    "idx": int(row[1]) - 1,
                    "title": row[2],
                    "done": done,
                    "done_at": done_at,
                })

        history = []
        for day_str in sorted(by_date, reverse=True):
            actions = by_date[day_str]
            done_count = sum(1 for a in actions if a["done"])
            history.append({
                "date": day_str,
                "actions": actions,
                "completion_rate": done_count / len(actions) if actions else 0,
            })
        return history

    except Exception as exc:
        logger.warning(f"Failed to read action history from Sheet: {exc}")
        return []


# ─── Update actions ─────────────────────────────────────────────────────────

def _find_row_index(sheets, sheet_id: str, day: str, idx: int) -> Optional[int]:
    """Find the 1-based row number for a (date, action-index) pair."""
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{SHEET_TAB_NAME}!A:B",
    ).execute()
    for i, row in enumerate(result.get("values", [])):
        if len(row) >= 2 and row[0] == day and str(row[1]) == str(idx + 1):
            return i + 1          # 1-based for A1 notation
    return None


def mark_action_done_sheet(
    config: SyncConfig, day: str, idx: int
) -> bool:
    """Set Done=TRUE in the Sheet for the given action."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        sheets = _build_sheets_service(config)

        row_idx = _find_row_index(sheets, sheet_id, day, idx)
        if row_idx is None:
            logger.warning(f"Sheet row not found for {day} idx={idx}")
            return False

        now = datetime.utcnow().isoformat() + "Z"
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!D{row_idx}:E{row_idx}",
            valueInputOption="USER_ENTERED",
            body={"values": [[True, now]]},
        ).execute()
        logger.info(f"Marked action {idx} on {day} as done in Sheet.")
        return True

    except Exception as exc:
        logger.warning(f"Failed to mark action done in Sheet: {exc}")
        return False


def mark_action_undone_sheet(
    config: SyncConfig, day: str, idx: int
) -> bool:
    """Set Done=FALSE in the Sheet for the given action."""
    try:
        sheet_id = get_or_create_tracker_sheet(config)
        sheets = _build_sheets_service(config)

        row_idx = _find_row_index(sheets, sheet_id, day, idx)
        if row_idx is None:
            logger.warning(f"Sheet row not found for {day} idx={idx}")
            return False

        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB_NAME}!D{row_idx}:E{row_idx}",
            valueInputOption="USER_ENTERED",
            body={"values": [[False, ""]]},
        ).execute()
        logger.info(f"Unmarked action {idx} on {day} in Sheet.")
        return True

    except Exception as exc:
        logger.warning(f"Failed to unmark action in Sheet: {exc}")
        return False


# ─── Sync Sheet → local JSON ───────────────────────────────────────────────

def sync_sheet_to_local(config: SyncConfig, day: str) -> None:
    """Pull action status from Sheet and update the local JSON cache."""
    try:
        from .action_tracker import save_actions

        actions = read_action_status(config, day)
        if actions:
            save_actions(config.data_dir, day, actions)
            logger.info(f"Synced Sheet → local for {day}: {len(actions)} actions.")
    except Exception as exc:
        logger.warning(f"Failed to sync Sheet to local: {exc}")
