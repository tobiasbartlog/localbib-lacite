"""Where the Schlussberichte stay: the Add-on's own database (contract 2.7).

``ReportStore(conn)`` keeps one row per report in table ``reports``; the body
(``reports.build``: title, summary, claims, counts) is JSON, the columns are
what the list needs. ``conn`` is ``api.storage.open_plugin_db("reports")``, or
an in-memory database on a core before 2.7 (the reports then last until the
next start — the page says so via ``persistent``).

Access is serialized by one lock: the handle is shared by the MCP threadpool
and the request handlers. Writing a report replaces it only when the agent
names it (``replaces``); a report is never deleted here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    title       TEXT NOT NULL,
    client_name TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    lookups     INTEGER,
    body        TEXT NOT NULL
)
"""

LIST_COLUMNS = ("id", "created_at", "updated_at", "title", "client_name", "project", "lookups")


class ReportStore:
    def __init__(self, conn: Any, *, persistent: bool = True) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self.persistent = persistent
        with self._lock:
            self._conn.execute(SCHEMA)
            self._conn.commit()

    @classmethod
    def in_memory(cls) -> "ReportStore":
        return cls(sqlite3.connect(":memory:", check_same_thread=False), persistent=False)

    def save(self, body: dict, *, at: str, client_name: str = "", project: str = "",
             lookups: Optional[int] = None, replaces: Optional[int] = None) -> dict:
        """Store ``body`` as a new report, or over report ``replaces`` when it
        exists (it keeps its id and ``created_at``). Returns the full report."""
        data = json.dumps(body, ensure_ascii=False)
        with self._lock:
            row = None
            if replaces is not None:
                row = self._conn.execute("SELECT id FROM reports WHERE id = ?", (replaces,)).fetchone()
            if row is not None:
                self._conn.execute(
                    "UPDATE reports SET updated_at = ?, title = ?, client_name = ?, project = ?, "
                    "lookups = ?, body = ? WHERE id = ?",
                    (at, body["title"], client_name, project, lookups, data, row[0]))
                report_id = row[0]
            else:
                cur = self._conn.execute(
                    "INSERT INTO reports (created_at, updated_at, title, client_name, project, lookups, body) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (at, at, body["title"], client_name, project, lookups, data))
                report_id = cur.lastrowid
            self._conn.commit()
        return self.get(report_id)  # type: ignore[return-value]

    def get(self, report_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(LIST_COLUMNS)}, body FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _full(row) if row is not None else None

    def all(self) -> list[dict]:
        """Every report, full, newest first (for the search)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(LIST_COLUMNS)}, body FROM reports ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [_full(r) for r in rows]

    def summaries(self, full: Optional[list[dict]] = None) -> list[dict]:
        """The list: every report without its claims, newest first. Pass
        ``full`` (from :meth:`all`) to reuse rows already read."""
        out = []
        for report in (self.all() if full is None else [dict(r) for r in full]):
            report.pop("claims", None)
            report.pop("summary", None)
            out.append(report)
        return out

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]


def _full(row: tuple) -> dict:
    meta = dict(zip(LIST_COLUMNS, row[:len(LIST_COLUMNS)]))
    try:
        body = json.loads(row[-1])
    except (TypeError, ValueError):
        body = {"title": meta["title"], "summary": "", "claims": [], "counts": {}}
    return {**body, **meta}
