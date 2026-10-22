"""The MCP dispatch: JSON-RPC methods, the tools, page labels, guards."""

from __future__ import annotations

import pytest
from plugin_api.testing import InMemoryLibrary

from lacite.mcp import (INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, PROTOCOL_VERSIONS,
                        McpServer, page_info)


def _call(server, name, args=None, msg_id=1):
    return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                          "params": {"name": name, "arguments": args or {}}})["result"]


@pytest.fixture
def server(library):
    synced = []
    srv = McpServer(library, sync_all=lambda: synced.append(1) or [
        {"name": "Diss", "state": "ok", "entries": 3, "written": True, "error": None}],
        version="0.1.0", clock=lambda: "2026-10-22T10:00:00Z")
    srv.synced = synced
    return srv


def test_initialize_remembers_the_client(server):
    resp = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "claude-code", "version": "2.1.0"}}})
    result = resp["result"]
    assert resp["id"] == 0
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["version"] == "0.1.0"
    assert server.last_client == {"name": "claude-code", "version": "2.1.0", "at": "2026-10-22T10:00:00Z"}


def test_initialize_answers_its_newest_version_for_an_unknown_one(server):
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "1999-01-01"}})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert server.last_client["name"] == "MCP-Client"


def test_notifications_get_no_answer(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle_payload([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) is None


def test_ping_and_errors(server):
    assert server.handle({"jsonrpc": "2.0", "id": 7, "method": "ping"}) == {"jsonrpc": "2.0", "id": 7,
                                                                             "result": {}}
    assert server.handle({"jsonrpc": "2.0", "id": 8, "method": "nope"})["error"]["code"] == METHOD_NOT_FOUND
    assert server.handle({"id": 9, "method": "ping"})["error"]["code"] == INVALID_REQUEST
    assert server.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                          "params": {"name": "nope"}})["error"]["code"] == INVALID_PARAMS
    assert server.handle({"jsonrpc": "2.0", "id": 11, "result": {}}) is None  # a client response
    batch = server.handle_payload([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_tools_list_names_every_tool_with_a_schema(server):
    tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["search_library", "find_passages", "read_passages",
                                          "get_reference", "add_by_doi", "sync_bib"]
    for tool in tools:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_an_older_host_lists_no_passage_tools():
    class Old:
        def search_references(self, q, limit=20):
            return []

        def get_reference(self, key):
            return None

    names = [t["name"] for t in McpServer(Old()).tools()]
    assert names == ["search_library", "get_reference"]
    assert McpServer(Old()).call_tool("search_library", {"query": "x"})["isError"] is False
    with pytest.raises(LookupError):
        McpServer(Old()).call_tool("find_passages", {"query": "x"})


def test_search_library(server):
    result = _call(server, "search_library", {"query": "clay"})
    hit = result["structuredContent"]["results"][0]
    assert hit["citekey"] == "Smith2020" and hit["abstract_excerpt"] == "About clay."
    assert result["isError"] is False and '"Smith2020"' in result["content"][0]["text"]


def test_find_passages_cites_the_printed_page(server):
    hits = _call(server, "find_passages", {"query": "printed clay"})["structuredContent"]["results"]
    top = hits[0]
    assert top["page"] == "347" and top["pdf_page"] == 3
    assert top["page_label"] == "S. 347"
    assert top["cite"] == "\\cite[S.~347]{Smith2020}"
    assert top["text"] == "Printed clay layers bond at 80 degrees."


def test_find_passages_never_invents_a_printed_page(server):
    hits = _call(server, "find_passages", {"query": "electrolysis"})["structuredContent"]["results"]
    hit = hits[0]
    assert hit["page"] is None and hit["page_label"] == "PDF-S. 4"
    assert hit["cite"] == "\\cite{Smith2020}"
    assert "PDF-Seite 4" in hit["cite_note"]


def test_page_info_ranges_and_unknown():
    assert page_info({"printed_page_start": "12", "printed_page_end": "13"}, "k")["cite"] == \
        "\\cite[S.~12--13]{k}"
    assert page_info({"page_start": 2, "page_end": 3}, "k")["page_label"] == "PDF-S. 2–3"
    assert page_info({}, "k") == {"page": None, "pdf_page": None, "page_label": None,
                                  "cite": "\\cite{k}", "cite_note": "Seite unbekannt."}


def test_read_passages_and_the_abstract_fallback(server):
    out = _call(server, "read_passages", {"citekey": "Smith2020", "offset": 1})["structuredContent"]
    assert out["total"] == 2 and out["passages"][0]["index"] == 1
    assert out["passages"][0]["page_label"] == "PDF-S. 4"
    fallback = _call(server, "read_passages", {"citekey": "Doe2019"})["structuredContent"]
    assert fallback["total"] == 0 and fallback["abstract"] == "Only an abstract."
    unknown = _call(server, "read_passages", {"citekey": "Nope"})
    assert unknown["isError"] is True and "Nope" in unknown["content"][0]["text"]


def test_get_reference(server):
    out = _call(server, "get_reference", {"citekey": "Niu.2026"})["structuredContent"]
    assert out["doi"] == "10.1/niu" and out["cite"] == "\\cite{Niu.2026}"
    assert _call(server, "get_reference", {"citekey": "x"})["isError"] is True


def test_add_by_doi_creates_and_syncs(server, library):
    out = _call(server, "add_by_doi", {"doi": "https://doi.org/10.5555/NEW"})["structuredContent"]
    assert out["created"] is True and out["doi"] == "10.5555/new"
    assert server.synced == [1]
    again = _call(server, "add_by_doi", {"doi": "10.5555/new"})["structuredContent"]
    assert again["created"] is False and server.synced == [1]


def test_add_by_doi_without_write_permission_is_a_tool_error():
    lib = InMemoryLibrary([], write_allowed=False)
    result = _call(McpServer(lib), "add_by_doi", {"doi": "10.1/x"})
    assert result["isError"] is True and "library.write" in result["content"][0]["text"]


def test_bad_arguments_are_tool_errors(server):
    assert _call(server, "search_library", {})["isError"] is True
    assert _call(server, "search_library", {"query": "x", "limit": "many"})["isError"] is True
    resp = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "search_library", "arguments": [1]}})
    assert resp["result"]["isError"] is True


def test_a_failing_library_is_a_tool_error_not_a_crash():
    class Failing:
        def search_references(self, q, limit=20):
            raise RuntimeError("database is locked")

    result = McpServer(Failing()).call_tool("search_library", {"query": "x"})
    assert result["isError"] is True and "database is locked" in result["content"][0]["text"]


def test_sync_bib(server):
    out = _call(server, "sync_bib")["structuredContent"]
    assert out["connections"] == [{"name": "Diss", "state": "ok", "entries": 3, "written": True,
                                   "error": None}]
