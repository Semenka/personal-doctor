from __future__ import annotations

from typing import Dict, Iterable, List

import requests

from .models import ResearchPaper
from ..sync.config import SyncConfig

OPENALEX_BASE_URL = "https://api.openalex.org/works"


def _build_journal_filter(journal: str, mode: str = "exact") -> str:
    cleaned = journal.replace("\"", "").strip()
    if mode == "search":
        return f"primary_location.source.display_name.search:{cleaned}"
    return f"primary_location.source.display_name:\"{cleaned}\""


def _fetch_with_filter(config: SyncConfig, filter_value: str, per_page: int) -> Dict:
    params = {
        "filter": filter_value,
        "sort": "cited_by_count:desc",
        "per_page": per_page,
    }
    if config.openalex_mailto:
        params["mailto"] = config.openalex_mailto
    response = requests.get(OPENALEX_BASE_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _fetch_with_search(config: SyncConfig, query: str, per_page: int) -> Dict:
    params = {
        "search": query,
        "sort": "cited_by_count:desc",
        "per_page": per_page,
    }
    if config.openalex_mailto:
        params["mailto"] = config.openalex_mailto
    response = requests.get(OPENALEX_BASE_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_top_papers(
    config: SyncConfig,
    journals: Iterable[str],
    per_page: int = 100,
) -> List[ResearchPaper]:
    papers: List[ResearchPaper] = []
    seen: set[str] = set()
    for journal in journals:
        filter_value = _build_journal_filter(journal, mode="exact")
        try:
            payload = _fetch_with_filter(config, filter_value, per_page)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
            filter_value = _build_journal_filter(journal, mode="search")
            try:
                payload = _fetch_with_filter(config, filter_value, per_page)
            except requests.HTTPError as exc_search:
                if exc_search.response is None or exc_search.response.status_code != 400:
                    raise
                payload = _fetch_with_search(config, journal, per_page)

        results = payload.get("results", [])
        for item in results:
            work_id = item.get("id", "")
            if not work_id or work_id in seen:
                continue
            seen.add(work_id)
            source = item.get("primary_location", {}).get("source", {})
            papers.append(
                ResearchPaper(
                    work_id=work_id,
                    title=item.get("title", "Untitled"),
                    journal=source.get("display_name", "Unknown"),
                    cited_by_count=int(item.get("cited_by_count") or 0),
                    publication_date=item.get("publication_date"),
                    url=item.get("doi") or item.get("id"),
                )
            )
    return papers
