"""Read-only access to gbrain, the shared personal memory.

gbrain holds what reaches the user from everywhere else — mail, chats,
documents, other agents' sessions. The Health OS only ever *reads* it, and
only through the sanctioned read-only stdio wrapper that OpenClaw ships
(``gbrain-readonly-mcp.mjs``): the wrapper owns the credential and the
allow-list (recall, entity, search, get_page, list_pages), so this module
never touches the token file and cannot write, and it never opens the PGlite
database itself.

Rules this client keeps:
- search only, with two or more specific terms (generic corpus-wide queries
  stall the brain); the wrapper caps limit and snippet size.
- results are returned to the caller and never written to disk — retrieved
  content must not be re-ingested.
- everything retrieved is untrusted data. Callers quote it to the model as
  data, never as instructions.
- any failure (wrapper missing, gbrain down, timeout) returns ``[]``; the
  Health OS answers from its own data instead.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("personal-doctor.gbrain")

DEFAULT_WRAPPER = (
    "~/.openclaw/local-bundles/gbrain-retrieval/bin/gbrain-readonly-mcp.mjs"
)
DEFAULT_URL = "http://127.0.0.1:3131/mcp"

_STOPWORDS = frozenset(
    """
    a about after all am an and any are as at be been before but by can could did
    do does doing done for from get give had has have how i if in into is it its
    last latest me my need now of on or our please say should show so some tell
    than that the their them then there these this to today tomorrow was we were
    what when where which who why will with would yesterday you your
    """.split()
)


def _node_bin() -> Optional[str]:
    """Find node without depending on launchd's pinned PATH.

    The service runs under launchd, whose PATH is frozen at install time and
    breaks on every node upgrade — so look in nvm's directory as well.
    """
    override = os.getenv("GBRAIN_NODE_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("node")
    if found:
        return found
    candidates = sorted(
        glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node")),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("/node/")[-1])[:3]],
    )
    for extra in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if Path(extra).exists():
            candidates.append(extra)
    return candidates[-1] if candidates else None


def _wrapper_path() -> Path:
    return Path(os.getenv("GBRAIN_MCP_WRAPPER", DEFAULT_WRAPPER)).expanduser()


def available() -> bool:
    if os.getenv("GBRAIN_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(_node_bin()) and _wrapper_path().exists()


def search_terms(question: str, max_terms: int = 6) -> List[str]:
    """Specific terms worth sending to gbrain (names, numbers, rare words)."""
    out: List[str] = []
    for tok in re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9()\-']+", question or ""):
        low = tok.lower().strip("'-")
        if len(low) < 3 or low in _STOPWORDS or low in out:
            continue
        out.append(low)
    return out[:max_terms]


def _call_tool(name: str, arguments: Dict[str, Any], timeout_s: int) -> Optional[Dict[str, Any]]:
    """One MCP tools/call over a short-lived stdio session with the wrapper."""
    return _call_tools([(name, arguments)], timeout_s)[0]


def _call_tools(
    calls: List[Any], timeout_s: int
) -> List[Optional[Dict[str, Any]]]:
    """Several tools/call requests in one wrapper session (one start-up cost)."""
    empty: List[Optional[Dict[str, Any]]] = [None] * len(calls)
    node = _node_bin()
    wrapper = _wrapper_path()
    if not node or not wrapper.exists():
        return empty
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "personal-doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ] + [
        {"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
        for i, (name, arguments) in enumerate(calls)
    ]
    env = dict(os.environ)
    env.setdefault("GBRAIN_HTTP_URL", DEFAULT_URL)
    proc = subprocess.Popen(
        [node, str(wrapper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env,
    )
    try:
        payload = "".join(json.dumps(r) + "\n" for r in requests)
        # The wrapper answers each line then keeps reading; closing stdin
        # (communicate) ends the session once the answers are flushed.
        out, _ = proc.communicate(payload, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        logger.warning(f"gbrain session timed out after {timeout_s}s")
        return empty
    results = list(empty)
    for line in (out or "").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        idx = msg.get("id")
        if not isinstance(idx, int) or not 100 <= idx < 100 + len(calls):
            continue
        if msg.get("error"):
            logger.warning(f"gbrain call error: {msg['error'].get('message')}")
            continue
        results[idx - 100] = msg.get("result") or {}
    return results


def _result_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise a tools/call result into [{title, source, date, text}]."""
    if result.get("isError"):
        return []
    data: Any = result.get("structuredContent")
    if data is None:
        text = "\n".join(
            c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"
        )
        # The JSON rows may be followed by a plain-text notice from gbrain.
        try:
            data, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except ValueError:
            return []
    if isinstance(data, dict):
        for key in ("results", "rows", "items", "pages", "hits"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    rows: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        text = (
            item.get("snippet") or item.get("chunk_text") or item.get("text")
            or item.get("content") or item.get("excerpt") or ""
        )
        if not isinstance(text, str) or not text.strip():
            continue
        rows.append({
            "title": str(item.get("slug") or item.get("page_slug") or item.get("title") or ""),
            "source": str(item.get("source_id") or item.get("source") or ""),
            "evidence": str(item.get("evidence") or ""),
            "date": str(
                item.get("effective_date") or item.get("date")
                or item.get("updated_at") or item.get("created_at") or ""
            )[:10],
            "text": _clean_snippet(text),
        })
    return rows


# Agent transcripts quote the Health OS's own output back; original mail,
# chats and documents are the better evidence, so they rank first.
AGENT_LOG_SOURCES = frozenset({"codex", "claude-code", "claude-cowork", "openclaw"})


def _clean_snippet(text: str) -> str:
    text = re.sub(r"^\[quoted [^\]]*\]\s*", "", text.strip())
    text = re.sub(r"…?\s*\[truncated \d+ chars[^\]]*\]", " …", text)
    text = text.replace("```text", " ").replace("```", " ")
    return " ".join(text.split())


# The Health OS mails its own plans and briefs, and gbrain captures that
# mailbox — its own output is not independent evidence either.
_OWN_OUTPUT = ("Daily Health Plan —", "Health OS — week", "Weekly Retrospective —")


def _is_echo(row: Dict[str, Any]) -> bool:
    return row.get("source") in AGENT_LOG_SOURCES or any(
        marker in row.get("text", "")[:400] for marker in _OWN_OUTPUT
    )


def _rank(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=_is_echo)


def _queries(terms: List[str]) -> List[str]:
    """Full term set first, then narrower ones: gbrain's keyword leg wants
    every term to match, so a long question often matches nothing."""
    out = [" ".join(terms)]
    if len(terms) > 3:
        out.append(" ".join(terms[:3]))
    if len(terms) > 2:
        out.append(" ".join(terms[:2]))
        out.append(" ".join(terms[-2:]))
    seen: List[str] = []
    for q in out:
        if q not in seen:
            seen.append(q)
    return seen[:4]


def search(query: str, limit: int = 6, snippet_chars: int = 600, timeout_s: int = 60) -> List[Dict[str, Any]]:
    """Search gbrain. Returns [] when unavailable or the query is too generic."""
    if not available():
        return []
    terms = search_terms(query)
    if len(terms) < 2:
        return []
    calls = [
        ("search", {"query": q, "limit": 10, "snippet_chars": snippet_chars})
        for q in _queries(terms)
    ]
    try:
        results = _call_tools(calls, timeout_s)
    except Exception as exc:  # noqa: BLE001 — memory is optional, never fatal
        logger.warning(f"gbrain search failed: {exc}")
        return []
    rows: List[Dict[str, Any]] = []
    seen = set()
    for result in results:
        for row in _result_rows(result or {}):
            key = row["text"][:80]
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return _rank(rows)[:limit]


def findings_for_question(question: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Memory snippets relevant to a free-text question (may be empty)."""
    return search(question, limit=limit)
