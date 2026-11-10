"""The Schlussbericht: submit_report checks every quote, keeps the report, serves it."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugin_api.testing import InMemoryLibrary, TempSqliteStorage, make_api

import lacite
from lacite import reports
from lacite.mcp import McpServer, page_info
from lacite.report_store import ReportStore
from lacite.session_log import SessionLog
from lacite.session_log import searches as log_searches
from lacite_fakes import MANIFEST

P = "/api/plugins/lacite"

BOND = "Printed clay layers bond at 80 degrees."


def _library() -> InMemoryLibrary:
    return InMemoryLibrary([
        {"citekey": "Smith2020", "title": "Clay printing", "year": 2020, "passages": [
            {"text": "Intro text that says nothing much about the topic at hand.", "page_start": 2},
            {"text": "Printed clay layers bond at 80 de-\ngrees. The bond fails below 60 degrees "
                     "according to “all” our tests.", "page_start": 3, "printed_page_start": "347"},
            {"text": "Electrolysis at 80 degrees is more efficient than at room temperature.",
             "page_start": 4},
            {"text": "Printed clay layers bond at 80 degrees, as shown before.", "page_start": 9},
        ]},
        {"citekey": "Doe2019", "title": "No text", "year": 2019, "abstract": "Only an abstract."},
    ])


def _check(library, key, quote, **kw):
    return reports.verify_source(library, key, quote, page_info=page_info, **kw)


# ---------------------------------------------------------------------------
# Matching and one source
# ---------------------------------------------------------------------------

def test_normalize_ignores_case_space_quotes_dashes_and_hyphenation():
    assert reports.normalize("„Riss-\n bildung“ – im  BETON") == reports.normalize('"Rissbildung" - im beton')


def test_a_verbatim_quote_is_verified_with_the_page_of_its_passage(_lib=None):
    found = _check(_library(), "Smith2020", "printed clay layers bond at 80 degrees")
    assert found["state"] == reports.VERIFIED
    assert found["page_label"] == "S. 347" and found["cite"] == "\\cite[S.~347]{Smith2020}"
    assert found["passage"].startswith("Printed clay layers bond")
    assert found["occurrences"] == 2


def test_the_agents_pdf_page_only_picks_between_occurrences():
    found = _check(_library(), "Smith2020", "Printed clay layers bond at 80 degrees", pdf_page=9)
    assert found["state"] == reports.VERIFIED and found["page_label"] == "PDF-S. 9"
    assert "\\cite{Smith2020}" == found["cite"]  # no printed page known there: none invented
    wrong = _check(_library(), "Smith2020", "Printed clay layers bond at 80 degrees", pdf_page=4)
    assert wrong["page_label"] == "S. 347"  # not on the hinted page: the first occurrence


def test_an_ellipsis_is_a_gap_and_a_quote_may_run_over_a_passage_border():
    lib = _library()
    assert _check(lib, "Smith2020", "Printed clay layers bond … fails below 60 degrees")["state"] == "verified"
    assert _check(lib, "Smith2020", "our tests. Electrolysis at 80 degrees")["state"] == "verified"
    assert _check(lib, "Smith2020", "fails below 60 degrees […] Printed clay")["state"] == "verified"


def test_paraphrase_short_quotes_and_missing_text_are_not_verified():
    lib = _library()
    para = _check(lib, "Smith2020", "Clay layers stick together when printed at 80 °C")
    assert para["state"] == reports.NOT_FOUND and para["page_label"] is None
    assert _check(lib, "Smith2020", "80 degrees")["state"] == reports.NOT_FOUND  # too short to prove
    assert _check(lib, "Doe2019", "Only an abstract, nothing else here.")["state"] == reports.NO_TEXT
    assert _check(lib, "Nope2000", "whatever the agent made up here")["state"] == reports.UNKNOWN_KEY
    assert _check(lib, "Smith2020", "")["state"] == reports.NO_QUOTE


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------

ARGS = {
    "title": "Kapitel 2.3",
    "summary": "Zwei von drei Aussagen belegt.",
    "claims": [
        {"claim": "Lagen verbinden sich bei 80 Grad.",
         "sources": [{"citekey": "Smith2020", "quote": BOND, "note": "nur Ton"}]},
        {"claim": "Unter 60 Grad versagt der Verbund.",
         "sources": [{"citekey": "Smith2020", "quote": "The bond always fails when it is cold", "pdf_page": 3}]},
        {"claim": "Hydrophobierung hilft.", "searched": ["Hydrophobierung Risse"]},
    ],
}


def test_build_checks_every_source_and_sets_each_claims_status():
    body = reports.build(ARGS, _library(), page_info=page_info)
    assert [c["status"] for c in body["claims"]] == [reports.SUPPORTED, reports.CHECK, reports.UNSUPPORTED]
    assert body["counts"] == {"supported": 1, "check": 1, "missing": 0, "unsupported": 1, "total": 3,
                              "verified": 1, "works": 1, "missing_works": 0}
    assert body["kind"] == reports.CHECK_KIND and body["question"] == ""  # no question: a check
    unchecked = body["claims"][1]["sources"][0]
    assert unchecked["state"] == reports.NOT_FOUND and unchecked["pdf_page_hint"] == 3
    assert reports.source_page(unchecked) == "PDF-S. 3"  # the hint, only ever as a PDF page
    assert body["claims"][2]["searched"] == ["Hydrophobierung Risse"]
    assert body["missing"] == []


# A work the agent relied on that the library does not have, once by DOI,
# once only by an invented key, once by title alone.
MISSING_ARGS = {
    "title": "Kapitel 3",
    "claims": [
        {"claim": "Wasserstoff lässt sich in Kavernen speichern.",
         "sources": [{"doi": "https://doi.org/10.1000/Cavern", "title": "Cavern storage",
                      "authors": ["Meier, K.", "Roth, L."], "year": 2021,
                      "quote": "Salt caverns hold hydrogen at high pressure for months."}]},
        {"claim": "Kavernen sind dicht.",
         "sources": [{"doi": "10.1000/cavern", "quote": "tight"},
                     {"citekey": "Smith2020", "quote": "The bond always fails when it is cold"}]},
        {"claim": "Lagen verbinden sich bei 80 Grad.",
         "sources": [{"citekey": "Smith2020", "quote": BOND}, {"citekey": "Gone2018", "quote": "x"}]},
        {"claim": "Ton altert.", "sources": [{"title": "Aging of clay", "year": "1999"}, {"quote": "nothing"}]},
    ],
}


def test_a_work_not_in_the_library_is_missing_and_listed_once():
    body = reports.build(MISSING_ARGS, _library(), page_info=page_info)
    statuses = [c["status"] for c in body["claims"]]
    assert statuses == [reports.SOURCE_MISSING, reports.SOURCE_MISSING, reports.SUPPORTED, reports.SOURCE_MISSING]
    assert body["counts"] == {"supported": 1, "check": 0, "missing": 3, "unsupported": 0, "total": 4,
                              "verified": 1, "works": 1, "missing_works": 3}
    cavern = body["claims"][0]["sources"][0]
    assert cavern["state"] == reports.MISSING and cavern["citekey"] == "" and cavern["cite"] == ""
    assert cavern["doi"] == "10.1000/cavern" and cavern["authors"] == "Meier, K.; Roth, L."
    assert [s["state"] for s in body["claims"][2]["sources"]] == [reports.VERIFIED, reports.MISSING]
    assert len(body["claims"][3]["sources"]) == 1  # a source naming no work is dropped
    assert body["missing"] == [
        {"doi": "10.1000/cavern", "citekey": "", "title": "Cavern storage", "authors": "Meier, K.; Roth, L.",
         "year": 2021, "claims": [1, 2]},
        {"doi": "", "citekey": "Gone2018", "title": "", "authors": "", "year": None, "claims": [3]},
        {"doi": "", "citekey": "", "title": "Aging of clay", "authors": "", "year": 1999, "claims": [4]},
    ]


def test_the_agent_is_told_how_to_add_a_missing_work():
    body = reports.build(MISSING_ARGS, _library(), page_info=page_info)
    answer = reports.agent_answer({**body, "id": 7})
    assert "not in the user's library" in answer["message"] and "add_by_doi" in answer["message"]
    assert "could not be verified" in answer["message"]  # the not-found quote in claim 2
    src = answer["claims"][0]["sources"][0]
    assert src["state"] == "missing" and src["doi"] == "10.1000/cavern" and src["title"] == "Cavern storage"


def test_markdown_lists_the_missing_works():
    body = reports.build(MISSING_ARGS, _library(), page_info=page_info)
    md = reports.to_markdown({**body, "id": 1})
    assert "1 belegt · 0 prüfen · 3 Quelle fehlt · 0 ohne Beleg" in md
    assert "## Nicht in der Bibliothek" in md
    assert "- Meier, K.; Roth, L. (2021): Cavern storage. doi:10.1000/cavern (Aussage 1, 2)" in md
    assert "> — Cavern storage (nicht in der Bibliothek)" in md
    assert "`\\cite{}`" not in md
    assert reports.search([{**body, "id": 1}], "meier cavern")


def test_recheck_cites_the_added_key_and_verifies_its_quote():
    library = _library()
    body = reports.build(MISSING_ARGS, library, page_info=page_info)
    added = library.create_by_doi("10.1000/cavern", title="Cavern storage")
    library._refs[added["citekey"]]["passages"] = [
        {"text": "Salt caverns hold hydrogen at high pressure for months.", "page_start": 5}]
    again = reports.recheck(body, library, page_info=page_info, lookup=library.get_reference,
                            added={"https://doi.org/10.1000/CAVERN": added["citekey"]})
    cavern = again["claims"][0]["sources"][0]
    assert cavern["citekey"] == added["citekey"] and cavern["state"] == reports.VERIFIED
    assert again["claims"][0]["status"] == reports.SUPPORTED
    assert again["claims"][1]["status"] == reports.CHECK  # "tight" is too short to prove anything
    assert [m["citekey"] or m["title"] for m in again["missing"]] == ["Gone2018", "Aging of clay"]
    assert again["counts"]["missing"] == 1  # claims, not sources: claim 3 is supported anyway


def test_recheck_turns_an_old_unknown_key_into_missing():
    old = {"title": "Alt", "summary": "", "claims": [{"n": 1, "claim": "a", "status": "check", "sources": [
        {"citekey": "Gone2018", "quote": "x", "state": "unknown_key"}]}]}
    again = reports.recheck(old, _library(), page_info=page_info)
    assert again["claims"][0]["sources"][0]["state"] == reports.MISSING
    assert again["claims"][0]["status"] == reports.SOURCE_MISSING


@pytest.mark.parametrize("raw, doi", [
    ("10.1000/ABC", "10.1000/abc"), ("https://doi.org/10.1/x", "10.1/x"), ("doi: 10.1/x", "10.1/x"),
    ("http://dx.doi.org/10.1/x", "10.1/x"), ("nonsense", ""), ("", ""), (None, ""),
])
def test_normalize_doi(raw, doi):
    assert reports.normalize_doi(raw) == doi


@pytest.mark.parametrize("bad", [
    {}, {"title": "x"}, {"title": "x", "claims": []}, {"title": "", "claims": [{"claim": "a"}]},
    {"title": "x", "claims": [{"sources": []}]},
])
def test_build_refuses_what_is_no_report(bad):
    with pytest.raises(reports.ReportError):
        reports.build(bad, _library(), page_info=page_info)


def test_markdown_lists_claim_then_passage_and_never_a_page_the_library_did_not_confirm():
    body = reports.build(ARGS, _library(), page_info=page_info)
    md = reports.to_markdown({**body, "id": 1, "created_at": "2026-10-26T14:32:00Z",
                              "client_name": "claude-code", "project": "Dissertation"})
    assert md.startswith("# Kapitel 2.3\n")
    assert "## 1. Lagen verbinden sich bei 80 Grad." in md
    assert f"> „{BOND}“" in md and "Smith2020, S. 347 (wörtlich im Text)" in md
    assert "`\\cite[S.~347]{Smith2020}`" in md
    assert "Smith2020, PDF-S. 3 (Zitat nicht im Text gefunden)" in md
    assert "Gesucht: „Hydrophobierung Risse“" in md


def test_search_finds_claims_by_statement_quote_and_citekey_across_reports():
    body = reports.build(ARGS, _library(), page_info=page_info)
    stored = [{**body, "id": 2, "title": "B"}, {**body, "id": 1, "title": "A"}]
    hits = reports.search(stored, "verbund")
    assert [(h["report_id"], h["claim"]["n"]) for h in hits] == [(2, 2), (1, 2)]
    assert len(reports.search(stored, "smith2020 80")) == 2  # every word must match
    assert reports.search(stored, "   ") == []


def test_project_names_the_connection_of_the_folder(tmp_path):
    conns = [{"name": "Diss", "folder": str(tmp_path / "Diss")}]
    assert reports.project_for(str(tmp_path / "Diss"), conns) == "Diss"
    assert reports.project_for(str(tmp_path / "Diss" / "kapitel"), conns) == "Diss"
    assert reports.project_for(str(tmp_path / "Other"), conns) == "Other"
    assert reports.project_for("", conns) == ""


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_the_store_keeps_reports_across_handles_and_replaces_only_when_named(tmp_path):
    storage = TempSqliteStorage(tmp_path)
    store = ReportStore(storage.open_plugin_db("reports"))
    body = reports.build(ARGS, _library(), page_info=page_info)
    first = store.save(body, at="2026-10-26T14:00:00Z", client_name="claude-code", lookups=7)
    second = store.save({**body, "title": "Zweiter"}, at="2026-10-26T15:00:00Z")
    fixed = store.save({**body, "title": "Kapitel 2.3 korrigiert"}, at="2026-10-26T16:00:00Z",
                       replaces=first["id"])
    assert fixed["id"] == first["id"] and fixed["created_at"] == "2026-10-26T14:00:00Z"
    storage.close()

    again = ReportStore(TempSqliteStorage(tmp_path).open_plugin_db("reports"))
    assert [r["title"] for r in again.summaries()] == ["Zweiter", "Kapitel 2.3 korrigiert"]
    assert "claims" not in again.summaries()[0]
    assert again.get(first["id"])["claims"][0]["sources"][0]["page_label"] == "S. 347"
    assert again.get(first["id"])["lookups"] is None and second["lookups"] is None
    assert again.get(999) is None


# ---------------------------------------------------------------------------
# MCP and the live session
# ---------------------------------------------------------------------------

def _server(log=None, store=None):
    server = McpServer(_library(), sync_all=lambda: [], version="0.3.0",
                       clock=lambda: "2026-10-26T14:32:00Z", log=log.record if log else None,
                       reports=store or ReportStore.in_memory(),
                       connections=lambda: [{"name": "Diss", "folder": "C:/proj/diss"}],
                       lookups=(lambda: log.count_since("submit_report")) if log else None)
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2.1"}}})
    return server


def _call(server, name, args):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})["result"]


def test_submit_report_is_listed_and_the_instructions_ask_for_it():
    server = _server()
    tools = {t["name"]: t for t in server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
             ["result"]["tools"]}
    schema = tools["submit_report"]["inputSchema"]
    assert schema["required"] == ["title", "claims"]
    source = schema["properties"]["claims"]["items"]["properties"]["sources"]["items"]
    assert "required" not in source  # a work not in the library has no citekey
    assert {"citekey", "quote", "doi", "title", "authors", "year"} <= set(source["properties"])
    init = server.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})
    assert "submit_report" in init["result"]["instructions"]
    assert "without citekey" in init["result"]["instructions"]


def test_no_submit_report_without_a_store_or_passages():
    assert "submit_report" not in {t["name"] for t in McpServer(_library()).tools()}

    class Old:
        def get_reference(self, key):
            return None
    assert "submit_report" not in {t["name"] for t in McpServer(Old(), reports=ReportStore.in_memory()).tools()}


def test_submit_report_answers_per_source_and_logs_a_report_entry():
    log = SessionLog()
    store = ReportStore.in_memory()
    server = _server(log, store)
    _call(server, "find_passages", {"query": "clay"})
    _call(server, "read_passages", {"citekey": "Smith2020"})
    result = _call(server, "submit_report", {**ARGS, "project_folder": "C:/proj/diss"})
    assert result["isError"] is False
    answer = result["structuredContent"]
    assert answer["report_id"] == 1 and answer["counts"]["total"] == 3
    assert "replaces=1" in answer["message"]
    first = answer["claims"][0]["sources"][0]
    assert first == {"citekey": "Smith2020", "state": "verified",
                     "explanation": "verified verbatim in the text", "page_label": "S. 347",
                     "cite": "\\cite[S.~347]{Smith2020}"}

    saved = store.get(1)
    assert saved["client_name"] == "claude-code" and saved["project"] == "Diss" and saved["lookups"] == 2
    entry = log.entries()[-1]
    assert entry["tool"] == "submit_report" and entry["count"] == 3
    assert entry["report"] == {"id": 1, "title": "Kapitel 2.3", "counts": answer["counts"]}
    assert log.count_since("submit_report") == 0


RESEARCH_ARGS = {
    "kind": "research",
    "title": "Wie verbinden sich gedruckte Tonlagen?",
    "question": "Was sagt meine Bibliothek zur Verbindung von Tonlagen?",
    "summary": "Bei 80 Grad verbinden sie sich, unter 60 Grad versagt der Verbund.",
    "works": [{"citekey": "Smith2020", "relevance": "central", "why": "Nennt genau deine Temperatur."},
              {"citekey": "Doe2019", "relevance": "urgent", "why": "unbekannte Stufe"},
              {"relevance": "central"}],
    "claims": [
        {"claim": "Tonlagen verbinden sich bei 80 Grad.", "sources": [{"citekey": "Smith2020", "quote": BOND}]},
        {"claim": "Nur ein Abstract.", "sources": [{"citekey": "Doe2019", "quote": "Only an abstract, nothing more."}]},
        {"claim": "Zum Düsenabstand nichts.", "searched": ["nozzle distance"]},
    ],
}


def test_a_research_report_keeps_the_question_and_groups_findings_by_work_most_relevant_first():
    lib = _library()
    body = reports.build(RESEARCH_ARGS, lib, page_info=page_info, lookup=lib.get_reference,
                         searches=[{"tool": "find_passages", "query": "clay bond", "count": 2}])
    assert body["kind"] == reports.RESEARCH and body["question"].startswith("Was sagt")
    assert body["ratings"] == {"Smith2020": {"relevance": "central", "why": "Nennt genau deine Temperatur."},
                               "Doe2019": {"relevance": "", "why": "unbekannte Stufe"}}
    assert [w["citekey"] for w in body["works"]] == ["Smith2020", "Doe2019"]
    smith = body["works"][0]
    assert smith["relevance"] == "central" and smith["verified"] == 1
    assert smith["findings"] == [{"n": 1, "src": 0}] and smith["title"] == "Clay printing"
    assert body["searches"] == [{"tool": "find_passages", "query": "clay bond", "count": 2}]
    assert [c["n"] for c in reports.open_claims(body)] == [3]
    assert body["counts"]["verified"] == 1 and body["counts"]["works"] == 2


def test_works_are_ordered_by_relevance_then_first_appearance():
    claims = [{"n": 1, "sources": [{"citekey": "A", "state": "verified"}]},
              {"n": 2, "sources": [{"citekey": "B", "state": "not_found"}, {"citekey": "C", "state": "verified"},
                                   {"citekey": "A", "state": "verified"}, {"title": "X", "state": "missing"}]}]
    works = reports.works_of(claims, {"C": {"relevance": "central", "why": ""},
                                      "A": {"relevance": "marginal", "why": ""}})
    assert [w["citekey"] for w in works] == ["C", "A", "B"]
    assert works[1]["findings"] == [{"n": 1, "src": 0}, {"n": 2, "src": 2}] and works[1]["verified"] == 2


def test_kind_without_a_valid_value_follows_the_question():
    assert reports.kind_of("", "Was gibt es zu X?") == reports.RESEARCH
    assert reports.kind_of(None) == reports.CHECK_KIND
    assert reports.kind_of("CHECK", "frage") == reports.CHECK_KIND
    assert reports.kind_of("nonsense") == reports.CHECK_KIND


def test_an_older_report_is_completed_for_the_page_without_rechecking():
    old = {"title": "Alt", "summary": "", "counts": {"supported": 1, "total": 1},
           "claims": [{"n": 1, "claim": "a", "status": "supported", "sources": [
               {"citekey": "Smith2020", "quote": BOND, "state": "verified", "title": "Clay printing"}]}]}
    done = reports.complete(old)
    assert done["kind"] == reports.CHECK_KIND and done["searches"] == [] and done["missing"] == []
    assert [w["citekey"] for w in done["works"]] == ["Smith2020"]
    assert done["counts"]["supported"] == 1 and done["counts"]["verified"] == 1 and done["counts"]["works"] == 1


def test_research_markdown_reads_question_summary_lookups_works_and_open():
    lib = _library()
    body = reports.build(RESEARCH_ARGS, lib, page_info=page_info, lookup=lib.get_reference,
                         searches=[{"at": "2026-10-27T17:09:00Z", "tool": "find_passages", "query": "clay | bond",
                                    "count": 2, "works": ["Smith2020"]},
                                   {"at": "2026-10-27T17:12:00Z", "tool": "find_passages",
                                    "query": "nozzle distance", "count": 0, "works": []}])
    md = reports.to_markdown({**body, "id": 3, "client_name": "claude-code", "created_at": "2026-10-27T17:31:00Z"})
    assert md.startswith("# Wie verbinden sich gedruckte Tonlagen?\n")
    assert "> **Anfrage:** Was sagt meine Bibliothek" in md
    assert "## Kurz gesagt" in md and "## Gesucht" in md and "## Gefunden" in md and "## Offen" in md
    assert "| clay / bond | 2 Stellen · 1 Werk |" in md
    assert "| nozzle distance | nichts gefunden |" in md
    assert "### Clay printing — Zentral" in md and "Nennt genau deine Temperatur." in md
    assert md.index("Clay printing") < md.index("### No text")
    assert "S. 347" in md and "`\\cite[S.~347]{Smith2020}`" in md
    assert "**3. Zum Düsenabstand nichts.**\n\nKeine Stelle in der Bibliothek. Gesucht: „nozzle distance“" in md


def test_the_print_page_escapes_everything_and_reads_like_the_report():
    from lacite import report_html
    args = {**RESEARCH_ARGS, "title": "<script>alert(1)</script>"}
    body = reports.build(args, _library(), page_info=page_info)
    page = report_html.to_html({**body, "id": 4})
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "Recherchebericht · Nr. 4" in page and "Gefunden · 2 Quellen" in page
    assert "Einschätzung des Agenten" in page and "Zentral" in page and "\\cite[S.~347]{Smith2020}" in page
    check = report_html.to_html({**reports.build(ARGS, _library(), page_info=page_info), "id": 5})
    assert "Zitatprüfung · Nr. 5" in check and "Ohne Beleg" in check


def test_search_finds_a_report_by_its_question_or_lookups_as_a_hit_without_claim():
    body = reports.build(RESEARCH_ARGS, _library(), page_info=page_info,
                         searches=[{"tool": "find_passages", "query": "Spritzkopf Lehm", "count": 0}])
    found = reports.search([{**body, "id": 1}], "spritzkopf")
    assert found == [{"report_id": 1, "title": body["title"], "created_at": None, "claim": None}]
    assert [h["claim"]["n"] for h in reports.search([{**body, "id": 1}], "80 Grad")] == [1]


def test_the_report_takes_its_lookups_from_the_log_and_a_correction_keeps_its_own():
    log = SessionLog()
    store = ReportStore.in_memory()
    server = _server(log, store)
    server.searches = lambda: log_searches(log.since("submit_report"))
    _call(server, "find_passages", {"query": "clay bond"})
    _call(server, "submit_report", RESEARCH_ARGS)
    first = store.get(1)
    assert [s["query"] for s in first["searches"]] == ["clay bond"]
    assert first["searches"][0]["works"] == ["Smith2020"] and first["lookups"] == 1
    # The next request starts; a correction of report 1 in between takes none
    # of its lookups, and they stay with the next report.
    _call(server, "read_passages", {"citekey": "Smith2020"})
    _call(server, "submit_report", {**RESEARCH_ARGS, "replaces": 1})
    again = store.get(1)
    assert [s["query"] for s in again["searches"]] == ["clay bond"] and again["lookups"] == 1
    _call(server, "submit_report", RESEARCH_ARGS)
    second = store.get(2)
    assert [s["tool"] for s in second["searches"]] == ["read_passages"] and second["lookups"] == 1
    tools = {t["name"]: t for t in server.tools()}
    props = tools["submit_report"]["inputSchema"]["properties"]
    assert props["kind"]["enum"] == ["research", "check"] and "works" in props and "question" in props


def test_a_broken_report_is_a_tool_error_the_agent_reads():
    result = _call(_server(), "submit_report", {"title": "x", "claims": []})
    assert result["isError"] is True and "claims" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Plugin + HTTP
# ---------------------------------------------------------------------------

def _client(api):
    plugin = lacite.LaCitePlugin()
    app = FastAPI()

    async def boot():
        await plugin.activate(api)
    asyncio.run(boot())
    for router in api.routes.routers:
        app.include_router(router)
    return plugin, TestClient(app)


def _mcp(client, name, args):
    return client.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": name, "arguments": args}}).json()


def test_reports_are_served_searched_and_downloaded_and_survive_a_restart(tmp_path):
    storage = TempSqliteStorage(tmp_path)
    plugin, client = _client(make_api(MANIFEST.permissions, library=_library(), storage=storage))
    _mcp(client, "submit_report", ARGS)
    listing = client.get(f"{P}/reports").json()
    assert listing["persistent"] is True
    assert [r["title"] for r in listing["reports"]] == ["Kapitel 2.3"]
    assert listing["reports"][0]["counts"]["supported"] == 1
    report_id = listing["reports"][0]["id"]
    full = client.get(f"{P}/reports/{report_id}").json()
    assert full["claims"][0]["sources"][0]["passage"].startswith("Printed clay")
    hits = client.get(f"{P}/reports", params={"q": "Hydrophobierung"}).json()["hits"]
    assert [h["claim"]["n"] for h in hits] == [3]
    md = client.get(f"{P}/reports/{report_id}/markdown")
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment;" in md.headers["content-disposition"] and ".md" in md.headers["content-disposition"]
    assert md.text.startswith("# Kapitel 2.3")
    printed = client.get(f"{P}/reports/{report_id}/print")
    assert printed.headers["content-type"].startswith("text/html") and "Zitatprüfung · Nr." in printed.text
    assert full["kind"] == "check" and full["works"][0]["citekey"] == "Smith2020"
    assert listing["reports"][0]["kind"] == "check" and "claims" not in listing["reports"][0]
    assert client.get(f"{P}/reports/999").json()["detail"]["code"] == "error.lacite.report_not_found"
    _mcp(client, "find_passages", {"query": "clay"})
    _mcp(client, "submit_report", RESEARCH_ARGS)
    research = client.get(f"{P}/reports").json()["reports"][0]
    assert research["kind"] == "research" and research["question"].startswith("Was sagt")
    assert research["summary"] and "searches" not in research
    served = client.get(f"{P}/reports/{research['id']}").json()
    assert [s["query"] for s in served["searches"]] == ["clay"]  # wired to the live session
    md_research = client.get(f"{P}/reports/{research['id']}/markdown")
    assert "Recherchebericht-" in md_research.headers["content-disposition"]
    asyncio.run(plugin.deactivate())

    plugin, client = _client(make_api(MANIFEST.permissions, library=_library(), storage=storage))
    assert len(client.get(f"{P}/reports").json()["reports"]) == 2
    asyncio.run(plugin.deactivate())
    storage.close()


def test_without_storage_the_reports_live_in_memory():
    perms = [p for p in MANIFEST.permissions if p.value != "storage"]
    plugin, client = _client(make_api(perms, library=_library()))
    _mcp(client, "submit_report", ARGS)
    listing = client.get(f"{P}/reports").json()
    assert listing["persistent"] is False and len(listing["reports"]) == 1
    asyncio.run(plugin.deactivate())
    assert json.dumps(listing)  # plain JSON


def test_a_missing_work_is_added_by_doi_and_the_report_checked_again():
    library = _library()
    plugin, client = _client(make_api(MANIFEST.permissions, library=library))
    _mcp(client, "submit_report", MISSING_ARGS)
    report = client.get(f"{P}/reports").json()["reports"][0]
    assert report["counts"]["missing"] == 3
    full = client.get(f"{P}/reports/{report['id']}").json()
    assert full["missing"][0]["doi"] == "10.1000/cavern"

    out = client.post(f"{P}/reports/{report['id']}/add-source", json={"doi": "https://doi.org/10.1000/cavern"})
    assert out.status_code == 200
    added = out.json()["added"]
    assert added["created"] is True and added["citekey"]
    ref = library.get_reference(added["citekey"])
    assert ref["title"] == "Cavern storage" and ref["year"] == 2021  # what the agent said about it
    again = out.json()["report"]
    assert again["id"] == report["id"] and again["created_at"] == report["created_at"]
    assert again["claims"][0]["sources"][0]["citekey"] == added["citekey"]
    assert again["claims"][0]["sources"][0]["state"] == "no_text"  # in the library now, no PDF text yet
    assert [m["citekey"] or m["title"] for m in again["missing"]] == ["Gone2018", "Aging of clay"]
    assert client.get(f"{P}/reports/{report['id']}").json()["counts"]["missing"] == 1

    # The same DOI again, or one the report does not list: nothing to add.
    again_out = client.post(f"{P}/reports/{report['id']}/add-source", json={"doi": "10.1000/cavern"})
    assert again_out.status_code == 409
    assert again_out.json()["detail"]["code"] == "error.lacite.source_not_missing"

    library.add({"citekey": "Gone2018", "title": "Back again", "abstract": "x"})
    rechecked = client.post(f"{P}/reports/{report['id']}/recheck").json()
    assert rechecked["claims"][2]["sources"][1]["state"] == "no_text"
    assert [m["title"] for m in rechecked["missing"]] == ["Aging of clay"]
    asyncio.run(plugin.deactivate())


def test_adding_needs_library_write():
    perms = [p for p in MANIFEST.permissions if p.value != "library.write"]
    plugin, client = _client(make_api(perms, library=_library()))
    _mcp(client, "submit_report", MISSING_ARGS)
    out = client.post(f"{P}/reports/1/add-source", json={"doi": "10.1000/cavern"})
    assert out.status_code == 403 and out.json()["detail"]["code"] == "error.lacite.write_denied"
    asyncio.run(plugin.deactivate())


def test_a_failed_submit_does_not_take_the_lookups_from_the_next_report():
    log = SessionLog()
    store = ReportStore.in_memory()
    server = _server(log, store)
    server.searches = lambda: log_searches(log.since("submit_report"))
    _call(server, "find_passages", {"query": "clay bond"})
    assert _call(server, "submit_report", {"claims": []})["isError"] is True
    _call(server, "submit_report", RESEARCH_ARGS)
    assert [s["query"] for s in store.get(1)["searches"]] == ["clay bond"] and store.get(1)["lookups"] == 1


def test_a_claim_backed_only_by_works_outside_the_library_stays_open_with_its_quote():
    from lacite import report_html
    args = {**RESEARCH_ARGS, "claims": [*RESEARCH_ARGS["claims"], {
        "claim": "Kavernen halten Wasserstoff.",
        "sources": [{"doi": "10.1000/cavern", "title": "Cavern storage", "quote": "Salt caverns hold hydrogen."}]}]}
    body = reports.build(args, _library(), page_info=page_info)
    n = len(args["claims"])
    assert [c["n"] for c in reports.open_claims(body)][-1] == n
    md = reports.to_markdown({**body, "id": 1})
    assert f"**{n}. Kavernen halten Wasserstoff.**" in md and "„Salt caverns hold hydrogen.“" in md
    page = report_html.to_html({**body, "id": 1})
    assert "Kavernen halten Wasserstoff." in page and "Salt caverns hold hydrogen." in page


def test_recheck_finds_a_work_the_user_added_by_hand_by_doi_or_title():
    library = _library()
    body = reports.build(MISSING_ARGS, library, page_info=page_info)
    library._refs["Aging1999"] = {"citekey": "Aging1999", "title": "Aging of Clay", "year": 1999, "passages": []}
    again = reports.recheck(body, library, page_info=page_info)
    aging = again["claims"][3]["sources"][0]
    assert aging["citekey"] == "Aging1999" and aging["state"] != reports.MISSING
    assert [m["citekey"] or m["title"] for m in again["missing"]] == ["Cavern storage", "Gone2018"]
    assert reports.find_added(library, {"title": "Aging of"}) == ""  # only an exact title counts


def test_recheck_keeps_the_agents_pdf_page_hint():
    body = reports.build(ARGS, _library(), page_info=page_info)
    assert body["claims"][1]["sources"][0]["pdf_page_hint"] == 3
    again = reports.recheck(body, _library(), page_info=page_info)
    src = again["claims"][1]["sources"][0]
    assert src["pdf_page_hint"] == 3 and reports.source_page(src) == "PDF-S. 3"


def test_the_meta_line_shows_local_time():
    from datetime import datetime
    stamp = datetime.fromisoformat("2026-10-27T17:31:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
    assert stamp in reports.report_meta({"created_at": "2026-10-27T17:31:00Z"})
    assert reports.local_stamp("kaputt") == "kaputt"
