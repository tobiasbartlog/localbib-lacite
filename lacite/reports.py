"""The Schlussbericht: the claims an agent wants supported and their passages.

At the end of a writing task the agent calls the MCP tool ``submit_report``
(``mcp.py``) with a title, an optional summary and one entry per claim — the
statement in the user's text that needs support — with its sources: citekey
plus the **verbatim** quote from the passage it read. Nothing the agent says
about a source is trusted:

* :func:`verify_source` looks for the quote in the Item's own passages
  (``LibraryApi.get_passages``) and takes the page from the passage where it
  stands — printed page when the core knows it, else "PDF-S. n" (the
  contract 2.5 rule, ``mcp.page_info``). The agent's ``pdf_page`` only picks
  between several occurrences.
* A source whose quote is not in the text is ``not_found``; an Item without
  extracted text is ``no_text``; an unknown citekey ``unknown_key``.
* A claim is ``supported`` when one source is ``verified``, ``check`` when it
  has sources but none verified, ``unsupported`` without sources.

The matching (:func:`normalize`, :func:`find_quote`) ignores case, white
space, quote and dash styles, soft hyphens and line-break hyphenation; an
ellipsis in the quote ("…", "...", "[…]") is a gap, the parts must appear in
order within :data:`GAP_CHARS`. A quote shorter than :data:`MIN_QUOTE_CHARS`
(normalized) proves nothing and stays unverified.

:func:`build` turns the tool arguments into the stored report,
:func:`to_markdown` is "Als Markdown speichern", :func:`search` the search over
every stored report. Pure: the library is passed in, nothing is written here
(``report_store.py`` keeps the reports).
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Any, Callable, Optional

MAX_CLAIMS = 100
MAX_SOURCES = 10
MAX_SEARCHED = 10
TITLE_CHARS = 300
SUMMARY_CHARS = 3000
CLAIM_CHARS = 2000
QUOTE_CHARS = 2000
NOTE_CHARS = 1000
PASSAGE_CHARS = 600       # passage text kept per verified source (frames the PDF)
MIN_QUOTE_CHARS = 20      # normalized; shorter quotes prove nothing
GAP_CHARS = 1500          # the most text an ellipsis may skip
PAGE_BATCH = 100          # passages read per get_passages call
MAX_PASSAGES = 5000       # passages read per Item at most

SUPPORTED, CHECK, UNSUPPORTED = "supported", "check", "unsupported"
VERIFIED, NOT_FOUND, NO_TEXT, UNKNOWN_KEY, NO_QUOTE = (
    "verified", "not_found", "no_text", "unknown_key", "no_quote")


class ReportError(ValueError):
    """The arguments are no report; the message goes to the agent."""


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_QUOTES = dict.fromkeys(map(ord, "\"'„“”‚‘’«»‹›`´"), None)
_DASHES = {ord(c): "-" for c in "‐‑‒–—―−"}
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|\(\s*(?:…|\.\.\.)\s*\)|…|\.\.\.)\s*")
_HYPHEN_BREAK = re.compile(r"(\w)- (\w)")


def normalize(text: Any) -> str:
    """The form both sides are compared in."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("­", "").translate(_QUOTES).translate(_DASHES)
    text = " ".join(text.split()).lower()
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def quote_parts(quote: str) -> list[str]:
    """The quote's parts between ellipses, normalized, empty ones dropped."""
    return [p for p in (normalize(part) for part in _ELLIPSIS.split(str(quote or ""))) if p]


def find_quote(parts: list[str], text: str) -> list[int]:
    """Every start in ``text`` (normalized) where ``parts`` appear in order,
    each gap at most :data:`GAP_CHARS`."""
    if not parts:
        return []
    starts, at = [], 0
    while True:
        start = text.find(parts[0], at)
        if start < 0:
            return starts
        pos, ok = start + len(parts[0]), True
        for part in parts[1:]:
            # Bounded: an unbounded find re-scans the whole work per start
            # of a common first part (quadratic on a long book).
            nxt = text.find(part, pos, pos + GAP_CHARS + len(part))
            if nxt < 0 or nxt - pos > GAP_CHARS:
                ok = False
                break
            pos = nxt + len(part)
        if ok:
            starts.append(start)
        at = start + 1


# ---------------------------------------------------------------------------
# One source
# ---------------------------------------------------------------------------

def read_all_passages(library: Any, citekey: str) -> Optional[list[dict]]:
    """The Item's passages in reading order, ``None`` for an unknown key."""
    get = getattr(library, "get_passages", None)
    if not callable(get):
        return []
    out: list[dict] = []
    offset = 0
    while offset < MAX_PASSAGES:
        found = get(citekey, offset, PAGE_BATCH)
        if found is None:
            return None if offset == 0 else out
        batch = found.get("passages") or []
        out.extend(batch)
        total = int(found.get("total") or 0)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return out


def _stream(passages: list[dict]) -> tuple[str, list[int]]:
    """All passages as one normalized text plus each passage's start in it
    (a quote may run over a chunk border)."""
    texts, starts, pos = [], [], 0
    for p in passages:
        t = normalize(p.get("text"))
        starts.append(pos)
        texts.append(t)
        pos += len(t) + 1
    return " ".join(texts), starts


def _passage_at(starts: list[int], offset: int) -> int:
    index = 0
    for i, s in enumerate(starts):
        if s <= offset:
            index = i
        else:
            break
    return index


def _on_page(passage: dict, pdf_page: int) -> bool:
    """Whether ``passage`` covers PDF page ``pdf_page`` (it may span pages)."""
    start = passage.get("page_start")
    end = passage.get("page_end") or start
    try:
        return start is not None and int(start) <= pdf_page <= int(end)
    except (TypeError, ValueError):
        return False


def verify_source(library: Any, citekey: str, quote: str, *, pdf_page: Optional[int] = None,
                  page_info: Callable[[dict, str], dict],
                  cache: Optional[dict] = None) -> dict:
    """The checked fields of one source: ``state`` plus, when ``verified``,
    the page fields from the passage the quote stands in and that passage's
    text (``passage``, frames the PDF). ``cache`` holds read passages per key
    within one report."""
    out: dict = {"state": NO_QUOTE, "page": None, "pdf_page": None, "page_label": None,
                 "cite": f"\\cite{{{citekey}}}", "passage": "", "occurrences": 0}
    passages = (cache.get(citekey) if cache is not None and citekey in cache
                else read_all_passages(library, citekey))
    if cache is not None:
        cache[citekey] = passages
    if passages is None:
        return {**out, "state": UNKNOWN_KEY}
    parts = quote_parts(quote)
    if not parts:
        return out
    if not passages:
        return {**out, "state": NO_TEXT}
    if sum(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return {**out, "state": NOT_FOUND, "reason": "too_short"}
    text, starts = _stream(passages)
    hits = find_quote(parts, text)
    if not hits:
        return {**out, "state": NOT_FOUND}
    indexes = [_passage_at(starts, h) for h in hits]
    chosen = indexes[0]
    if pdf_page is not None:
        on_page = [i for i in indexes if _on_page(passages[i], pdf_page)]
        chosen = on_page[0] if on_page else chosen
    passage = passages[chosen]
    info = page_info(passage, citekey)
    raw = " ".join(str(passage.get("text") or "").split())
    return {**out, "state": VERIFIED, "page": info.get("page"), "pdf_page": info.get("pdf_page"),
            "page_label": info.get("page_label"), "cite": info.get("cite") or out["cite"],
            "passage": raw[:PASSAGE_CHARS], "occurrences": len(hits)}


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------

def _text(value: Any, n: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + "…"


def positive_int(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def claim_status(sources: list[dict]) -> str:
    if not sources:
        return UNSUPPORTED
    return SUPPORTED if any(s.get("state") == VERIFIED for s in sources) else CHECK


def counts(claims: list[dict]) -> dict:
    out = {SUPPORTED: 0, CHECK: 0, UNSUPPORTED: 0}
    for c in claims:
        out[c.get("status")] = out.get(c.get("status"), 0) + 1
    out["total"] = len(claims)
    return out


def build(args: dict, library: Any, *, page_info: Callable[[dict, str], dict],
          lookup: Optional[Callable[[str], Optional[dict]]] = None) -> dict:
    """The stored report body from ``submit_report``'s arguments: every quote
    checked, every page taken from the library. Raises :class:`ReportError`.

    ``lookup(citekey)`` (``LibraryApi.get_reference``) adds title, year and
    the Item id per source when known."""
    if not isinstance(args, dict):
        raise ReportError("arguments must be an object")
    title = _text(args.get("title"), TITLE_CHARS)
    if not title:
        raise ReportError("'title' is required: what the report covers, e.g. the chapter")
    raw_claims = args.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ReportError("'claims' must be a non-empty list")
    if len(raw_claims) > MAX_CLAIMS:
        raise ReportError(f"at most {MAX_CLAIMS} claims per report")
    cache: dict = {}
    refs: dict = {}
    claims = []
    for n, raw in enumerate(raw_claims, 1):
        if not isinstance(raw, dict) or not str(raw.get("claim") or "").strip():
            raise ReportError(f"claim {n}: 'claim' (the statement to support) is required")
        sources = []
        for raw_src in (raw.get("sources") or [])[:MAX_SOURCES]:
            if not isinstance(raw_src, dict):
                continue
            key = str(raw_src.get("citekey") or "").strip()
            if not key:
                continue
            quote = _text(raw_src.get("quote"), QUOTE_CHARS)
            hint = positive_int(raw_src.get("pdf_page"))
            checked = verify_source(library, key, quote, pdf_page=hint, page_info=page_info, cache=cache)
            if lookup is not None and key not in refs:
                try:
                    refs[key] = lookup(key) or {}
                except Exception:
                    refs[key] = {}
            ref = refs.get(key) or {}
            sources.append({"citekey": key, "quote": quote, "note": _text(raw_src.get("note"), NOTE_CHARS),
                            "pdf_page_hint": hint, **checked,
                            "title": ref.get("title") or "", "year": ref.get("year"),
                            "paper_id": ref.get("id"), "has_pdf": ref.get("has_pdf")})
        searched = [_text(q, 200) for q in (raw.get("searched") or [])[:MAX_SEARCHED]
                    if str(q or "").strip()] if isinstance(raw.get("searched"), list) else []
        claims.append({"n": n, "claim": _text(raw.get("claim"), CLAIM_CHARS),
                       "note": _text(raw.get("note"), NOTE_CHARS), "sources": sources,
                       "searched": searched, "status": claim_status(sources)})
    return {"title": title, "summary": _text(args.get("summary"), SUMMARY_CHARS),
            "claims": claims, "counts": counts(claims)}


_STATE_TEXT = {
    VERIFIED: "verified verbatim in the text",
    NOT_FOUND: "QUOTE NOT FOUND in the work's text",
    NO_TEXT: "the work has no extracted text: quote not checked",
    UNKNOWN_KEY: "UNKNOWN citekey",
    NO_QUOTE: "no quote given: not checked",
}


def agent_answer(report: dict) -> dict:
    """What the agent reads back: per claim the status, per source the state
    and the citation to use; ``message`` says what to fix."""
    problems = sum(1 for c in report["claims"] for s in c["sources"] if s["state"] != VERIFIED)
    message = ("Report saved; every quote was found in its source." if not problems else
               f"Report saved. {problems} source(s) could not be verified (see 'state'). Copy quotes "
               "verbatim from find_passages/read_passages text, then call submit_report again with "
               f"replaces={report.get('id')} to update this report.")
    return {"report_id": report.get("id"), "counts": report["counts"], "message": message,
            "claims": [{"n": c["n"], "status": c["status"], "sources": [
                {"citekey": s["citekey"], "state": s["state"], "explanation": _STATE_TEXT[s["state"]],
                 "page_label": s["page_label"], "cite": s["cite"]} for s in c["sources"]]}
                for c in report["claims"]]}


# ---------------------------------------------------------------------------
# Reading: Markdown and search
# ---------------------------------------------------------------------------

_STATUS_DE = {SUPPORTED: "Belegt", CHECK: "Prüfen", UNSUPPORTED: "Ohne Beleg"}
_STATE_DE = {
    VERIFIED: "wörtlich im Text",
    NOT_FOUND: "Zitat nicht im Text gefunden",
    NO_TEXT: "kein Volltext, nicht geprüft",
    UNKNOWN_KEY: "Schlüssel nicht in der Bibliothek",
    NO_QUOTE: "ohne Zitat, nicht geprüft",
}


def source_page(src: dict) -> Optional[str]:
    """The page a reader sees: the checked one, else the agent's hint as a
    PDF page (never a printed page the library did not confirm)."""
    if src.get("page_label"):
        return src["page_label"]
    hint = src.get("pdf_page_hint")
    return f"PDF-S. {hint}" if hint else None


def to_markdown(report: dict) -> str:
    """"Als Markdown speichern": the report as one Markdown document."""
    meta = [x for x in (report.get("client_name"), (report.get("created_at") or "").replace("T", " ")[:16],
                        report.get("project")) if x]
    lines = [f"# {report.get('title') or 'Schlussbericht'}", ""]
    if meta:
        lines += ["_" + " · ".join(meta) + "_", ""]
    c = report.get("counts") or {}
    lines += [f"{c.get(SUPPORTED, 0)} belegt · {c.get(CHECK, 0)} prüfen · "
              f"{c.get(UNSUPPORTED, 0)} ohne Beleg", ""]
    if report.get("summary"):
        lines += [report["summary"], ""]
    for claim in report.get("claims") or []:
        lines += [f"## {claim['n']}. {claim['claim']}", "", f"**{_STATUS_DE.get(claim['status'], '')}**", ""]
        for src in claim.get("sources") or []:
            page = source_page(src)
            head = src["citekey"] + (f", {page}" if page else "")
            if src.get("quote"):
                lines.append(f"> „{src['quote']}“")
                lines.append(">")
            lines.append(f"> — {head} ({_STATE_DE.get(src['state'], '')})")
            lines += ["", f"`{src['cite']}`", ""]
            if src.get("note"):
                lines += [f"Agent: {src['note']}", ""]
        if claim.get("note"):
            lines += [f"Agent: {claim['note']}", ""]
        if not claim.get("sources"):
            lines += ["Keine Stelle in der Bibliothek gefunden."]
            if claim.get("searched"):
                lines.append("Gesucht: " + ", ".join(f"„{q}“" for q in claim["searched"]))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def search(reports: list[dict], query: str, limit: int = 100) -> list[dict]:
    """Claims across ``reports`` (newest first as given) whose statement,
    note, quotes, citekeys or searched terms contain every word of ``query``:
    ``[{report_id, title, created_at, claim}]``."""
    words = normalize(query).split()
    if not words:
        return []
    out: list[dict] = []
    for report in reports:
        for claim in report.get("claims") or []:
            hay = normalize(" ".join([claim.get("claim") or "", claim.get("note") or "",
                                      " ".join(claim.get("searched") or []),
                                      *[f"{s.get('citekey')} {s.get('quote')} {s.get('note')}"
                                        for s in claim.get("sources") or []]]))
            if all(w in hay for w in words):
                out.append({"report_id": report.get("id"), "title": report.get("title"),
                            "created_at": report.get("created_at"), "claim": claim})
                if len(out) >= limit:
                    return out
    return out


def project_for(folder: Any, connections: list[dict]) -> str:
    """The name of the connection whose folder is ``folder`` (or contains it,
    or lies inside it), else the folder's own name; ``""`` without a folder."""
    raw = str(folder or "").strip()
    if not raw:
        return ""

    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    given = norm(raw)
    best = ""
    for conn in connections or []:
        own = norm(str(conn.get("folder") or ""))
        if not own:
            continue
        if given == own:
            return str(conn.get("name") or "")
        inside = given.startswith(own + os.sep) or own.startswith(given + os.sep)
        if inside and not best:
            best = str(conn.get("name") or "")
    return best or os.path.basename(os.path.normpath(raw))
