from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import psycopg

from .config import SyncConfig


def write_daily_json(data_dir: Path, date: str, payload: Dict[str, Any]) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / f"daily_{date}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return target


def write_lab_document_json(
    data_dir: Path,
    kind: str,
    date: str,
    payload: Dict[str, Any],
) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / f"{kind}_{date}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return target


def init_db(config: SyncConfig) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        conn.execute(
            """
            create table if not exists daily_data (
                date date primary key,
                source text not null,
                payload jsonb not null,
                created_at timestamptz default now()
            );
            """
        )
        conn.execute(
            """
            create table if not exists research_papers (
                work_id text primary key,
                journal text not null,
                title text not null,
                cited_by_count int not null,
                publication_date text,
                url text,
                date date not null,
                created_at timestamptz default now()
            );
            """
        )
        conn.execute(
            """
            create table if not exists research_recommendations (
                id serial primary key,
                date date not null,
                goal text not null,
                action text not null,
                expected_impact_pct double precision not null,
                evidence text not null,
                paper_title text not null,
                journal text not null,
                cited_by_count int not null,
                url text,
                created_at timestamptz default now()
            );
            """
        )
        conn.execute(
            """
            create table if not exists lab_documents (
                id serial primary key,
                kind text not null,
                date date not null,
                raw_text text not null,
                metadata jsonb,
                created_at timestamptz default now()
            );
            """
        )


def save_daily_payload_db(config: SyncConfig, payload: Dict[str, Any]) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        conn.execute(
            """
            insert into daily_data (date, source, payload)
            values (%s, %s, %s)
            on conflict (date)
            do update set source = excluded.source, payload = excluded.payload;
            """,
            (payload["date"], payload.get("source", "unknown"), json.dumps(payload)),
        )


def save_lab_document_db(
    config: SyncConfig,
    kind: str,
    date: str,
    raw_text: str,
    metadata: Dict[str, Any],
) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        conn.execute(
            """
            insert into lab_documents (kind, date, raw_text, metadata)
            values (%s, %s, %s, %s);
            """,
            (kind, date, raw_text, json.dumps(metadata)),
        )


def save_research_papers_db(
    config: SyncConfig, date: str, papers: Iterable[Dict[str, Any]]
) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    payload = [
        (
            paper["work_id"],
            paper["journal"],
            paper["title"],
            int(paper["cited_by_count"]),
            paper.get("publication_date"),
            paper.get("url"),
            date,
        )
        for paper in papers
    ]
    if not payload:
        return
    with psycopg.connect(config.database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into research_papers (
                    work_id, journal, title, cited_by_count, publication_date, url, date
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (work_id)
                do update set
                    journal = excluded.journal,
                    title = excluded.title,
                    cited_by_count = excluded.cited_by_count,
                    publication_date = excluded.publication_date,
                    url = excluded.url,
                    date = excluded.date;
                """,
                payload,
            )


def delete_research_papers_except(config: SyncConfig, date: str) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        conn.execute(
            "delete from research_papers where date <> %s;",
            (date,),
        )


def save_research_recommendations_db(
    config: SyncConfig, recommendations: Iterable[Dict[str, Any]]
) -> None:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    payload = [
        (
            rec["date"],
            rec["goal"],
            rec["action"],
            float(rec["expected_impact_pct"]),
            rec["evidence"],
            rec["paper_title"],
            rec["journal"],
            int(rec["cited_by_count"]),
            rec.get("url"),
        )
        for rec in recommendations
    ]
    if not payload:
        return
    with psycopg.connect(config.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from research_recommendations where date = %s;",
                (payload[0][0],),
            )
            cur.executemany(
                """
                insert into research_recommendations (
                    date, goal, action, expected_impact_pct, evidence,
                    paper_title, journal, cited_by_count, url
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                payload,
            )


def load_research_recommendations_db(
    config: SyncConfig, date: str
) -> List[Dict[str, Any]]:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        rows = conn.execute(
            """
            select goal, action, expected_impact_pct, evidence,
                   paper_title, journal, cited_by_count, url
              from research_recommendations
             where date = %s
             order by goal, expected_impact_pct desc;
            """,
            (date,),
        ).fetchall()
    return [
        {
            "goal": row[0],
            "action": row[1],
            "expected_impact_pct": row[2],
            "evidence": row[3],
            "paper_title": row[4],
            "journal": row[5],
            "cited_by_count": row[6],
            "url": row[7],
        }
        for row in rows
    ]


def load_daily_payload_db(config: SyncConfig, date: str) -> Dict[str, Any]:
    if not config.database_url:
        raise ValueError("DATABASE_URL is required for Postgres storage")
    with psycopg.connect(config.database_url) as conn:
        row = conn.execute(
            "select payload from daily_data where date = %s;",
            (date,),
        ).fetchone()
    if not row:
        raise FileNotFoundError(f"No daily payload found for {date}")
    return row[0]


def load_daily_payload_file(data_dir: Path, date: str) -> Dict[str, Any]:
    target = data_dir / f"daily_{date}.json"
    if not target.exists():
        raise FileNotFoundError(f"No daily payload file found at {target}")
    return json.loads(target.read_text())


def load_daily_payload(config: SyncConfig, date: str) -> Dict[str, Any]:
    if config.database_url:
        return load_daily_payload_db(config, date)
    return load_daily_payload_file(config.data_dir, date)
