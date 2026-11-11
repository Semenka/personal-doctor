from __future__ import annotations

import re
from typing import Optional

REPORT_TYPES = {
    "blood": "blood_test",
    "urine": "urine_test",
    "genetic": "genetic_test",
    "sperm": "sperm_test",
    "conclusion": "doctor_conclusion",
    "prescription": "prescription",
    "scan": "medical_image",
    "health_check": "health_check",
}

REPORT_TYPE_VALUES = set(REPORT_TYPES.values())

_PATTERNS = {
    "blood_test": re.compile(
        r"(blood[\s_-]?(test|panel|work|analysis|results|report)|hemo(glo|gram)|cbc"
        r"|complete[\s_-]?blood|lipid[\s_-]?panel|hemato)",
        re.IGNORECASE,
    ),
    "urine_test": re.compile(
        r"(urine[\s_-]?(test|analysis|sample|results|report)|urinalysis)",
        re.IGNORECASE,
    ),
    "genetic_test": re.compile(
        r"(genetic[\s_-]?(test|analysis|report|screen|results)|dna[\s_-]?test|genom"
        r"|23andme|ancestry|snp|karyotype)",
        re.IGNORECASE,
    ),
    "sperm_test": re.compile(
        r"(sperm[\s_-]?(test|analysis|count|motil|report|results)|spermogram"
        r"|semen[\s_-]?analysis|seminogram)",
        re.IGNORECASE,
    ),
    "doctor_conclusion": re.compile(
        r"(doctor[\s_-]?(conclusion|report)|medical[\s_-]?(conclusion|report)"
        r"|diagnosis|clinical[\s_-]?summary|discharge[\s_-]?summary"
        r"|consultation[\s_-]?report)",
        re.IGNORECASE,
    ),
    "prescription": re.compile(
        r"(prescription|ordonnance|rx[\s_-]|prescribed[\s_-]?medic|treatment[\s_-]?plan"
        r"|medication[\s_-]?list)",
        re.IGNORECASE,
    ),
    "health_check": re.compile(
        r"(health[\s_-]?check|medical[\s_-]?check[\s_-]?up|annual[\s_-]?check[\s_-]?up"
        r"|general[\s_-]?check[\s_-]?up|routine[\s_-]?exam|preventive[\s_-]?exam"
        r"|wellness[\s_-]?exam|physical[\s_-]?exam|full[\s_-]?body[\s_-]?check"
        r"|comprehensive[\s_-]?exam|check[\s_-]?up|annual[\s_-]?exam)",
        re.IGNORECASE,
    ),
}


def classify_report(filename: str, text_content: Optional[str] = None) -> Optional[str]:
    """Classify a health report by filename and optional text content.

    Returns one of the REPORT_TYPE_VALUES or None if unrecognized.
    """
    # Check filename first
    for report_type, pattern in _PATTERNS.items():
        if pattern.search(filename):
            return report_type

    # Fall back to text content
    if text_content:
        scores = {}
        for report_type, pattern in _PATTERNS.items():
            matches = pattern.findall(text_content[:3000])
            if matches:
                scores[report_type] = len(matches)
        if scores:
            return max(scores, key=scores.get)

    return None


# Words that show a document is medical at all (EN + FR, the languages of the
# user's labs). Used only to stop a folder's default type from being stamped
# on something that is plainly not a medical report.
_MEDICAL_SIGNAL = re.compile(
    r"\b(laborato\w+|patient\w*|sperm\w*|semen|biolog\w+|m[ée]dec\w+|medical\w*|m[ée]dical\w*"
    r"|clini\w+|docteur|doctor|physician|pr[ée]l[èe]vement|specimen|sample"
    r"|r[ée]sultats?|results?|reference range|valeurs? de r[ée]f[ée]rence|diagnos\w+"
    r"|hormon\w+|testost[ée]ron\w*|h[ée]mo\w+|mmol/l|mg/dl|ng/ml|ui/l|iu/l|g/l"
    r"|ordonnance|prescri\w+|posologie|analyse\w*|examen\w*|radiolog\w+|[ée]chograph\w+"
    r"|пациент\w*|лаборатор\w*|результат\w*|анализ\w*|спермограмм\w*|генетическ\w*"
    r"|референсн\w*|исследован\w*|врач\w*|диагноз\w*)\b",
    re.IGNORECASE,
)
_MIN_TEXT_FOR_CONTENT_CHECK = 200


def _text_scores(text: str) -> dict:
    scores = {}
    for report_type, pattern in _PATTERNS.items():
        n = len(pattern.findall(text[:6000]))
        if n:
            scores[report_type] = n
    return scores


def resolve_kind(
    filename: str,
    text_content: Optional[str] = None,
    override: Optional[str] = None,
    folder_kind: Optional[str] = None,
) -> str:
    """Final report type for a Drive file.

    ``override`` (an explicit CLI choice) always wins. ``folder_kind`` — the
    default of the folder the file came from — is right for photos and scans
    with no extractable text, but it used to win over the content too: a
    boarding pass, event tickets and a deal memo dropped in the archive folder
    were all filed (and summarised) as sperm tests. Now, when the file has
    text:

    - text with no medical vocabulary at all → ``unclassified`` (kept, but
      never sent to biomarker extraction or the advisor as a lab result);
    - otherwise the folder default stands.
    """
    if override:
        return override
    text = (text_content or "").strip()
    if not folder_kind:
        return classify_report(filename, text) or "unclassified"
    if len(text) < _MIN_TEXT_FOR_CONTENT_CHECK:
        return folder_kind
    scores = _text_scores(text)
    if not scores.get(folder_kind):
        if not scores and len({m.lower() for m in _MEDICAL_SIGNAL.findall(text[:6000])}) < 2:
            return "unclassified"
    return folder_kind


def settle_unreadable(folder_kind: str, readings_found: int) -> tuple:
    """Kind and note for a file with no extractable text.

    ``readings_found`` is how many biomarker values a vision pass read from it
    (0 for a scanned PDF, which gets no vision pass). With values it is the
    lab result its folder says it is. Without, nothing shows it is one — it is
    kept as ``unclassified`` with a note naming the folder default, so it is
    never counted as a test that happened.
    """
    if readings_found > 0:
        return folder_kind, ""
    return "unclassified", (
        f"No readable content (photo or scan without text, no lab values found); "
        f"the folder default was {folder_kind}. Not treated as a result — "
        f"open the file to check what it is."
    )
