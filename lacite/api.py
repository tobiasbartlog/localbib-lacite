"""HTTP routes of LaCite, mounted under ``/api/plugins/lacite/``.

  GET    /status                       Bundle version, MCP "Zuletzt verbunden", poll interval
  GET    /sources                      library entry count + live Collections (dialog step 2)
  GET    /connections                  every connection with status and the "Im Dokument" check
  POST   /check-folder                 dialog step 1: may this folder be used? what .tex is there?
  POST   /connections                  create + first sync (a foreign file asks first)
  PUT    /connections/{id}             edit (name, folder, file name, source)
  DELETE /connections/{id}             "Trennen": the connection goes, the file stays
  POST   /connections/{id}/sync        "Jetzt aktualisieren" (``overwrite`` after asking)
  POST   /sync                         sync every connection now
  POST   /connections/{id}/open-folder the project folder in the file manager
  POST   /pick-folder                  the native folder dialog (or ``error.lacite.picker_unavailable``)
  POST   /mcp                          the MCP endpoint (Streamable HTTP, JSON answers)

Errors are codes (``{"detail": {"code": "error.lacite.…"}}``), never sentences.
Blocking work (disk, library export, dialogs) runs in sync handlers, i.e. in
FastAPI's threadpool, never on the event loop.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from lacite import connections as conns
from lacite import desktop, engine as engine_mod, sync, texcheck
from lacite.mcp import PARSE_ERROR, McpServer, _error

PREFIX = "/api/plugins/lacite"


class ConnectionIn(BaseModel):
    name: Optional[str] = None
    folder: Optional[str] = None
    filename: Optional[str] = None
    source: Optional[dict] = None
    overwrite: bool = False


class FolderIn(BaseModel):
    folder: str = ""
    filename: Optional[str] = None


class SyncIn(BaseModel):
    overwrite: bool = False


class PickIn(BaseModel):
    initial: str = ""


def _fail(status: int, code: str, **params: Any) -> HTTPException:
    detail: dict = {"code": code}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


def build_router(engine: "engine_mod.Engine", mcp: McpServer, *, version: str) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def _found(conn_id: str) -> dict:
        conn = engine.get(conn_id)
        if conn is None:
            raise _fail(404, "error.lacite.connection_not_found")
        return conn

    @router.get("/status")
    def status() -> dict:
        return {"version": version, "mcp": mcp.last_client, "poll_seconds": engine_mod.POLL_SECONDS,
                "connections": len(engine.connections())}

    @router.get("/sources")
    def sources() -> dict:
        return engine.sources()

    @router.get("/connections")
    def list_connections() -> dict:
        return {"connections": engine.views()}

    @router.post("/check-folder")
    def check_folder(body: FolderIn) -> dict:
        folder = body.folder.strip()
        error = sync.check_folder(folder, refused_roots=engine.refused_roots)
        out: dict = {"folder": folder, "ok": error is None, "error": error,
                     "name": conns.folder_name(folder)}
        if error is None:
            try:
                filename = conns.normalize_filename(body.filename)
            except conns.InvalidConnection:
                filename = conns.DEFAULT_FILENAME
            out["document"] = texcheck.check(folder, filename, [])
            out["document"]["missing_keys"] = []
            out["exists"] = sync.target_exists(sync.target_path(folder, filename))
        return out

    def _write(result: Optional[dict]) -> dict:
        if result is None:
            raise _fail(404, "error.lacite.connection_not_found")
        return result

    @router.post("/connections")
    def create_connection(body: ConnectionIn) -> dict:
        try:
            return engine.create(body.model_dump(exclude={"overwrite"}), overwrite=body.overwrite)
        except conns.InvalidConnection as exc:
            raise _fail(422, exc.code, field=exc.field) from None

    @router.put("/connections/{conn_id}")
    def update_connection(conn_id: str, body: ConnectionIn) -> dict:
        _found(conn_id)
        try:
            return _write(engine.update(conn_id, body.model_dump(exclude={"overwrite"}),
                                        overwrite=body.overwrite))
        except conns.InvalidConnection as exc:
            raise _fail(422, exc.code, field=exc.field) from None

    @router.delete("/connections/{conn_id}")
    def delete_connection(conn_id: str) -> dict:
        conn = _found(conn_id)
        engine.remove(conn_id)
        return {"removed": conn_id, "file": str(sync.target_path(conn["folder"], conn["filename"])),
                "file_kept": True}

    @router.post("/connections/{conn_id}/sync")
    def sync_connection(conn_id: str, body: Optional[SyncIn] = None) -> dict:
        result = engine.sync(_found(conn_id), overwrite=bool(body and body.overwrite), force=True)
        conn = engine.get(conn_id)
        if result is None or conn is None:  # removed meanwhile
            raise _fail(404, "error.lacite.connection_not_found")
        return {"sync": result, "connection": engine.view(conn)}

    @router.post("/sync")
    def sync_all() -> dict:
        return {"results": engine.sync_all(force=True)}

    @router.post("/connections/{conn_id}/open-folder")
    def open_folder(conn_id: str) -> dict:
        conn = _found(conn_id)
        try:
            desktop.open_folder(conn["folder"])
        except FileNotFoundError:
            raise _fail(404, "error.lacite.folder_missing") from None
        except OSError:
            raise _fail(409, "error.lacite.open_unavailable") from None
        return {"opened": conn["folder"]}

    @router.post("/pick-folder")
    def pick_folder(body: Optional[PickIn] = None) -> dict:
        try:
            chosen = desktop.pick_folder(body.initial if body else "")
        except desktop.PickerBusy:
            raise _fail(409, "error.lacite.picker_busy") from None
        except desktop.PickerUnavailable:
            raise _fail(409, "error.lacite.picker_unavailable") from None
        return {"folder": chosen}

    # --- MCP (Streamable HTTP, stateless) ------------------------------------
    @router.post("/mcp")
    async def mcp_post(request: Request) -> Response:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            return JSONResponse(_error(None, PARSE_ERROR, "Parse error"), status_code=400)
        answer = await run_in_threadpool(mcp.handle_payload, payload)
        if answer is None:
            return Response(status_code=202)
        return JSONResponse(answer)

    @router.get("/mcp")
    def mcp_get() -> Response:
        # No server-initiated stream: the spec's answer is 405.
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.delete("/mcp")
    def mcp_delete() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    return router
