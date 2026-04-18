"""PubMed E-utilities connector.

No API key required. We use esearch → esummary for lightweight paper metadata
(title, journal, pub date, first author, PMID). Citations-by count is NOT
available from E-utilities; we combine with OpenAlex later for citation rank.

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import List, Optional

import requests

from .models import ResearchPaper

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _search_pmids(
    query: str,
    since_months: int,
    retmax: int = 10,
    mailto: Optional[str] = None,
) -> List[str]:
    today = date.today()
    start = today - timedelta(days=30 * since_months)
    date_filter = f"{start.strftime('%Y/%m/%d')}:{today.strftime('%Y/%m/%d')}[pdat]"
    params = {
        "db": "pubmed",
        "term": f"{query} AND {date_filter}",
        "retmax": retmax,
        "sort": "relevance",
        "retmode": "json",
    }
    if mailto:
        params["email"] = mailto
        params["tool"] = "personal-doctor"
    r = requests.get(ESEARCH_URL, params=params, timeout=20)
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    return list(ids)


def _summarize_pmids(pmids: List[str], mailto: Optional[str] = None) -> List[dict]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }
    if mailto:
        params["email"] = mailto
        params["tool"] = "personal-doctor"
    r = requests.get(ESUMMARY_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("result", {})
    out = []
    for pmid in pmids:
        entry = data.get(pmid)
        if not entry:
            continue
        out.append(entry)
    return out


def fetch_papers_for_query(
    query: str,
    since_months: int = 24,
    retmax: int = 5,
    mailto: Optional[str] = None,
) -> List[ResearchPaper]:
    """Return up to ``retmax`` PubMed papers matching ``query`` published in the
    last ``since_months`` months, ranked by PubMed's relevance score.
    """
    pmids = _search_pmids(query, since_months, retmax=retmax, mailto=mailto)
    # E-utilities has a "no more than 3 requests per second" rule without key
    time.sleep(0.35)
    entries = _summarize_pmids(pmids, mailto=mailto)
    papers: List[ResearchPaper] = []
    for e in entries:
        pmid = e.get("uid") or ""
        title = e.get("title", "Untitled")
        journal = e.get("fulljournalname") or e.get("source") or "PubMed"
        pub_date = e.get("pubdate") or ""
        # E-utilities doesn't return citation counts — leave 0 (will be refined later if
        # merged with OpenAlex)
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        papers.append(
            ResearchPaper(
                work_id=f"pmid:{pmid}",
                title=title.rstrip("."),
                journal=journal,
                cited_by_count=0,
                publication_date=pub_date,
                url=url,
            )
        )
    return papers
