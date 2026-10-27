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
    assert body["counts"] == {"supported": 1, "check": 1, "unsupported": 1, "total": 3}
    unchecked = body["claims"][1]["sources"][0]
    assert unchecked["state"] == reports.NOT_FOUND and unchecked["pdf_page_hint"] == 3
    assert reports.source_page(unchecked) == "PDF-S. 3"  # the hint, only ever as a PDF page
    assert body["claims"][2]["searched"] == ["Hydrophobierung Risse"]


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
    assert schema["properties"]["claims"]["items"]["properties"]["sources"]["items"]["required"] == \
        ["citekey", "quote"]
    init = server.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})
    assert "submit_report" in init["result"]["instructions"]


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
    assert client.get(f"{P}/reports/999").json()["detail"]["code"] == "error.lacite.report_not_found"
    asyncio.run(plugin.deactivate())

    plugin, client = _client(make_api(MANIFEST.permissions, library=_library(), storage=storage))
    assert len(client.get(f"{P}/reports").json()["reports"]) == 1
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
