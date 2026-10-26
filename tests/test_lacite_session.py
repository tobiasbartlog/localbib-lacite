"""The live session: every MCP tool call logged, ring-buffered and pushed over SSE."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugin_api.testing import make_api

import lacite
from lacite import api as lacite_api
from lacite.events import EventBroadcaster
from lacite.mcp import McpServer
from lacite.session_log import SessionLog, summarize
from lacite_fakes import MANIFEST

P = "/api/plugins/lacite"


def _call(server, name, args=None, msg_id=1):
    return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                          "params": {"name": name, "arguments": args or {}}})


def _init(server, name="claude-code", version="2.1.0"):
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "clientInfo": {"name": name, "version": version}}})


@pytest.fixture
def logged(library):
    log = SessionLog()
    server = McpServer(library, sync_all=lambda: [], version="0.1.0",
                       clock=lambda: "2026-10-23T09:00:00Z", log=log.record)
    _init(server)
    return server, log


def test_every_tool_call_is_logged_with_agent_tool_args_and_hits(logged):
    server, log = logged
    _call(server, "search_library", {"query": "clay"})
    _call(server, "find_passages", {"query": "printed clay", "limit": 2})
    _call(server, "read_passages", {"citekey": "Smith2020"})
    _call(server, "get_reference", {"citekey": "Niu.2026"})
    _call(server, "add_by_doi", {"doi": "10.5555/new"})
    _call(server, "sync_bib")

    entries = log.entries()
    assert [e["tool"] for e in entries] == ["search_library", "find_passages", "read_passages",
                                            "get_reference", "add_by_doi", "sync_bib"]
    assert [e["id"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert all(e["client"] == {"name": "claude-code", "version": "2.1.0"} for e in entries)
    assert all(e["at"] == "2026-10-23T09:00:00Z" and e["error"] is None for e in entries)
    assert all(isinstance(e["duration_ms"], int) and e["duration_ms"] >= 0 for e in entries)

    search, find, read, ref, added, synced = entries
    assert search["args"] == {"query": "clay"}
    assert search["hits"][0]["citekey"] == "Smith2020"
    assert search["hits"][0]["paper_id"] == 1 and search["hits"][0]["has_pdf"] is False

    assert find["args"] == {"query": "printed clay"}  # only the core arguments
    top = find["hits"][0]
    assert top["citekey"] == "Smith2020" and top["page_label"] == "S. 347" and top["pdf_page"] == 3
    assert top["text"] == "Printed clay layers bond at 80 degrees."
    assert top["cite"] == "\\cite[S.~347]{Smith2020}"
    assert top["paper_id"] is None  # passages carry no id: the page resolves it by citekey
    assert find["count"] == len(find["hits"])

    assert read["args"] == {"citekey": "Smith2020"} and read["count"] == 2
    assert [h["index"] for h in read["hits"]] == [0, 1]
    assert read["hits"][1]["page_label"] == "PDF-S. 4"

    assert ref["hits"][0]["citekey"] == "Niu.2026" and ref["hits"][0]["paper_id"] == 2
    assert added["hits"][0]["created"] is True and added["hits"][0]["paper_id"] is not None
    assert synced["count"] == 0 and synced["hits"] == []


def test_the_ring_buffer_keeps_the_newest_calls(library):
    log = SessionLog(limit=3)
    server = McpServer(library, log=log.record)
    for i in range(5):
        _call(server, "search_library", {"query": f"q{i}"})
    entries = log.entries()
    assert [e["args"]["query"] for e in entries] == ["q2", "q3", "q4"]
    assert [e["id"] for e in entries] == [3, 4, 5]
    log.clear()
    assert log.entries() == []
    _call(server, "search_library", {"query": "after"})
    assert log.entries()[0]["id"] == 6


def test_a_failed_call_is_logged_with_its_error(logged):
    server, log = logged
    unknown = _call(server, "get_reference", {"citekey": "Nope"})
    assert unknown["result"]["isError"] is True
    missing = _call(server, "find_passages", {})
    assert missing["result"]["isError"] is True
    bad = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "search_library", "arguments": [1]}})
    assert bad["result"]["isError"] is True
    errors = [(e["tool"], e["error"]) for e in log.entries()]
    assert errors == [("get_reference", "Unknown citekey: Nope"),
                      ("find_passages", "'query' is required"),
                      ("search_library", "'arguments' must be an object")]
    assert all(e["hits"] == [] and e["count"] is None for e in log.entries())


def test_a_failing_library_call_is_logged_too():
    class Failing:
        def search_references(self, q, limit=20):
            raise RuntimeError("database is locked")

    log = SessionLog()
    result = McpServer(Failing(), log=log.record).call_tool("search_library", {"query": "x"})
    assert result["isError"] is True
    assert log.entries()[0]["error"] == "search_library failed: database is locked"


def test_a_broken_log_never_breaks_the_mcp_call(library):
    def explode(entry):
        raise RuntimeError("log is broken")

    server = McpServer(library, log=explode)
    result = _call(server, "find_passages", {"query": "printed clay"})["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["results"][0]["citekey"] == "Smith2020"

    def bad_publish(event):
        raise RuntimeError("no stream")

    log = SessionLog(publish=bad_publish)
    server = McpServer(library, log=log.record)
    assert _call(server, "search_library", {"query": "clay"})["result"]["isError"] is False
    assert len(log.entries()) == 1


def test_summarize_clips_long_text_and_arguments():
    entry = summarize("find_passages", {"query": "x" * 1000, "limit": 3},
                      {"results": [{"citekey": "k", "text": "word " * 200}] * 30})
    assert len(entry["args"]["query"]) <= 301 and "limit" not in entry["args"]
    assert entry["count"] == 30 and len(entry["hits"]) == 20
    assert entry["hits"][0]["text"].endswith("…") and len(entry["hits"][0]["text"]) <= 281
    assert entry["client"] is None


# --- HTTP + SSE --------------------------------------------------------------

def _mount(api):
    app = FastAPI()
    for router in api.routes.routers:
        app.include_router(router)
    return TestClient(app)


def _route(api, path, method="GET"):
    return next(r for r in api.routes.routers[0].routes
                if r.path == P + path and method in r.methods)


def test_session_routes_list_and_clear(library):
    api = make_api(MANIFEST.permissions, library=library)
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    try:
        client = _mount(api)
        assert client.get(f"{P}/session").json()["entries"] == []
        client.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "opencode", "version": "1"}}})
        client.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "find_passages", "arguments": {"query": "printed clay"}}})
        session = client.get(f"{P}/session").json()
        assert session["limit"] == 200 and session["client"]["name"] == "opencode"
        (entry,) = session["entries"]
        assert entry["tool"] == "find_passages" and entry["client"]["name"] == "opencode"
        assert entry["hits"][0]["page_label"] == "S. 347"
        assert client.delete(f"{P}/session").json() == {"entries": []}
        assert client.get(f"{P}/session").json()["entries"] == []
    finally:
        asyncio.run(plugin.deactivate())


def test_an_sse_event_follows_a_tools_call(library):
    async def scenario():
        api = make_api(MANIFEST.permissions, library=library)
        plugin = lacite.LaCitePlugin()
        await plugin.activate(api)
        try:
            response = await _route(api, "/events").endpoint()
            assert response.media_type == "text/event-stream"
            stream = response.body_iterator
            first = asyncio.ensure_future(stream.__anext__())
            await asyncio.sleep(0)  # the subscriber is registered
            assert plugin.events.subscriber_count == 1
            # MCP calls run in the threadpool: publish from another thread.
            await asyncio.to_thread(plugin.mcp.handle, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "find_passages", "arguments": {"query": "printed clay"}}})
            chunk = await asyncio.wait_for(first, timeout=2)
            assert chunk.startswith("data: ") and chunk.endswith("\n\n")
            event = json.loads(chunk[len("data: "):])
            assert event["type"] == "mcp-call"
            assert event["entry"]["tool"] == "find_passages"
            assert event["entry"]["hits"][0]["citekey"] == "Smith2020"

            nxt = asyncio.ensure_future(stream.__anext__())
            await asyncio.to_thread(plugin.session.clear)
            cleared = await asyncio.wait_for(nxt, timeout=2)
            assert '"session-cleared"' in cleared

            # deactivate ends open streams instead of leaving them waiting
            last = asyncio.ensure_future(stream.__anext__())
            await plugin.deactivate()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(last, timeout=2)
        finally:
            if plugin.engine is not None:
                await plugin.deactivate()

    asyncio.run(scenario())


def test_deactivate_unhooks_the_session_log(library):
    api = make_api(MANIFEST.permissions, library=library)
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    mcp, session, events = plugin.mcp, plugin.session, plugin.events
    asyncio.run(plugin.deactivate())
    assert plugin.session is None and plugin.events is None
    assert mcp.log is None and session.publish is None
    assert events.subscriber_count == 0


def test_broadcaster_publish_never_raises_without_a_loop():
    events = EventBroadcaster()
    events.publish({"type": "x"})  # no subscriber, no loop
    events.close()


def test_a_stream_opened_after_close_ends_at_once():
    async def scenario():
        events = EventBroadcaster()
        events.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(events.subscribe().__anext__(), timeout=2)
        assert events.subscriber_count == 0

    asyncio.run(scenario())


def test_sse_stream_formats_events():
    async def scenario():
        events = EventBroadcaster()
        events.bind_loop(asyncio.get_running_loop())
        stream = lacite_api.sse_stream(events)
        first = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        events.publish({"type": "mcp-call", "entry": {"tool": "ä"}})
        assert await first == 'data: {"type": "mcp-call", "entry": {"tool": "ä"}}\n\n'
        events.close()
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

    asyncio.run(scenario())
