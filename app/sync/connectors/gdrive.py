from __future__ import annotations

import io
import json
import mimetypes
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from ..config import SyncConfig

SCOPES = ["https://www.googleapis.com/auth/drive"]

HEALTH_FOLDER_PATH = "me/health"


class DriveConnectorError(RuntimeError):
    pass


def _get_credentials(config: SyncConfig) -> Credentials:
    """Load or refresh Google OAuth2 credentials.

    Looks for token.json (cached creds) and credentials.json (OAuth client
    secrets) in config.gdrive_credentials_dir.
    """
    creds_dir = config.gdrive_credentials_dir
    if creds_dir is None:
        raise DriveConnectorError(
            "GDRIVE_CREDENTIALS_DIR not set. "
            "Point it to a directory containing credentials.json from Google Cloud Console."
        )
    creds_dir = Path(creds_dir)
    token_path = creds_dir / "token.json"
    client_secrets = creds_dir / "credentials.json"

    creds: Optional[Credentials] = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not client_secrets.exists():
            raise DriveConnectorError(
                f"Missing {client_secrets}. Download OAuth client JSON from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return creds


def _build_service(config: SyncConfig):
    creds = _get_credentials(config)
    return build("drive", "v3", credentials=creds)


def _resolve_folder_id(service, folder_path: str) -> str:
    """Walk a slash-separated path under My Drive and return the final folder ID."""
    parent_id = "root"
    for part in folder_path.split("/"):
        query = (
            f"'{parent_id}' in parents "
            f"and name = '{part}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        results = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
        files = results.get("files", [])
        if not files:
            raise DriveConnectorError(f"Folder not found: '{part}' under parent {parent_id}")
        parent_id = files[0]["id"]
    return parent_id


def _resolve_or_create_folder(service, folder_path: str) -> str:
    """Walk a slash-separated path, creating missing folders along the way."""
    parent_id = "root"
    for part in folder_path.split("/"):
        query = (
            f"'{parent_id}' in parents "
            f"and name = '{part}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        results = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
        files = results.get("files", [])
        if files:
            parent_id = files[0]["id"]
        else:
            meta = {
                "name": part,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            created = service.files().create(body=meta, fields="id").execute()
            parent_id = created["id"]
    return parent_id


def list_files_in_health_folder(
    config: SyncConfig,
    folder_path: str = HEALTH_FOLDER_PATH,
) -> List[Dict[str, Any]]:
    """List all files in the Google Drive health folder."""
    service = _build_service(config)
    folder_id = _resolve_folder_id(service, folder_path)

    all_files: List[Dict[str, Any]] = []
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        all_files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return all_files


# Subfolder names that map to report types when found under me/health/
SUBFOLDER_REPORT_TYPES = {
    "genetic": "genetic_test",
    "genetics": "genetic_test",
    "blood": "blood_test",
    "urine": "urine_test",
    "sperm": "sperm_test",
    "prescription": "prescription",
    "prescriptions": "prescription",
    "conclusion": "doctor_conclusion",
    "conclusions": "doctor_conclusion",
    "health_check": "health_check",
    "health_checks": "health_check",
    "checkup": "health_check",
    "check-up": "health_check",
}


def _collect_files_deep(
    service,
    folder_id: str,
    report_type: str | None = None,
    subfolder_name: str | None = None,
    max_depth: int = 5,
    _depth: int = 0,
) -> List[Dict[str, Any]]:
    """Recursively collect all files under *folder_id*, descending into sub-subfolders.

    Each returned file dict gets ``subfolder_type`` and ``subfolder_name`` if
    *report_type* / *subfolder_name* are provided (i.e. we're inside a typed
    subfolder like ``me/health/genetic/``).

    *max_depth* prevents runaway recursion on deeply nested Drive trees.
    """
    if _depth > max_depth:
        return []

    collected: List[Dict[str, Any]] = []
    child_folders: List[str] = []

    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        for f in resp.get("files", []):
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                child_folders.append(f["id"])
            else:
                if report_type:
                    f["subfolder_type"] = report_type
                if subfolder_name:
                    f["subfolder_name"] = subfolder_name
                collected.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Recurse into child folders
    for child_id in child_folders:
        collected.extend(
            _collect_files_deep(
                service,
                child_id,
                report_type=report_type,
                subfolder_name=subfolder_name,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
        )

    return collected


def list_files_recursive(
    config: SyncConfig,
    folder_path: str = HEALTH_FOLDER_PATH,
) -> List[Dict[str, Any]]:
    """List files in the health folder AND its known subfolders.

    Files found in a typed subfolder (e.g. ``me/health/genetic/``) get an
    extra ``"subfolder_type"`` key so the pipeline can auto-classify them.

    Scanning is fully recursive — sub-subfolders within each typed folder
    are also traversed (e.g. ``me/health/genetic/2024/results.pdf``).
    """
    service = _build_service(config)
    folder_id = _resolve_folder_id(service, folder_path)

    all_files: List[Dict[str, Any]] = []

    # 1. Root-level files (non-recursive — only direct children)
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        for f in resp.get("files", []):
            if f.get("mimeType") != "application/vnd.google-apps.folder":
                all_files.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # 2. Scan known subfolders — recursively descend into sub-subfolders
    for subfolder_name, report_type in SUBFOLDER_REPORT_TYPES.items():
        query = (
            f"'{folder_id}' in parents "
            f"and name = '{subfolder_name}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        result = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
        folders = result.get("files", [])
        if not folders:
            continue

        sub_id = folders[0]["id"]
        all_files.extend(
            _collect_files_deep(
                service,
                sub_id,
                report_type=report_type,
                subfolder_name=subfolder_name,
            )
        )

    return all_files


def download_file(config: SyncConfig, file_id: str, dest: Path) -> Path:
    """Download a Drive file by ID to a local path."""
    service = _build_service(config)
    request = service.files().get_media(fileId=file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest


def upload_file(
    config: SyncConfig,
    local_path: Path,
    folder_path: str = HEALTH_FOLDER_PATH,
    file_name: Optional[str] = None,
) -> str:
    """Upload a local file to the health folder. Returns the Drive file ID."""
    service = _build_service(config)
    folder_id = _resolve_folder_id(service, folder_path)

    name = file_name or local_path.name
    mime_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

    file_metadata = {"name": name, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    created = service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    return created["id"]


def calendar_folder_path(day: date) -> str:
    """Return ``me/health/YYYY/MM/DD`` folder path for a given date."""
    return f"{HEALTH_FOLDER_PATH}/{day.strftime('%Y/%m/%d')}"


def upload_bytes(
    config: SyncConfig,
    content: bytes,
    file_name: str,
    mime_type: str = "application/json",
    folder_path: str = HEALTH_FOLDER_PATH,
    create_folders: bool = False,
) -> str:
    """Upload in-memory bytes to a Drive folder. Returns the Drive file ID.

    If *create_folders* is True, missing folders in *folder_path* are created
    automatically (used for calendar sub-folders like ``me/health/2026/02/10``).
    """
    service = _build_service(config)
    if create_folders:
        folder_id = _resolve_or_create_folder(service, folder_path)
    else:
        folder_id = _resolve_folder_id(service, folder_path)

    file_metadata = {"name": file_name, "parents": [folder_id]}
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    created = service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    return created["id"]
