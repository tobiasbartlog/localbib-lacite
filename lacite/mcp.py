"""A minimal MCP server (Streamable HTTP, stateless JSON-RPC 2.0) — pure dispatch.

``POST /api/plugins/lacite/mcp`` (``lacite/api.py``) hands every JSON-RPC
message to :meth:`McpServer.handle` and answers ``application/json``; no SSE
stream, no session id. Methods: ``initialize`` (remembers ``clientInfo`` and
the time for the page's "Zuletzt verbunden"), ``notifications/initialized``
and every other notification (no answer), ``ping``, ``tools/list``,
``tools/call``. Hand-written on purpose: no ``mcp`` dependency.

Every tool is a thin layer over ``LibraryApi``. Page numbers follow the
contract 2.5 rule and are never invented: a printed page gives
``\\cite[S.~347]{key}``; without one the passage is labelled "PDF-S. 3" and
the suggestion says that no printed page is known. The 2.5 methods are
guarded with ``getattr``: on an older host ``find_passages`` and
``read_passages`` are simply not listed. ``add_by_doi`` without
``library.write`` is a tool error (``isError: true``), never a crash.

Every ``tools/call`` — answer or tool error — goes to the live session log
(``log``, see ``session_log``) on its way out; the log cannot fail the call.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from lacite import reports, session_log

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "localbib-lacite"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

EXCERPT_CHARS = 400

INSTRUCTIONS = (
    "LocalBib is the user's personal reference library. Use search_library to find "
    "works, find_passages to find text passages that support a claim, read_passages "
    "to read around a passage, get_reference for full metadata. Cite with the "
    "citekey exactly as returned. When a passage has a printed page, use the "
    "suggested \\cite[S.~<page>]{<key>}. When it only has a PDF page, never present "
    "that number as a printed page: cite without a page or tell the user it is the "
    "PDF page. The .bib file in the user's LaTeX project is kept up to date by "
    "LaCite; call sync_bib after add_by_doi if the user wants to cite the new entry now. "
    "When you finish a task in which you looked up or cited sources, call submit_report "
    "once: one claim per statement in the text that needs support, each with the passage "
    "that supports it as a verbatim quote copied from the find_passages/read_passages text, "
    "and the statements you could not support with the queries you tried. LocalBib checks "
    "every quote against the PDF text and sets the page itself; fix the quotes it reports "
    "as not found and submit again with replaces=<report_id>."
)


class ToolError(Exception):
    """A tool failed in a way the agent should read (``isError: true``)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _excerpt(text: Any, n: int = EXCERPT_CHARS) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _int(args: dict, name: str, default: int, lo: int, hi: int) -> int:
    value = args.get(name, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be an integer") from None
    return max(lo, min(hi, value))


def _note(known: dict, citekey: Any, paper_id: Any, has_pdf: Any) -> None:
    """Remember an Item id the tool's own answer already had, for the log."""
    if citekey and paper_id is not None:
        known[str(citekey)] = {"paper_id": paper_id,
                               "has_pdf": bool(has_pdf) if has_pdf is not None else None}


def _str(args: dict, name: str) -> str:
    value = str(args.get(name) or "").strip()
    if not value:
        raise ToolError(f"'{name}' is required")
    return value


# ---------------------------------------------------------------------------
# Page labels and citation suggestions (pure)
# ---------------------------------------------------------------------------

def page_info(passage: dict, citekey: str) -> dict:
    """The page fields an agent gets for one passage: ``page`` (printed, or
    ``None``), ``pdf_page``, ``page_label`` ("S. 347", "S. 347–348",
    "PDF-S. 3" or ``None``), ``cite`` and ``cite_note``."""
    start, end = passage.get("printed_page_start"), passage.get("printed_page_end")
    pdf_start, pdf_end = passage.get("page_start"), passage.get("page_end")
    if start:
        span = start if not end or end == start else f"{start}–{end}"
        tex = start if not end or end == start else f"{start}--{end}"
        return {"page": span, "pdf_page": pdf_start, "page_label": f"S. {span}",
                "cite": f"\\cite[S.~{tex}]{{{citekey}}}", "cite_note": ""}
    if pdf_start:
        span = pdf_start if not pdf_end or pdf_end == pdf_start else f"{pdf_start}–{pdf_end}"
        return {"page": None, "pdf_page": pdf_start, "page_label": f"PDF-S. {span}",
                "cite": f"\\cite{{{citekey}}}",
                "cite_note": (f"Keine gedruckte Seitenzahl sicher bestimmbar; die Stelle steht auf "
                              f"PDF-Seite {span}. Nicht als gedruckte Seite ausgeben – ohne Seite "
                              f"zitieren oder die Seite im PDF prüfen.")}
    return {"page": None, "pdf_page": None, "page_label": None,
            "cite": f"\\cite{{{citekey}}}", "cite_note": "Seite unbekannt."}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _schema(props: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


_QUERY = {"type": "string", "description": "What to look for (words, a claim, a topic)."}
_KEY = {"type": "string", "description": "The citekey exactly as LocalBib returned it."}


_REPORT_SCHEMA = _schema({
    "title": {"type": "string", "description": "What the report covers, e.g. the chapter or section "
                                               "you worked on."},
    "summary": {"type": "string", "description": "One to three sentences: the outcome."},
    "project_folder": {"type": "string", "description": "Absolute path of the LaTeX project folder "
                                                        "you work in."},
    "replaces": {"type": "integer", "description": "report_id of an earlier report this one corrects."},
    "claims": {"type": "array", "minItems": 1, "maxItems": reports.MAX_CLAIMS, "items": {
        "type": "object", "additionalProperties": False, "required": ["claim"],
        "properties": {
            "claim": {"type": "string", "description": "The statement in the text that needs support."},
            "sources": {"type": "array", "maxItems": reports.MAX_SOURCES, "items": {
                "type": "object", "additionalProperties": False, "required": ["citekey", "quote"],
                "properties": {
                    "citekey": _KEY,
                    "quote": {"type": "string", "description": (
                        "The supporting words copied verbatim from the passage text (an ellipsis "
                        "marks a gap). Never paraphrase.")},
                    "pdf_page": {"type": "integer", "description": "The pdf_page of that passage, "
                                                                   "if you have it."},
                    "note": {"type": "string", "description": "How far the source carries the claim."},
                }}},
            "searched": {"type": "array", "items": {"type": "string"},
                         "description": "Queries you tried when no source was found."},
            "note": {"type": "string"},
        }}},
}, ("title", "claims"))


class McpServer:
    """Dispatch over one Add-on's ``LibraryApi``.

    ``sync_all`` is the engine's "sync every connection now" (for
    ``sync_bib`` and after ``add_by_doi``); ``version`` is the Bundle's;
    ``log`` gets one ``session_log`` entry per tool call (the live session)."""

    def __init__(self, library: Any, *, sync_all: Optional[Callable[[], list]] = None,
                 version: str = "0", clock: Callable[[], str] = _now,
                 log: Optional[Callable[[dict], None]] = None,
                 reports: Optional[Any] = None,
                 connections: Optional[Callable[[], list]] = None,
                 lookups: Optional[Callable[[], Optional[int]]] = None) -> None:
        self.library = library
        self.sync_all = sync_all
        self.reports = reports          # ReportStore: submit_report keeps the reports there
        self.connections = connections  # the engine's connections, to name the project
        self.lookups = lookups          # tool calls since the last report (the session log)
        self.version = version
        self._clock = clock
        self.log = log  # SessionLog.record: one entry per tools/call
        self.last_client: Optional[dict] = None

    # --- tool table --------------------------------------------------------
    def _has(self, name: str) -> bool:
        return self.library is not None and callable(getattr(self.library, name, None))

    def tools(self) -> list[dict]:
        out = []
        if self._has("search_references"):
            out.append({
                "name": "search_library",
                "description": ("Search the user's LocalBib library for works (books, articles, theses). "
                                "Returns citekey, title, authors, year and an abstract excerpt."),
                "inputSchema": _schema({"query": _QUERY, "limit": {"type": "integer", "minimum": 1,
                                        "maximum": 50, "default": 10}}, ("query",)),
            })
        if self._has("search_passages"):
            out.append({
                "name": "find_passages",
                "description": ("Find text passages in the PDFs of the user's library that match a query or "
                                "support a claim. Each hit has the full passage text, its page and a ready "
                                "LaTeX citation: \\cite[S.~<printed page>]{key} when the printed page is "
                                "known; otherwise the page is labelled 'PDF-S. n' (a PDF page, NOT the printed "
                                "page) and cite_note explains it."),
                "inputSchema": _schema({"query": _QUERY, "limit": {"type": "integer", "minimum": 1,
                                        "maximum": 20, "default": 5}}, ("query",)),
            })
        if self._has("get_passages"):
            out.append({
                "name": "read_passages",
                "description": ("Read one work's passages in reading order, with pages — e.g. the context "
                                "around a find_passages hit. Falls back to the abstract when the work has no "
                                "extracted text."),
                "inputSchema": _schema({"citekey": _KEY,
                                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                                        "limit": {"type": "integer", "minimum": 1, "maximum": 30,
                                                  "default": 5}}, ("citekey",)),
            })
        if self._has("get_reference"):
            out.append({
                "name": "get_reference",
                "description": "Full metadata of one work by its citekey (title, authors, year, journal, "
                               "DOI, volume/issue/pages, abstract).",
                "inputSchema": _schema({"citekey": _KEY}, ("citekey",)),
            })
        if self._has("create_by_doi"):
            out.append({
                "name": "add_by_doi",
                "description": ("Add a work to the user's library by its DOI (metadata from CrossRef/OpenAlex, "
                                "an open-access PDF when one exists). An existing DOI is returned unchanged. "
                                "Returns the citekey to cite."),
                "inputSchema": _schema({"doi": {"type": "string", "description": "The DOI, e.g. 10.1000/xyz"}},
                                       ("doi",)),
            })
        if self.reports is not None and self._has("get_passages"):
            out.append({
                "name": "submit_report",
                "description": ("Hand in the final report of a writing task: every statement that needs "
                                "support, each with the verbatim quote from the passage that supports it. "
                                "LocalBib checks each quote against the work's text, sets the page from the "
                                "passage it stands in and shows the report to the user for checking. "
                                "Statements without support go in too, with the queries you tried. The "
                                "answer says per source whether the quote was found and gives the citation "
                                "to use."),
                "inputSchema": _REPORT_SCHEMA,
            })
        if self.sync_all is not None:
            out.append({
                "name": "sync_bib",
                "description": ("Update the .bib files LaCite keeps in the user's LaTeX project folders now "
                                "(they also update on their own within about 30 seconds)."),
                "inputSchema": _schema({}),
            })
        return out

    # --- tool bodies -------------------------------------------------------
    def _search_library(self, args: dict, known: dict) -> dict:
        query = _str(args, "query")
        limit = _int(args, "limit", 10, 1, 50)
        hits = self.library.search_references(query, limit)
        for h in hits[:limit]:
            _note(known, h.get("citekey"), h.get("id"), h.get("has_pdf"))
        return {"results": [{
            "citekey": h.get("citekey"), "title": h.get("title") or "",
            "authors": h.get("authors") or "", "year": h.get("year"),
            "abstract_excerpt": _excerpt(h.get("abstract")),
        } for h in hits[:limit]]}

    def _find_passages(self, args: dict, known: dict) -> dict:
        query = _str(args, "query")
        limit = _int(args, "limit", 5, 1, 20)
        results = []
        for p in self.library.search_passages(query, limit):
            key = p.get("citekey") or ""
            results.append({"citekey": key, "title": p.get("title") or "", "year": p.get("year"),
                            "text": p.get("text") or "", **page_info(p, key),
                            "score": p.get("score")})
        return {"results": results}

    def _read_passages(self, args: dict, known: dict) -> dict:
        key = _str(args, "citekey")
        offset = _int(args, "offset", 0, 0, 1_000_000)
        limit = _int(args, "limit", 5, 1, 30)
        found = self.library.get_passages(key, offset, limit)
        if found is None:
            raise ToolError(f"Unknown citekey: {key}")
        out = {"citekey": found.get("citekey") or key, "total": found.get("total", 0),
               "offset": offset, "passages": [
                   {"index": p.get("index"), "text": p.get("text") or "", **page_info(p, key)}
                   for p in found.get("passages") or []]}
        if not out["total"]:
            abstract = self.library.get_abstract(key) if self._has("get_abstract") else None
            out["abstract"] = abstract or ""
            out["note"] = ("Kein Volltext verfügbar – nur der Abstract (ohne Seitenzahl)."
                           if abstract else "Kein Volltext und kein Abstract verfügbar.")
        return out

    def _get_reference(self, args: dict, known: dict) -> dict:
        key = _str(args, "citekey")
        ref = self.library.get_reference(key)
        if ref is None:
            raise ToolError(f"Unknown citekey: {key}")
        _note(known, ref.get("citekey") or key, ref.get("id"), ref.get("has_pdf"))
        keep = ("citekey", "title", "authors", "year", "journal", "doi", "volume", "issue",
                "pages", "abstract", "has_pdf")
        return {**{k: ref.get(k) for k in keep}, "cite": f"\\cite{{{ref.get('citekey') or key}}}"}

    def _add_by_doi(self, args: dict, known: dict) -> dict:
        doi = _str(args, "doi")
        try:
            result = self.library.create_by_doi(doi)
        except PermissionError:
            raise ToolError("LaCite may not add entries to this library (permission library.write "
                            "is not granted).") from None
        except ValueError as exc:
            raise ToolError(f"Could not add DOI {doi}: {exc}") from None
        _note(known, result.get("citekey"), result.get("paper_id"), None)
        if result.get("created") and self.sync_all is not None:
            self.sync_all()
        return {"doi": result.get("doi"), "created": bool(result.get("created")),
                "citekey": result.get("citekey"), "pdf": result.get("pdf"),
                "cite": f"\\cite{{{result.get('citekey')}}}"}

    def _sync_bib(self, args: dict, known: dict) -> dict:
        results = self.sync_all() if self.sync_all is not None else []
        return {"connections": [{
            "name": r.get("name"), "state": r.get("state"), "entries": r.get("entries"),
            "written": bool(r.get("written")), "error": r.get("error"),
        } for r in results]}

    def _submit_report(self, args: dict, known: dict) -> dict:
        lookup = getattr(self.library, "get_reference", None)
        try:
            body = reports.build(args, self.library, page_info=page_info,
                                 lookup=lookup if callable(lookup) else None)
        except reports.ReportError as exc:
            raise ToolError(str(exc)) from None
        project = ""
        if self.connections is not None and args.get("project_folder"):
            try:
                project = reports.project_for(args.get("project_folder"), self.connections())
            except Exception:
                project = ""
        lookups = None
        if self.lookups is not None:
            try:
                lookups = self.lookups()
            except Exception:
                lookups = None
        client = (self.last_client or {}).get("name") or ""
        saved = self.reports.save(body, at=self._clock(), client_name=client, project=project,
                                  lookups=lookups, replaces=reports.positive_int(args.get("replaces")))
        return {**reports.agent_answer(saved), "title": saved["title"]}

    _BODIES = {
        "search_library": "_search_library",
        "find_passages": "_find_passages",
        "read_passages": "_read_passages",
        "get_reference": "_get_reference",
        "add_by_doi": "_add_by_doi",
        "sync_bib": "_sync_bib",
        "submit_report": "_submit_report",
    }

    def call_tool(self, name: str, args: Any) -> dict:
        """A ``tools/call`` result. A tool failure is ``isError: true`` with
        the reason as text; an unknown tool is a JSON-RPC error (raised)."""
        if name not in {t["name"] for t in self.tools()}:
            raise LookupError(name)
        if args is None:
            args = {}
        started = time.perf_counter()
        known: dict = {}
        if not isinstance(args, dict):
            return self._logged(name, args, None, "'arguments' must be an object", started, known)
        try:
            data = getattr(self, self._BODIES[name])(args, known)
        except ToolError as exc:
            return self._logged(name, args, None, str(exc), started, known)
        except Exception as exc:  # the library failed: tell the agent, keep serving
            return self._logged(name, args, None, f"{name} failed: {exc}", started, known)
        return self._logged(name, args, data, None, started, known)

    def _logged(self, name: str, args: Any, data: Optional[dict], error: Optional[str],
                started: float, known: dict) -> dict:
        """The call's answer, after handing it to the session log. The log
        never raises into the call and only queues (``session_log``)."""
        if self.log is not None:
            try:
                self.log(session_log.summarize(
                    name, args, data, error=error, client=self.last_client, at=self._clock(),
                    duration_ms=round((time.perf_counter() - started) * 1000), known=known))
            except Exception:
                pass
        if error is not None:
            return _tool_error(error)
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                "structuredContent": data, "isError": False}

    # --- JSON-RPC ------------------------------------------------------------
    def handle(self, message: Any) -> Optional[dict]:
        """One JSON-RPC message -> its response, ``None`` for a notification."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" \
                or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "method" not in message and ("result" in message
                                                                         or "error" in message):
                return None  # a response from the client: nothing to answer
            return _error(message.get("id") if isinstance(message, dict) else None,
                          INVALID_REQUEST, "Invalid Request")
        method = message["method"]
        is_notification = "id" not in message
        msg_id = message.get("id")
        params = message.get("params") or {}
        if is_notification:
            return None
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")
        try:
            if method == "initialize":
                return _result(msg_id, self._initialize(params))
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, {"tools": self.tools()})
            if method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str):
                    return _error(msg_id, INVALID_PARAMS, "tools/call needs a tool name")
                try:
                    return _result(msg_id, self.call_tool(name, params.get("arguments")))
                except LookupError:
                    return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
            return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        except Exception as exc:
            return _error(msg_id, INTERNAL_ERROR, str(exc))

    def handle_payload(self, payload: Any) -> Any:
        """A message or a batch (list) -> the response(s); ``None`` when
        nothing needs an answer (only notifications)."""
        if isinstance(payload, list):
            if not payload:
                return _error(None, INVALID_REQUEST, "Empty batch")
            answers = [a for a in (self.handle(m) for m in payload) if a is not None]
            return answers or None
        return self.handle(payload)

    def _initialize(self, params: dict) -> dict:
        info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        self.last_client = {"name": str(info.get("name") or "MCP-Client"),
                            "version": str(info.get("version") or ""), "at": self._clock()}
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "LocalBib (LaCite)", "version": self.version},
                "instructions": INSTRUCTIONS}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}
