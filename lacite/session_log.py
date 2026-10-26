"""The live session: what the writing agent looked up over MCP, in memory only.

``McpServer.call_tool`` (the one place every tool call passes) hands each call
to :func:`summarize` and the result to :meth:`SessionLog.record`. The log is a
ring buffer (``LIMIT`` calls): nothing goes to disk, a restart or "Sitzung
leeren" empties it. Each recorded entry is also handed to ``publish`` (the
Bundle's ``EventBroadcaster``) as ``{"type": "mcp-call", "entry": ...}``, so an
open live window gets it at once; ``GET /session`` is the history on opening
and the fallback.

The log never raises and never slows the MCP call: :meth:`SessionLog.record`
swallows every failure, and ``publish`` only queues (``call_soon_threadsafe``).

An entry::

    {"id": 7, "at": "2026-10-23T09:12:03Z", "client": {"name", "version"} | None,
     "tool": "find_passages", "args": {"query": "…"}, "count": 5,
     "hits": [{"citekey", "title", "year", "page_label", "pdf_page", "page",
               "text", "cite", "paper_id", "has_pdf", "created"}],
     "error": None | "…", "duration_ms": 41}

``paper_id``/``has_pdf`` are filled where the tool's own answer already knew
them (``search_library``, ``get_reference``, ``add_by_doi``), else ``None`` —
the page resolves the rest by citekey (``GET /api/papers/lookup?keys=``).
Pure apart from the lock that guards the buffer.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from typing import Any, Callable, Optional

LIMIT = 200
HIT_LIMIT = 20          # hits kept per call
EXCERPT_CHARS = 280     # text kept per hit
ARG_CHARS = 300         # an argument kept per call
CORE_ARGS = ("query", "citekey", "doi")


def _clip(value: Any, n: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _args(args: Any) -> dict:
    """The arguments a reader needs to follow the call: query, citekey, DOI."""
    if not isinstance(args, dict):
        return {}
    return {k: _clip(args[k], ARG_CHARS) for k in CORE_ARGS if args.get(k) not in (None, "")}


def _hit(raw: dict, known: dict, **extra: Any) -> dict:
    key = str(raw.get("citekey") or "")
    ids = known.get(key) or {}
    hit = {
        "citekey": key,
        "title": raw.get("title") or "",
        "year": raw.get("year"),
        "page_label": raw.get("page_label"),
        "pdf_page": raw.get("pdf_page"),
        "page": raw.get("page"),
        "text": _clip(raw.get("text"), EXCERPT_CHARS) if raw.get("text") else "",
        "cite": raw.get("cite") or "",
        "paper_id": ids.get("paper_id"),
        "has_pdf": ids.get("has_pdf"),
        "created": False,
    }
    hit.update(extra)
    return hit


def _hits(tool: str, data: dict, known: dict) -> tuple[list[dict], Optional[int]]:
    """The hits worth showing and the count the call produced."""
    if tool in ("search_library", "find_passages"):
        rows = data.get("results") or []
        return [_hit(r, known) for r in rows[:HIT_LIMIT]], len(rows)
    if tool == "read_passages":
        key = data.get("citekey") or ""
        rows = data.get("passages") or []
        return ([_hit({**r, "citekey": key}, known, index=r.get("index")) for r in rows[:HIT_LIMIT]],
                len(rows))
    if tool == "get_reference":
        return [_hit(data, known)], 1
    if tool == "add_by_doi":
        return [_hit(data, known, created=bool(data.get("created")))], 1
    if tool == "sync_bib":
        return [], len(data.get("connections") or [])
    return [], None


def summarize(tool: str, args: Any, data: Optional[dict], *, error: Optional[str] = None,
              client: Optional[dict] = None, at: str = "", duration_ms: int = 0,
              known: Optional[dict] = None) -> dict:
    """One log entry for one tool call. ``data`` is the tool's structured
    answer (``None`` on an error); ``known`` maps citekey ->
    ``{paper_id, has_pdf}`` where the tool already had them."""
    hits, count = _hits(tool, data, known or {}) if isinstance(data, dict) else ([], None)
    return {
        "at": at,
        "client": ({"name": str(client.get("name") or ""), "version": str(client.get("version") or "")}
                   if isinstance(client, dict) else None),
        "tool": tool,
        "args": _args(args),
        "count": count,
        "hits": hits,
        "error": _clip(error, 500) if error else None,
        "duration_ms": max(0, int(duration_ms)),
    }


class SessionLog:
    """The ring buffer. ``publish`` gets every event (an ``mcp-call`` per
    recorded entry, ``session-cleared`` on :meth:`clear`); its failures are
    swallowed like everything else here."""

    def __init__(self, limit: int = LIMIT,
                 publish: Optional[Callable[[dict], None]] = None) -> None:
        self._entries: deque = deque(maxlen=max(1, int(limit)))
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.publish = publish

    def record(self, entry: dict) -> None:
        """Keep ``entry`` (it gets the next ``id``) and announce it. Never raises."""
        try:
            with self._lock:
                stored = {**entry, "id": next(self._ids)}
                self._entries.append(stored)
            self._announce({"type": "mcp-call", "entry": stored})
        except Exception:  # the log must never break or slow an MCP call
            pass

    def entries(self) -> list[dict]:
        """The calls in the buffer, oldest first."""
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """"Sitzung leeren": the buffer goes, ids keep counting."""
        with self._lock:
            self._entries.clear()
        self._announce({"type": "session-cleared"})

    def _announce(self, event: dict) -> None:
        publish = self.publish
        if publish is None:
            return
        try:
            publish(event)
        except Exception:
            pass
