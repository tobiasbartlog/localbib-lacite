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
  extracted text is ``no_text``.
* A work the agent relied on that is not in the library (no citekey, or one
  the library does not know) is ``missing``: the agent describes it with
  ``doi``/``title``/``authors``/``year`` instead, the report lists it under
  "Nicht in der Bibliothek" (:func:`missing_sources`) and the user adds it by
  DOI; :func:`recheck` then verifies its quote. ``unknown_key`` is the state
  older reports stored for the same case.
* A claim is ``supported`` when one source is ``verified``, ``missing`` when
  none is and one is ``missing``, ``check`` when it has sources but none
  verified, ``unsupported`` without sources.

A report has a ``kind``: ``research`` answers one request of the user ("what
does my library say about X?") — ``question`` keeps the request, each claim
is a finding, and ``ratings`` say per work how much it matters for the
question (``central``/``supporting``/``marginal``) and why: the agent's
judgement, shown as such. ``check`` is the chapter check, one claim per
statement in the user's text (older reports have no kind and are this one).
Both are verified the same way. :func:`works_of` groups the findings by work
(the "nach Quelle" view). ``searches`` — what the agent looked up — never
comes from the agent: ``mcp.py`` takes it from the session log
(``session_log.searches``).

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
AUTHORS_CHARS = 500
DOI_CHARS = 200
QUESTION_CHARS = 2000
WHY_CHARS = 1000
MAX_RATINGS = 50
MAX_SEARCHES = 200        # search log entries kept per report (a replaced report adds up)
PASSAGE_CHARS = 600       # passage text kept per verified source (frames the PDF)
MIN_QUOTE_CHARS = 20      # normalized; shorter quotes prove nothing
GAP_CHARS = 1500          # the most text an ellipsis may skip
PAGE_BATCH = 100          # passages read per get_passages call
MAX_PASSAGES = 5000       # passages read per Item at most

SUPPORTED, CHECK, UNSUPPORTED, SOURCE_MISSING = "supported", "check", "unsupported", "missing"
VERIFIED, NOT_FOUND, NO_TEXT, UNKNOWN_KEY, NO_QUOTE, MISSING = (
    "verified", "not_found", "no_text", "unknown_key", "no_quote", "missing")
RESEARCH, CHECK_KIND = "research", "check"
CENTRAL, SUPPORTING, MARGINAL = "central", "supporting", "marginal"
RELEVANCE = (CENTRAL, SUPPORTING, MARGINAL)


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


_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def normalize_doi(value: Any) -> str:
    """``10.xxx/yyy`` in lower case, without a resolver prefix; ``""`` when
    ``value`` is no DOI."""
    doi = _DOI_PREFIX.sub("", str(value or "").strip()).strip().lower()
    return doi[:DOI_CHARS] if doi.startswith("10.") and "/" in doi else ""


def _people(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(a).strip() for a in value if str(a or "").strip())
    return _text(value, AUTHORS_CHARS)


def check_source(raw: dict, library: Any, *, page_info: Callable[[dict, str], dict],
                 lookup: Optional[Callable[[str], Optional[dict]]] = None,
                 cache: Optional[dict] = None, refs: Optional[dict] = None) -> Optional[dict]:
    """One stored source from the agent's fields (or a stored source, for
    :func:`recheck`): the quote checked when the work is in the library,
    else ``missing`` with what the agent said about the work. ``None`` when
    the source names no work at all (no citekey, DOI or title)."""
    key = str(raw.get("citekey") or "").strip()
    doi = normalize_doi(raw.get("doi"))
    given_title = _text(raw.get("title"), TITLE_CHARS)
    if not (key or doi or given_title):
        return None
    quote = _text(raw.get("quote"), QUOTE_CHARS)
    hint = positive_int(raw.get("pdf_page")) or positive_int(raw.get("pdf_page_hint"))
    if key:
        checked = verify_source(library, key, quote, pdf_page=hint, page_info=page_info, cache=cache)
    else:
        checked = {"state": UNKNOWN_KEY, "page": None, "pdf_page": None, "page_label": None,
                   "passage": "", "occurrences": 0}
    ref: dict = {}
    if checked["state"] == UNKNOWN_KEY:
        checked = {**checked, "state": MISSING, "cite": ""}
    elif lookup is not None:
        refs = {} if refs is None else refs
        if key not in refs:
            try:
                refs[key] = lookup(key) or {}
            except Exception:
                refs[key] = {}
        ref = refs.get(key) or {}
    return {"citekey": key, "doi": normalize_doi(ref.get("doi")) or doi, "quote": quote,
            "note": _text(raw.get("note"), NOTE_CHARS), "pdf_page_hint": hint, **checked,
            "title": ref.get("title") or given_title, "year": ref.get("year") or positive_int(raw.get("year")),
            "authors": _people(ref.get("authors")) or _people(raw.get("authors")),
            "paper_id": ref.get("id"), "has_pdf": ref.get("has_pdf")}


def claim_status(sources: list[dict]) -> str:
    if not sources:
        return UNSUPPORTED
    if any(s.get("state") == VERIFIED for s in sources):
        return SUPPORTED
    if any(s.get("state") in (MISSING, UNKNOWN_KEY) for s in sources):
        return SOURCE_MISSING
    return CHECK


def counts(claims: list[dict]) -> dict:
    """Claims per status, ``total``, and ``verified``: the sources whose quote
    was found ("6 Stellen wörtlich bestätigt")."""
    out = {SUPPORTED: 0, CHECK: 0, SOURCE_MISSING: 0, UNSUPPORTED: 0}
    for c in claims:
        out[c.get("status")] = out.get(c.get("status"), 0) + 1
    out["total"] = len(claims)
    out["verified"] = sum(1 for c in claims for s in c.get("sources") or [] if s.get("state") == VERIFIED)
    return out


def kind_of(value: Any, question: str = "") -> str:
    """``research`` or ``check``; without a valid kind a report with a
    question is research, any other (older reports too) a check."""
    kind = str(value or "").strip().lower()
    if kind in (RESEARCH, CHECK_KIND):
        return kind
    return RESEARCH if question else CHECK_KIND


def parse_ratings(raw: Any) -> dict:
    """The agent's ``works`` list -> ``{citekey: {relevance, why}}``. A
    relevance outside :data:`RELEVANCE` is dropped (the "why" stays); entries
    without a citekey are dropped."""
    out: dict = {}
    if not isinstance(raw, list):
        return out
    for item in raw[:MAX_RATINGS]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("citekey") or "").strip()
        if not key or key in out:
            continue
        relevance = str(item.get("relevance") or "").strip().lower()
        out[key] = {"relevance": relevance if relevance in RELEVANCE else "",
                    "why": _text(item.get("why"), WHY_CHARS)}
    return out


_RANK = {CENTRAL: 0, SUPPORTING: 1, MARGINAL: 2}


def works_of(claims: list[dict], ratings: Optional[dict] = None) -> list[dict]:
    """The works in the library the findings stand on, once each, most
    relevant first (then in the order they first appear): ``[{citekey, title,
    authors, year, paper_id, has_pdf, relevance, why, findings: [{n, src}],
    verified}]``. ``src`` is the source's index in claim ``n``. A work not in
    the library is not here (see :func:`missing_sources`)."""
    ratings = ratings or {}
    out: list[dict] = []
    seen: dict = {}
    for claim in claims:
        for i, src in enumerate(claim.get("sources") or []):
            key = src.get("citekey")
            if not key or src.get("state") in (MISSING, UNKNOWN_KEY):
                continue
            work = seen.get(key)
            if work is None:
                rating = ratings.get(key) or {}
                work = seen[key] = {"citekey": key, "title": "", "authors": "", "year": None,
                                    "paper_id": None, "has_pdf": None,
                                    "relevance": rating.get("relevance") or "", "why": rating.get("why") or "",
                                    "findings": [], "verified": 0}
                out.append(work)
            for field in ("title", "authors", "year", "paper_id", "has_pdf"):
                if work[field] in (None, "") and src.get(field) not in (None, ""):
                    work[field] = src[field]
            work["findings"].append({"n": claim.get("n"), "src": i})
            if src.get("state") == VERIFIED:
                work["verified"] += 1
    order = {id(w): i for i, w in enumerate(out)}
    return sorted(out, key=lambda w: (_RANK.get(w["relevance"], 3), order[id(w)]))


def _identity(src: dict) -> str:
    return (normalize_doi(src.get("doi")) or str(src.get("citekey") or "").strip().lower()
            or normalize(src.get("title")))


def missing_sources(claims: list[dict]) -> list[dict]:
    """The works the report relies on that are not in the library, once each
    (same DOI, else citekey, else title), in the order they first appear:
    ``[{doi, citekey, title, authors, year, claims: [n, ...]}]``."""
    out: list[dict] = []
    seen: dict = {}
    for claim in claims:
        for src in claim.get("sources") or []:
            if src.get("state") not in (MISSING, UNKNOWN_KEY):
                continue
            ident = _identity(src)
            entry = seen.get(ident)
            if entry is None:
                entry = seen[ident] = {"doi": "", "citekey": "", "title": "", "authors": "",
                                       "year": None, "claims": []}
                out.append(entry)
            for field in ("doi", "citekey", "title", "authors", "year"):
                if not entry[field] and src.get(field):
                    entry[field] = normalize_doi(src[field]) if field == "doi" else src[field]
            if claim.get("n") not in entry["claims"]:
                entry["claims"].append(claim.get("n"))
    return out


def _body(title: str, summary: str, claims: list[dict], *, kind: str = CHECK_KIND,
          question: str = "", ratings: Optional[dict] = None,
          searches: Optional[list] = None) -> dict:
    ratings = ratings or {}
    missing = missing_sources(claims)
    works = works_of(claims, ratings)
    tally = counts(claims)
    tally["works"] = len(works)
    tally["missing_works"] = len(missing)
    return {"kind": kind, "title": title, "question": question, "summary": summary,
            "claims": claims, "counts": tally, "missing": missing, "ratings": ratings,
            "works": works, "searches": list(searches or [])[-MAX_SEARCHES:]}


def build(args: dict, library: Any, *, page_info: Callable[[dict, str], dict],
          lookup: Optional[Callable[[str], Optional[dict]]] = None,
          searches: Optional[list] = None) -> dict:
    """The stored report body from ``submit_report``'s arguments: every quote
    checked, every page taken from the library. Raises :class:`ReportError`.

    ``lookup(citekey)`` (``LibraryApi.get_reference``) adds title, year and
    the Item id per source when known; ``searches`` is what the session log
    recorded (``session_log.searches``), never the agent's word."""
    if not isinstance(args, dict):
        raise ReportError("arguments must be an object")
    title = _text(args.get("title"), TITLE_CHARS)
    if not title:
        raise ReportError("'title' is required: a short headline, e.g. the question or the chapter")
    question = _text(args.get("question"), QUESTION_CHARS)
    kind = kind_of(args.get("kind"), question)
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
            src = check_source(raw_src, library, page_info=page_info, lookup=lookup, cache=cache, refs=refs)
            if src is not None:
                sources.append(src)
        searched = [_text(q, 200) for q in (raw.get("searched") or [])[:MAX_SEARCHED]
                    if str(q or "").strip()] if isinstance(raw.get("searched"), list) else []
        claims.append({"n": n, "claim": _text(raw.get("claim"), CLAIM_CHARS),
                       "note": _text(raw.get("note"), NOTE_CHARS), "sources": sources,
                       "searched": searched, "status": claim_status(sources)})
    return _body(title, _text(args.get("summary"), SUMMARY_CHARS), claims, kind=kind,
                 question=question, ratings=parse_ratings(args.get("works")), searches=searches)


def find_added(library: Any, src: dict) -> str:
    """The citekey of a work the report lists as missing that is in the
    library now: the same DOI, else the same title (after :func:`normalize`).
    Only an exact match counts — never a guess. ``""`` when there is none or
    the host cannot search."""
    search = getattr(library, "search_references", None)
    doi = normalize_doi(src.get("doi"))
    title = normalize(src.get("title"))
    if not callable(search) or not (doi or title):
        return ""
    for query, exact in ((doi, lambda h: normalize_doi(h.get("doi")) == doi),
                         (src.get("title") or "", lambda h: title and normalize(h.get("title")) == title)):
        if not query:
            continue
        try:
            hits = search(str(query), 10) or []
        except Exception:
            continue
        for hit in hits:
            if isinstance(hit, dict) and hit.get("citekey") and exact(hit):
                return str(hit["citekey"])
    return ""


def recheck(report: dict, library: Any, *, page_info: Callable[[dict, str], dict],
            lookup: Optional[Callable[[str], Optional[dict]]] = None,
            added: Optional[dict] = None) -> dict:
    """The report body checked again against the library as it is now, e.g.
    after the user added a missing work. ``added`` maps a DOI to the citekey
    it got: a missing source with that DOI cites that key from now on; any
    other missing source is looked up by DOI or title (:func:`find_added`)."""
    keys = {normalize_doi(d): k for d, k in (added or {}).items() if normalize_doi(d) and k}
    cache: dict = {}
    refs: dict = {}
    claims = []
    for claim in report.get("claims") or []:
        sources = []
        for old in claim.get("sources") or []:
            raw = dict(old)
            doi = normalize_doi(old.get("doi"))
            if old.get("state") in (MISSING, UNKNOWN_KEY):
                if doi in keys:
                    raw["citekey"] = keys[doi]
                elif not str(old.get("citekey") or "").strip() or old.get("state") == UNKNOWN_KEY:
                    raw["citekey"] = find_added(library, old) or raw.get("citekey") or ""
            src = check_source(raw, library, page_info=page_info, lookup=lookup, cache=cache, refs=refs)
            sources.append(src if src is not None else old)
        claims.append({**claim, "sources": sources, "status": claim_status(sources)})
    question = report.get("question") or ""
    return _body(report.get("title") or "", report.get("summary") or "", claims,
                 kind=kind_of(report.get("kind"), question), question=question,
                 ratings=report.get("ratings") or {}, searches=report.get("searches") or [])


def complete(report: dict) -> dict:
    """A stored report with every field the page reads, older ones too:
    ``kind`` (none = check), ``question``, ``works``, ``searches``, ``ratings``
    and the newer counts. Reads only; nothing is re-verified."""
    claims = report.get("claims") or []
    question = report.get("question") or ""
    ratings = report.get("ratings") or {}
    out = {**report, "kind": kind_of(report.get("kind"), question), "question": question,
           "ratings": ratings, "searches": report.get("searches") or [],
           "missing": report.get("missing") or missing_sources(claims)}
    if "works" not in report:
        out["works"] = works_of(claims, ratings)
    tally = {**counts(claims), **(report.get("counts") or {})}
    tally.setdefault("works", len(out["works"]))
    tally.setdefault("missing_works", len(out["missing"]))
    out["counts"] = tally
    return out


_STATE_TEXT = {
    VERIFIED: "verified verbatim in the text",
    NOT_FOUND: "QUOTE NOT FOUND in the work's text",
    NO_TEXT: "the work has no extracted text: quote not checked",
    UNKNOWN_KEY: "UNKNOWN citekey",
    NO_QUOTE: "no quote given: not checked",
    MISSING: "NOT IN THE USER'S LIBRARY: listed in the report for the user to add",
}


def agent_answer(report: dict) -> dict:
    """What the agent reads back: per claim the status, per source the state
    and the citation to use; ``message`` says what to fix."""
    states = [s["state"] for c in report["claims"] for s in c["sources"]]
    missing = sum(1 for s in states if s in (MISSING, UNKNOWN_KEY))
    problems = sum(1 for s in states if s != VERIFIED) - missing
    saved = f"Report Nr. {report.get('id')} saved; the user reads it in LocalBib under LaCite → Berichte."
    if not problems and not missing:
        message = saved + " Every quote was found in its source."
    else:
        message = saved
        if problems:
            message += (f" {problems} source(s) could not be verified (see 'state'). Copy quotes "
                        "verbatim from find_passages/read_passages text, then call submit_report again "
                        f"with replaces={report.get('id')} to update this report.")
        if missing:
            message += (f" {missing} source(s) are not in the user's library. When you know a work's "
                        "DOI, add it with add_by_doi, then submit again with "
                        f"replaces={report.get('id')}, citing the returned citekey with a verbatim "
                        "quote; otherwise the report lists the work for the user to add.")
    return {"report_id": report.get("id"), "counts": report["counts"], "message": message,
            "claims": [{"n": c["n"], "status": c["status"], "sources": [_answer_source(s) for s in c["sources"]]}
                       for c in report["claims"]]}


def _answer_source(src: dict) -> dict:
    out = {"citekey": src["citekey"], "state": src["state"], "explanation": _STATE_TEXT[src["state"]],
           "page_label": src["page_label"], "cite": src["cite"]}
    if src["state"] in (MISSING, UNKNOWN_KEY):
        out.update({k: src[k] for k in ("doi", "title") if src.get(k)})
    return out


# ---------------------------------------------------------------------------
# Reading: Markdown and search
# ---------------------------------------------------------------------------

_STATUS_DE = {SUPPORTED: "Belegt", CHECK: "Prüfen", SOURCE_MISSING: "Quelle fehlt", UNSUPPORTED: "Ohne Beleg"}
_STATE_DE = {
    VERIFIED: "wörtlich im Text",
    NOT_FOUND: "Zitat nicht im Text gefunden",
    NO_TEXT: "kein Volltext, nicht geprüft",
    UNKNOWN_KEY: "Schlüssel nicht in der Bibliothek",
    NO_QUOTE: "ohne Zitat, nicht geprüft",
    MISSING: "nicht in der Bibliothek",
}


def source_name(src: dict) -> str:
    """How a source is named: its citekey, else the title the agent gave,
    else its DOI."""
    return src.get("citekey") or src.get("title") or (f"doi:{src['doi']}" if src.get("doi") else "")


def reference_line(src: dict) -> str:
    """The line naming a work not in the library: "Autoren (Jahr): Titel. doi:…"."""
    head = src.get("authors") or ""
    if src.get("year"):
        head = f"{head} ({src['year']})".strip()
    line = ": ".join(p for p in (head, src.get("title") or "") if p)
    if src.get("doi"):
        line = (line + ". " if line else "") + f"doi:{src['doi']}"
    return line


def source_page(src: dict) -> Optional[str]:
    """The page a reader sees: the checked one, else the agent's hint as a
    PDF page (never a printed page the library did not confirm)."""
    if src.get("page_label"):
        return src["page_label"]
    hint = src.get("pdf_page_hint")
    return f"PDF-S. {hint}" if hint else None


RELEVANCE_DE = {CENTRAL: "Zentral", SUPPORTING: "Ergänzend", MARGINAL: "Am Rand"}
TOOL_DE = {"search_library": "Werke", "find_passages": "Textstellen", "read_passages": "Gelesen",
           "get_reference": "Nachgeschlagen", "add_by_doi": "Per DOI"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def search_target(entry: dict) -> str:
    """What one logged lookup was about: the query, else the citekey or DOI."""
    return entry.get("query") or entry.get("citekey") or (f"doi:{entry['doi']}" if entry.get("doi") else "")


def search_result(entry: dict) -> str:
    """What one logged lookup brought back, in words ("5 Stellen · 2 Werke")."""
    if entry.get("error"):
        return "Fehler"
    tool, n = entry.get("tool"), entry.get("count")
    works = len(entry.get("works") or [])
    if tool == "get_reference":
        return "Metadaten"
    if tool == "add_by_doi":
        return "hinzugefügt" if entry.get("created") else "schon in der Bibliothek"
    if not n:
        return "nichts gefunden"
    if tool == "search_library":
        return _plural(n, "Werk", "Werke")
    if tool == "read_passages":
        return _plural(n, "Abschnitt", "Abschnitte")
    return _plural(n, "Stelle", "Stellen") + (f" · {_plural(works, 'Werk', 'Werke')}" if works else "")


def local_clock(at: Any) -> str:
    """``"18:09"`` in this machine's time zone for a stored UTC time."""
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return stamp.astimezone().strftime("%H:%M") if stamp.tzinfo else stamp.strftime("%H:%M")


def local_stamp(at: Any) -> str:
    """``"2026-11-10 18:09"`` in this machine's time zone for a stored UTC time."""
    from datetime import datetime
    text = str(at or "")
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text.replace("T", " ")[:16]
    return (stamp.astimezone() if stamp.tzinfo else stamp).strftime("%Y-%m-%d %H:%M")


def report_meta(report: dict) -> list[str]:
    """The meta line's parts: agent, time (local), project, lookups."""
    out = [x for x in (report.get("client_name"), local_stamp(report.get("created_at")),
                       report.get("project")) if x]
    if report.get("lookups"):
        out.append(_plural(int(report["lookups"]), "Nachschlagung", "Nachschlagungen"))
    return out


def in_library(src: dict) -> bool:
    return src.get("state") not in (MISSING, UNKNOWN_KEY)


def open_claims(report: dict) -> list[dict]:
    """The claims no work in the library stands behind — no source at all,
    or only works the library lacks. Read by work, these have no card."""
    return [c for c in report.get("claims") or []
            if not any(in_library(s) for s in c.get("sources") or [])]


def _md_source(lines: list[str], src: dict, *, name: bool = True) -> None:
    page = source_page(src) or ""
    head = source_name(src) + (f", {page}" if page else "") if name else page
    state = _STATE_DE.get(src["state"], "")
    if src.get("quote"):
        lines += [f"> „{src['quote']}“", ">"]
    lines.append(f"> — {head} ({state})" if head else f"> — {state}")
    if src["state"] in (MISSING, UNKNOWN_KEY) and reference_line(src):
        lines += [">", f"> {reference_line(src)}"]
    lines.append("")
    if src.get("cite"):
        lines += [f"`{src['cite']}`", ""]
    if src.get("note"):
        lines += [f"Agent: {src['note']}", ""]


def _research_markdown(report: dict) -> str:
    """A research report: question, summary, searches, findings per work, open."""
    lines = [f"# {report.get('title') or 'Recherchebericht'}", ""]
    meta = report_meta(report)
    lines += ["_" + " · ".join(["Recherchebericht", *meta]) + "_", ""]
    if report.get("question"):
        lines += [f"> **Anfrage:** {report['question']}", ""]
    c = report.get("counts") or {}
    if report.get("summary"):
        lines += ["## Kurz gesagt", "", report["summary"], ""]
    lines += [f"{_plural(c.get('verified', 0), 'Stelle', 'Stellen')} wörtlich bestätigt · "
              f"{_plural(c.get('works', 0), 'Quelle', 'Quellen')}", ""]
    searches = report.get("searches") or []
    if searches:
        lines += ["## Gesucht", "", "| Zeit | Art | Wonach | Ergebnis |", "|---|---|---|---|"]
        for s in searches:
            target = search_target(s).replace("|", "/")
            lines.append(f"| {local_clock(s.get('at'))} | {TOOL_DE.get(s.get('tool'), s.get('tool'))} "
                         f"| {target} | {search_result(s)} |")
        lines.append("")
    claims = {c.get("n"): c for c in report.get("claims") or []}
    works = report.get("works") or []
    if works:
        lines += ["## Gefunden", ""]
        for w in works:
            rel = RELEVANCE_DE.get(w.get("relevance"), "")
            lines += [f"### {w.get('title') or w['citekey']}" + (f" — {rel}" if rel else ""), ""]
            by = " · ".join(str(x) for x in (w["citekey"], w.get("authors"), w.get("year")) if x)
            lines += [f"_{by}_", ""]
            if w.get("why"):
                lines += [f"**Warum das für dich zählt** (Einschätzung des Agenten): {w['why']}", ""]
            for f in w.get("findings") or []:
                claim = claims.get(f.get("n"))
                sources = (claim or {}).get("sources") or []
                if not claim or not 0 <= f.get("src", 0) < len(sources):
                    continue
                lines += [f"**{claim['n']}. {claim['claim']}**", ""]
                _md_source(lines, sources[f.get("src", 0)], name=False)
    rest = open_claims(report)
    missing = report.get("missing") or []
    if rest or missing:
        lines += ["## Offen", ""]
        for claim in rest:
            lines += [f"**{claim['n']}. {claim['claim']}**", ""]
            if claim.get("sources"):
                lines += ["Belegt nur mit Werken, die nicht in der Bibliothek stehen:", ""]
                for src in claim["sources"]:
                    _md_source(lines, src, name=True)
            else:
                lines.append("Keine Stelle in der Bibliothek."
                             + (" Gesucht: " + ", ".join(f"„{q}“" for q in claim["searched"])
                                if claim.get("searched") else ""))
                lines.append("")
            if claim.get("note"):
                lines += [f"Agent: {claim['note']}", ""]
        if missing:
            lines += ["**Nicht in der Bibliothek**", ""]
            for m in missing:
                lines.append(f"- {reference_line(m) or source_name(m)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_markdown(report: dict) -> str:
    """"Als Markdown speichern": the report as one Markdown document — by
    work for a research report, by claim for a check."""
    if kind_of(report.get("kind"), report.get("question") or "") == RESEARCH:
        return _research_markdown(report)
    meta = report_meta(report)
    lines = [f"# {report.get('title') or 'Schlussbericht'}", ""]
    if meta:
        lines += ["_" + " · ".join(meta) + "_", ""]
    c = report.get("counts") or {}
    lines += [f"{c.get(SUPPORTED, 0)} belegt · {c.get(CHECK, 0)} prüfen · "
              + (f"{c[SOURCE_MISSING]} Quelle fehlt · " if c.get(SOURCE_MISSING) else "")
              + f"{c.get(UNSUPPORTED, 0)} ohne Beleg", ""]
    if report.get("summary"):
        lines += [report["summary"], ""]
    missing = report.get("missing") or []
    if missing:
        lines += ["## Nicht in der Bibliothek", ""]
        for m in missing:
            claims = ", ".join(str(n) for n in m.get("claims") or [])
            lines.append(f"- {reference_line(m) or source_name(m)}" + (f" (Aussage {claims})" if claims else ""))
        lines.append("")
    for claim in report.get("claims") or []:
        lines += [f"## {claim['n']}. {claim['claim']}", "", f"**{_STATUS_DE.get(claim['status'], '')}**", ""]
        for src in claim.get("sources") or []:
            _md_source(lines, src, name=True)
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
    ``[{report_id, title, created_at, claim}]``. A report none of whose
    claims match but whose question, summary or lookups do is one hit with
    ``claim: None``."""
    words = normalize(query).split()
    if not words:
        return []
    out: list[dict] = []
    for report in reports:
        head = {"report_id": report.get("id"), "title": report.get("title"),
                "created_at": report.get("created_at")}
        matched = False
        for claim in report.get("claims") or []:
            hay = normalize(" ".join([claim.get("claim") or "", claim.get("note") or "",
                                      " ".join(claim.get("searched") or []),
                                      *[f"{s.get('citekey')} {s.get('quote')} {s.get('note')} "
                                        f"{s.get('title') or ''} {s.get('authors') or ''} {s.get('doi') or ''}"
                                        for s in claim.get("sources") or []]]))
            if all(w in hay for w in words):
                matched = True
                out.append({**head, "claim": claim})
                if len(out) >= limit:
                    return out
        if matched:
            continue
        # No claim matched: the report itself may (its question, summary,
        # what was looked up, why a work matters) — a hit without a claim.
        hay = normalize(" ".join([report.get("title") or "", report.get("question") or "",
                                  report.get("summary") or "",
                                  *[search_target(s) for s in report.get("searches") or []],
                                  *[str(r.get("why") or "") for r in (report.get("ratings") or {}).values()]]))
        if all(w in hay for w in words):
            out.append({**head, "claim": None})
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
