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
