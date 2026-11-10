"""A report as one printable page — "Drucken / PDF" (``GET /reports/{id}/print``).

The SPA loads this page into a hidden frame and opens the browser's print
dialog, where "Als PDF speichern" makes the PDF; nothing is written here. The
page is standalone: inline styles, system fonts, no script, no network. A
research report reads by work (question, short answer, lookups, findings per
work with its relevance, what stays open), a check by claim — the same order
as the Markdown export (``reports.to_markdown``). Pure; every text from the
report is escaped.
"""

from __future__ import annotations

from html import escape
from typing import Any

from lacite import reports as r

_STATUS = {r.SUPPORTED: ("Belegt", "ok"), r.CHECK: ("Prüfen", "warn"),
           r.SOURCE_MISSING: ("Quelle fehlt", "miss"), r.UNSUPPORTED: ("Ohne Beleg", "none")}
_STATE = {r.VERIFIED: "✓ wörtlich im PDF", r.NOT_FOUND: "Zitat nicht im Text gefunden",
          r.NO_TEXT: "kein Volltext – nicht geprüft", r.UNKNOWN_KEY: "nicht in der Bibliothek",
          r.NO_QUOTE: "ohne Zitat – nicht geprüft", r.MISSING: "nicht in der Bibliothek"}

CSS = """
@page { size: A4; margin: 18mm 18mm 20mm; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: #1a1a1a; font: 10.5pt/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width: 172mm; margin: 0 auto; padding: 12mm 0; }
.eyebrow, h2, .mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
.eyebrow { font-size: 8pt; letter-spacing: .06em; text-transform: uppercase; color: #6b6760; }
h1 { font: 500 20pt/1.2 Georgia, "Times New Roman", serif; margin: 4pt 0 4pt; }
h2 { font-size: 8pt; font-weight: 500; letter-spacing: .06em; text-transform: uppercase;
     color: #6b6760; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #e7e2d6; }
h3 { font: 500 12.5pt/1.3 Georgia, serif; margin: 0; }
.meta, .muted { color: #6b6760; font-size: 9pt; }
.ask { margin: 10pt 0 0; padding: 7pt 9pt; border: 1px solid #e7e2d6; border-radius: 6pt; }
.ask span { display: block; font-size: 8pt; color: #6b6760; }
.serif { font-family: Georgia, serif; }
.lead { font: 11.5pt/1.55 Georgia, serif; margin: 0 0 6pt; }
table { width: 100%; border-collapse: collapse; font-size: 9pt; }
td { padding: 3pt 6pt 3pt 0; border-top: 1px solid #efeadf; vertical-align: top; }
td.q { font-family: Georgia, serif; font-style: italic; }
td.n { text-align: right; white-space: nowrap; }
.work { break-inside: avoid-page; margin: 0 0 12pt; padding: 9pt 11pt; border: 1px solid #e7e2d6; border-radius: 7pt; }
.work-head { display: flex; justify-content: space-between; gap: 10pt; align-items: baseline; }
.tag { flex: none; font-size: 8pt; font-weight: 600; padding: 1pt 6pt; border-radius: 8pt; }
.tag.central { background: #fce9da; color: #7a2808; }
.tag.supporting { background: #f3eee4; color: #3a3833; }
.tag.marginal { border: 1px solid #d8d1be; color: #6b6760; }
.why { margin: 6pt 0 0; padding: 6pt 8pt; background: #f3eee4; border-radius: 5pt; }
.why span { display: block; font-size: 7.5pt; color: #6b6760; }
.finding { break-inside: avoid; margin-top: 8pt; }
.finding p { margin: 0 0 3pt; font-weight: 600; }
blockquote { margin: 0; padding: 0 0 0 9pt; font: 10.5pt/1.45 Georgia, serif; color: #3a3833;
             border: 0; border-left: 1.5pt solid #d8d1be; }
.src { font-size: 8.5pt; margin-top: 2pt; color: #6b6760; }
.src .ok { color: #1c7a45; } .src .warn { color: #8a6414; }
code { font: 8.5pt ui-monospace, Consolas, monospace; background: #f3eee4; padding: 0 3pt; border-radius: 3pt; }
.open { break-inside: avoid; margin: 0 0 8pt; padding: 7pt 9pt; border: 1px dashed #d8d1be; border-radius: 6pt; }
.claim { break-inside: avoid; display: grid; grid-template-columns: 16pt 1fr; gap: 6pt;
         padding: 8pt 0; border-top: 1px solid #efeadf; }
.status { font-size: 8.5pt; } .status.ok { color: #1c7a45; } .status.warn { color: #8a6414; }
.status.miss, .status.none { color: #6b6760; }
.agent { font-size: 9pt; color: #6b6760; margin: 3pt 0 0; }
"""


def _e(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _source_line(src: dict, *, name: bool) -> str:
    page = r.source_page(src) or ""
    head = (r.source_name(src) + (f" · {page}" if page else "")) if name else page
    state = _STATE.get(src.get("state"), "")
    cls = "ok" if src.get("state") == r.VERIFIED else "warn"
    parts = [f'<span class="mono">{_e(head)}</span>' if head else "", f'<span class="{cls}">{_e(state)}</span>']
    line = " · ".join(p for p in parts if p)
    if src.get("state") == r.VERIFIED and src.get("cite"):
        line += f" · <code>{_e(src['cite'])}</code>"
    return f'<div class="src">{line}</div>'


def _source(src: dict, *, name: bool) -> str:
    out = []
    if src.get("quote"):
        out.append(f"<blockquote>„{_e(src['quote'])}“</blockquote>")
    out.append(_source_line(src, name=name))
    if src.get("state") in (r.MISSING, r.UNKNOWN_KEY) and r.reference_line(src):
        out.append(f'<div class="src">{_e(r.reference_line(src))}</div>')
    if src.get("note"):
        out.append(f'<p class="agent">Agent: {_e(src["note"])}</p>')
    return "".join(out)


def _head(report: dict, word: str) -> str:
    meta = " · ".join(r.report_meta(report))
    out = [f'<div class="eyebrow">{_e(word)} · Nr. {_e(report.get("id"))}</div>',
           f"<h1>{_e(report.get('title') or word)}</h1>"]
    if meta:
        out.append(f'<div class="meta">{_e(meta)}</div>')
    if report.get("question"):
        out.append(f'<div class="ask"><span>Deine Anfrage</span>'
                   f'<span class="serif" style="font-size:11pt;color:#3a3833">„{_e(report["question"])}“</span></div>')
    return "".join(out)


def _searches(report: dict) -> str:
    rows = []
    for s in report.get("searches") or []:
        target = r.search_target(s) + (f" ({s['pages']})" if s.get("pages") else "")
        q_cls = "q" if s.get("query") else "mono"
        rows.append(f'<tr><td class="mono">{_e(r.local_clock(s.get("at")))}</td>'
                    f'<td>{_e(r.TOOL_DE.get(s.get("tool"), s.get("tool")))}</td>'
                    f'<td class="{q_cls}">{_e(target)}</td><td class="n">{_e(r.search_result(s))}</td></tr>')
    if not rows:
        return ""
    return ('<h2>Gesucht</h2><p class="muted" style="margin:0 0 4pt">aus dem LaCite-Protokoll, '
            'nicht vom Agenten</p><table>' + "".join(rows) + "</table>")


def _open(report: dict) -> str:
    out = []
    for claim in r.open_claims(report):
        searched = ", ".join(f"„{q}“" for q in claim.get("searched") or [])
        if claim.get("sources"):
            body = ('<span class="muted">Belegt nur mit Werken, die nicht in deiner Bibliothek stehen:</span>'
                    + "".join(_source(s, name=True) for s in claim["sources"]))
        else:
            body = ('<span class="muted">Keine Stelle in deiner Bibliothek.'
                    + (f" Gesucht: {_e(searched)}" if searched else "") + "</span>")
        note = f'<div class="muted">Agent: {_e(claim["note"])}</div>' if claim.get("note") else ""
        out.append(f'<div class="open"><strong>{_e(claim["n"])}. {_e(claim["claim"])}</strong><br>'
                   f"{body}{note}</div>")
    missing = report.get("missing") or []
    if missing:
        items = "".join(f"<li>{_e(r.reference_line(m) or r.source_name(m))}</li>" for m in missing)
        out.append(f'<div class="open"><strong>Nicht in deiner Bibliothek</strong><ul style="margin:3pt 0 0;'
                   f'padding-left:14pt">{items}</ul></div>')
    return ("<h2>Offen</h2>" + "".join(out)) if out else ""


def _research(report: dict) -> str:
    c = report.get("counts") or {}
    out = [_head(report, "Recherchebericht")]
    if report.get("summary"):
        out.append(f'<h2>Kurz gesagt</h2><p class="lead">{_e(report["summary"])}</p>')
    out.append(f'<div class="muted">{c.get("verified", 0)} Stellen wörtlich bestätigt · '
               f'{c.get("works", 0)} Quellen</div>')
    out.append(_searches(report))
    claims = {cl.get("n"): cl for cl in report.get("claims") or []}
    works = report.get("works") or []
    if works:
        out.append(f"<h2>Gefunden · {len(works)} Quellen</h2>")
    for w in works:
        rel = w.get("relevance") or ""
        tag = f'<span class="tag {rel}">{_e(r.RELEVANCE_DE[rel])}</span>' if rel in r.RELEVANCE_DE else ""
        by = " · ".join(str(x) for x in (w.get("citekey"), w.get("authors"), w.get("year")) if x)
        parts = [f'<section class="work"><div class="work-head"><h3>{_e(w.get("title") or w.get("citekey"))}</h3>'
                 f"{tag}</div><div class=\"meta mono\">{_e(by)}</div>"]
        if w.get("why"):
            parts.append(f'<div class="why"><span>Warum das für dich zählt · Einschätzung des Agenten</span>'
                         f"{_e(w['why'])}</div>")
        for f in w.get("findings") or []:
            claim = claims.get(f.get("n"))
            sources = (claim or {}).get("sources") or []
            if not claim or f.get("src", 0) >= len(sources):
                continue
            parts.append(f'<div class="finding"><p>{_e(claim["n"])}. {_e(claim["claim"])}</p>'
                         f"{_source(sources[f.get('src', 0)], name=False)}</div>")
        parts.append("</section>")
        out.append("".join(parts))
    out.append(_open(report))
    return "".join(out)


def _check(report: dict) -> str:
    out = [_head(report, "Zitatprüfung")]
    if report.get("summary"):
        out.append(f'<p class="lead" style="margin-top:10pt">{_e(report["summary"])}</p>')
    out.append(_searches(report))
    out.append("<h2>Aussagen</h2>")
    for claim in report.get("claims") or []:
        label, cls = _STATUS.get(claim.get("status"), _STATUS[r.UNSUPPORTED])
        body = [f'<p style="margin:0 0 3pt"><strong>{_e(claim["claim"])}</strong> '
                f'<span class="status {cls}">· {_e(label)}</span></p>']
        for src in claim.get("sources") or []:
            body.append(f'<div style="margin-top:4pt">{_source(src, name=True)}</div>')
        if not claim.get("sources"):
            searched = ", ".join(f"„{q}“" for q in claim.get("searched") or [])
            body.append('<div class="muted">Keine Stelle in der Bibliothek gefunden.'
                        + (f" Gesucht: {_e(searched)}" if searched else "") + "</div>")
        if claim.get("note"):
            body.append(f'<p class="agent">Agent: {_e(claim["note"])}</p>')
        out.append(f'<div class="claim"><span class="mono muted">{_e(claim.get("n"))}</span>'
                   f"<div>{''.join(body)}</div></div>")
    missing = report.get("missing") or []
    if missing:
        items = "".join(f"<li>{_e(r.reference_line(m) or r.source_name(m))}</li>" for m in missing)
        out.append(f"<h2>Nicht in deiner Bibliothek</h2><ul>{items}</ul>")
    return "".join(out)


def to_html(report: dict) -> str:
    """The whole page for one report (a :func:`reports.complete` one)."""
    research = r.kind_of(report.get("kind"), report.get("question") or "") == r.RESEARCH
    body = _research(report) if research else _check(report)
    title = _e(report.get("title") or ("Recherchebericht" if research else "Zitatprüfung"))
    return ("<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
            f"<title>{title}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")
