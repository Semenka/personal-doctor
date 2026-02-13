"""Medical image analysis powered by Claude Opus 4.6 Vision.

Downloads MRI/X-ray/CT/ultrasound images from Google Drive and sends them
to Claude for radiological analysis.  The model flags potential pathologies
(tumours, ligament tears, meniscus damage, fractures, disc herniations, etc.)
and returns a structured report stored alongside other health data.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

from .config import SyncConfig

MODEL = "claude-opus-4-6"

# Filename / MIME patterns that indicate a medical image
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_MEDICAL_IMAGE_RE = re.compile(
    r"(mri|x[\s_-]?ray|xray|ct[\s_-]?scan|ultrasound|echo|radiograph|"
    r"mammogr|fluoroscop|angiogra|pet[\s_-]?scan|dexa|bone[\s_-]?scan|"
    r"sonogra|dicom|knee|spine|shoulder|hip|brain|chest|abdomen|pelvis|"
    r"lumbar|cervical|thoracic|ankle|wrist|elbow|meniscus|ligament|acl|"
    r"rotator[\s_-]?cuff|disc[\s_-]?herni)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """\
You are an experienced radiologist reviewing a medical image provided by your \
patient.  Your goal is to describe what you see and flag any abnormalities or \
potential pathologies.

Analyze the image and produce a structured report:

## Image Analysis

### Modality & Region
State the imaging modality (MRI, X-ray, CT, ultrasound, etc.) and anatomical \
region if identifiable.

### Findings
Describe ALL notable observations.  For each finding, state:
- **Location** (anatomical landmark)
- **Description** (size, signal intensity, density, morphology)
- **Significance** (normal variant, incidental, or concerning)

### Potential Pathologies
List any conditions suggested by the findings.  Include:
- Tumours / masses / cysts
- Ligament tears (ACL, PCL, MCL, LCL)
- Meniscus tears
- Disc herniations / bulges
- Fractures / stress reactions
- Degenerative changes (osteoarthritis, disc degeneration)
- Effusions / inflammation
- Other abnormalities

For each, rate concern level: LOW / MODERATE / HIGH.

### Recommendations
Suggest follow-up actions (additional imaging, specialist referral, monitoring).

### Severity Summary
One line: NORMAL / MINOR FINDINGS / MODERATE CONCERN / URGENT — SEEK SPECIALIST.

Be thorough and direct.  This is a screening tool — flag anything that \
warrants professional follow-up.  Do NOT dismiss subtle findings.
Keep the total response under 800 words."""


def is_medical_image(filename: str, mime_type: str) -> bool:
    """Return True if the file looks like a medical image (MRI/X-ray/CT/etc.)."""
    if mime_type not in IMAGE_MIMES:
        return False
    return bool(_MEDICAL_IMAGE_RE.search(filename))


def analyze_image(
    config: SyncConfig,
    image_path: Path,
    filename: str | None = None,
) -> Dict[str, Any]:
    """Send a local image to Claude Vision for radiological analysis.

    Returns a dict with the analysis text and metadata.
    """
    if not config.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for image analysis")

    fname = filename or image_path.name
    image_bytes = image_path.read_bytes()
    b64_data = base64.standard_b64encode(image_bytes).decode("ascii")

    # Determine media type
    suffix = image_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/jpeg")

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Please analyze this medical image.\n"
                            f"Filename: {fname}\n"
                            f"Look for any pathologies including but not limited to: "
                            f"cancer, tumours, ligament tears, meniscus damage, "
                            f"fractures, disc herniations, degenerative changes, "
                            f"effusions, and any other abnormalities."
                        ),
                    },
                ],
            }
        ],
    )

    analysis_text = response.content[0].text

    # Extract severity from the analysis
    severity = "UNKNOWN"
    for level in ["URGENT", "MODERATE CONCERN", "MINOR FINDINGS", "NORMAL"]:
        if level in analysis_text.upper():
            severity = level
            break

    return {
        "kind": "medical_image_analysis",
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": MODEL,
        "filename": fname,
        "severity": severity,
        "analysis": analysis_text,
    }


def analyze_image_from_drive(
    config: SyncConfig,
    file_id: str,
    filename: str,
    mime_type: str,
) -> Dict[str, Any]:
    """Download a Drive image and analyze it."""
    from .connectors.gdrive import download_file

    tmp_dir = config.data_dir / "gdrive_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_path = tmp_dir / filename
    download_file(config, file_id, local_path)

    try:
        result = analyze_image(config, local_path, filename)
        result["drive_file_id"] = file_id
        return result
    finally:
        local_path.unlink(missing_ok=True)


def save_analysis_local(config: SyncConfig, analysis: Dict[str, Any]) -> Path:
    """Save image analysis to a local JSON file."""
    out_dir = config.data_dir / "image_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = analysis.get("filename", "unknown").rsplit(".", 1)[0]
    day = analysis["date"]
    target = out_dir / f"scan_{fname}_{day}.json"
    target.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
    return target


def upload_analysis_to_drive(config: SyncConfig, analysis: Dict[str, Any]) -> str:
    """Upload image analysis report to Drive calendar folder."""
    from .connectors.gdrive import calendar_folder_path, upload_bytes

    day_str = analysis["date"]
    day_obj = date.fromisoformat(day_str)
    folder = calendar_folder_path(day_obj)

    fname = analysis.get("filename", "unknown").rsplit(".", 1)[0]
    severity = analysis.get("severity", "UNKNOWN")

    content = (
        f"Medical Image Analysis — {analysis['filename']}\n"
        f"Date: {day_str}\n"
        f"Severity: {severity}\n"
        f"Model: {analysis['model']}\n\n"
        f"{analysis['analysis']}\n"
    ).encode("utf-8")

    return upload_bytes(
        config,
        content,
        f"scan_{fname}_{day_str}.txt",
        mime_type="text/plain",
        folder_path=folder,
        create_folders=True,
    )


def load_image_analyses(config: SyncConfig) -> List[Dict[str, Any]]:
    """Load all saved image analyses from the data directory."""
    scan_dir = config.data_dir / "image_analysis"
    if not scan_dir.exists():
        return []
    results = []
    for f in sorted(scan_dir.glob("scan_*.json")):
        try:
            data = json.loads(f.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results
