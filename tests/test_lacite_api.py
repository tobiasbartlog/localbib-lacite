"""The Add-on as the core would mount it — activate()/deactivate() and the HTTP routes, fakes only."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugin_api.testing import make_api

import lacite
from lacite import desktop
from lacite_fakes import MANIFEST, seeded_library

P = "/api/plugins/lacite"


@pytest.fixture
def addon(library):
    api = make_api(MANIFEST.permissions, library=library)
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    app = FastAPI()
    for router in api.routes.routers:
        app.include_router(router)
    client = TestClient(app)
    client.api = api
    client.plugin = plugin
    yield client
    asyncio.run(plugin.deactivate())


def test_manifest():
    assert MANIFEST.id == "lacite"
    assert {p.value for p in MANIFEST.permissions} == {"library.read", "library.write", "settings.core", "files",
                                                    "storage"}
    assert MANIFEST.nav.route == "/lacite" and MANIFEST.nav.view == "lacite.home"


def test_activate_registers_nav_router_and_listener_and_deactivate_undoes_it(library):
    api = make_api(MANIFEST.permissions, library=library)
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    assert [n.route for n in api.ui.nav_items] == ["/lacite"]
    assert len(api.routes.routers) == 1
    assert all(r.path.startswith(P) for r in api.routes.routers[0].routes)
    assert library.listener_count == 1
    asyncio.run(plugin.deactivate())
    assert library.listener_count == 0 and plugin.engine is None


def test_activate_without_library_read_fails_loudly():
    api = make_api([])
    with pytest.raises(RuntimeError):
        asyncio.run(lacite.LaCitePlugin().activate(api))


def test_refused_roots_name_the_library_and_the_plugins_folder():
    roots = lacite.refused_roots("/home/u/Literatur")
    assert roots[0] == "/home/u/Literatur"
    assert str(lacite.BUNDLE_ROOT) in roots and str(lacite.BUNDLE_ROOT.parent) in roots


def test_connection_lifecycle_over_http(addon, project):
    assert addon.get(f"{P}/connections").json() == {"connections": []}
    check = addon.post(f"{P}/check-folder", json={"folder": str(project)}).json()
    assert check["ok"] and check["name"] == "Dissertation"
    assert check["document"]["main_tex"] == "main.tex" and check["exists"] is False

    created = addon.post(f"{P}/connections", json={"folder": str(project)}).json()
    conn = created["connection"]
    assert conn["status"]["state"] == "ok" and (project / "literatur.bib").exists()

    listed = addon.get(f"{P}/connections").json()["connections"]
    assert [c["id"] for c in listed] == [conn["id"]]

    synced = addon.post(f"{P}/connections/{conn['id']}/sync").json()
    assert synced["sync"]["written"] is False

    renamed = addon.put(f"{P}/connections/{conn['id']}", json={"name": "Diss"}).json()
    assert renamed["connection"]["name"] == "Diss"

    gone = addon.delete(f"{P}/connections/{conn['id']}").json()
    assert gone["file_kept"] is True and (project / "literatur.bib").exists()
    assert addon.delete(f"{P}/connections/{conn['id']}").status_code == 404


def test_errors_are_codes(addon, tmp_path):
    bad = addon.post(f"{P}/connections", json={"folder": str(tmp_path / "nope")})
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "error.lacite.folder_missing"
    assert addon.post(f"{P}/connections/zzz/sync").json()["detail"]["code"] == \
        "error.lacite.connection_not_found"
    check = addon.post(f"{P}/check-folder", json={"folder": "relative"}).json()
    assert check == {"folder": "relative", "ok": False, "error": "error.lacite.folder_not_absolute",
                     "name": "relative"}


def test_foreign_file_needs_confirmation(addon, project):
    (project / "literatur.bib").write_text("@misc{mine,}\n")
    first = addon.post(f"{P}/connections", json={"folder": str(project)}).json()
    assert first["needs_confirm"] == "foreign_file"
    assert addon.post(f"{P}/check-folder", json={"folder": str(project)}).json()["exists"] is True
    second = addon.post(f"{P}/connections", json={"folder": str(project), "overwrite": True}).json()
    assert second["connection"]["status"]["state"] == "ok"


def test_sources_and_status(addon, library):
    library.add_collection("Kapitel 2", refs=["Smith2020"])
    sources = addon.get(f"{P}/sources").json()
    assert sources["library"]["count"] == 3
    assert sources["collections"][0]["name"] == "Kapitel 2"
    status = addon.get(f"{P}/status").json()
    assert status["version"] == MANIFEST.version and status["mcp"] is None


def test_pick_folder_without_a_dialog_is_a_code(addon, monkeypatch):
    def unavailable(initial=""):
        raise desktop.PickerUnavailable("headless")
    monkeypatch.setattr(desktop, "pick_folder", unavailable)
    resp = addon.post(f"{P}/pick-folder", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "error.lacite.picker_unavailable"
    monkeypatch.setattr(desktop, "pick_folder", lambda initial="": "/chosen")
    assert addon.post(f"{P}/pick-folder").json() == {"folder": "/chosen"}


def test_pick_folder_tries_the_pickers_in_turn():
    def broken(initial):
        raise RuntimeError("no display")
    assert desktop.pick_folder(pickers=[broken, lambda initial: "/ok"]) == "/ok"
    assert desktop.pick_folder(pickers=[lambda initial: None]) is None  # cancelled
    with pytest.raises(desktop.PickerUnavailable):
        desktop.pick_folder(pickers=[broken])
    with pytest.raises(desktop.PickerUnavailable):
        desktop.pick_folder(pickers=[])


def test_open_folder(addon, project, monkeypatch):
    conn = addon.post(f"{P}/connections", json={"folder": str(project)}).json()["connection"]
    opened = []
    monkeypatch.setattr(desktop, "open_folder", lambda folder: opened.append(folder))
    assert addon.post(f"{P}/connections/{conn['id']}/open-folder").json() == {"opened": str(project)}
    assert opened == [str(project)]

    def missing(folder):
        raise FileNotFoundError(folder)
    monkeypatch.setattr(desktop, "open_folder", missing)
    assert addon.post(f"{P}/connections/{conn['id']}/open-folder").json()["detail"]["code"] == \
        "error.lacite.folder_missing"


def test_mcp_over_http(addon):
    init = addon.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2"}}})
    assert init.status_code == 200 and init.headers["content-type"].startswith("application/json")
    assert init.json()["result"]["serverInfo"]["name"] == "localbib-lacite"
    note = addon.post(f"{P}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.content == b""
    tools = addon.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    assert "find_passages" in [t["name"] for t in tools["result"]["tools"]]
    call = addon.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "find_passages", "arguments": {"query": "printed clay"}}}).json()
    assert call["result"]["structuredContent"]["results"][0]["cite"] == "\\cite[S.~347]{Smith2020}"
    status = addon.get(f"{P}/status").json()
    assert status["mcp"]["name"] == "claude-code"
    assert addon.post(f"{P}/mcp", content=b"{nope").status_code == 400
    assert addon.get(f"{P}/mcp").status_code == 405


def test_mcp_sync_bib_syncs_the_connections(addon, project, library):
    addon.post(f"{P}/connections", json={"folder": str(project)})
    library.add({"citekey": "ViaAgent", "title": "x"})
    call = addon.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "sync_bib"}}).json()
    (conn,) = call["result"]["structuredContent"]["connections"]
    assert conn["written"] is True and conn["name"] == "Dissertation"
    assert "ViaAgent" in (project / "literatur.bib").read_text(encoding="utf-8")


def test_mcp_add_by_doi_without_write_permission():
    perms = [p for p in MANIFEST.permissions if p.value != "library.write"]
    api = make_api(perms, library=seeded_library())
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    try:
        app = FastAPI()
        app.include_router(api.routes.routers[0])
        resp = TestClient(app).post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                      "params": {"name": "add_by_doi",
                                                                 "arguments": {"doi": "10.1/x"}}})
        assert resp.status_code == 200 and resp.json()["result"]["isError"] is True
    finally:
        asyncio.run(plugin.deactivate())
