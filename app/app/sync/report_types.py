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
        r"(blood[\s_-]?test|hemo(glo|gram)|cbc|complete[\s_-]?blood|lipid[\s_-]?panel"
        r"|blood[\s_-]?work|blood[\s_-]?analysis|hemato)",
        re.IGNORECASE,
    ),
    "urine_test": re.compile(
        r"(urine[\s_-]?test|urinalysis|urine[\s_-]?analysis|urine[\s_-]?sample)",
        re.IGNORECASE,
    ),
    "genetic_test": re.compile(
        r"(genetic[\s_-]?test|dna[\s_-]?test|genom|23andme|ancestry|snp|genetic[\s_-]?analysis"
        r"|karyotype|genetic[\s_-]?screen)",
        re.IGNORECASE,
    ),
    "sperm_test": re.compile(
        r"(sperm[\s_-]?test|spermogram|semen[\s_-]?analysis|sperm[\s_-]?count"
        r"|sperm[\s_-]?motil|seminogram)",
        re.IGNORECASE,
    ),
    "doctor_conclusion": re.compile(
        r"(doctor[\s_-]?conclusion|medical[\s_-]?conclusion|diagnosis|clinical[\s_-]?summary"
        r"|discharge[\s_-]?summary|doctor[\s_-]?report|medical[\s_-]?report"
        r"|check[\s_-]?up|annual[\s_-]?exam|consultation[\s_-]?report)",
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
        r"|comprehensive[\s_-]?exam)",
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
