"""LaCite — LocalBib's library inside any LaTeX editor, shipped as a Bundle under ``plugins/lacite``.

Two ways in, both editor-agnostic (docs/lacite/PLAN.md):

1. a ``.bib`` file LaCite keeps current inside the user's LaTeX project
   folders (one file per *connection*: folder + source + file name);
2. an MCP endpoint (``POST /api/plugins/lacite/mcp``) so writing agents can
   search the library, read passages with page numbers and cite them.

``activate()`` builds the sync engine over ``api.library`` and the Add-on's
own ``SettingsApi`` key ``connections``, subscribes to the core's change feed,
starts the background poll (~30 s), binds the live session (``SessionLog`` +
``EventBroadcaster`` on the server loop), opens the Schlussberichte
(``ReportStore`` in the Add-on's own database ``reports``, contract 2.7; in
memory on an older core) and registers the router and the nav item. ``deactivate()`` stops the poll, unsubscribes, unhooks the session log
and ends open live streams; the core registry drops the nav item and unmounts
the router (residue-free). LaCite writes exactly one
file per connection and never deletes anything.

P3 boundary: this package imports **only** from ``plugin_api`` (plus stdlib
and FastAPI for its router), never from the core or another Add-on. Enforced
by the Bundle's ``.importlinter`` (contract ``lacite-only-plugin-api``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from plugin_api import PluginApi, load_manifest

BUNDLE_ROOT = Path(__file__).resolve().parent.parent


def refused_roots(core_base_dir: str = "") -> list[str]:
    """Folders a connection may never point into: the library base folder
    and the Add-on folders (a Dev-Suchpfad's ``plugins/``, an installed
    Bundle's ``<plugins>/lacite/<version>`` and the plugins folder above it)."""
    roots = [core_base_dir, str(BUNDLE_ROOT)]
    parent = BUNDLE_ROOT.parent
    roots.append(str(parent.parent if parent.name == "lacite" else parent))
    return [r for r in roots if r]


class LaCitePlugin:
    manifest = load_manifest(BUNDLE_ROOT)

    def __init__(self) -> None:
        self.engine: Optional[Any] = None
        self.mcp: Optional[Any] = None
        self.session: Optional[Any] = None
        self.events: Optional[Any] = None
        self.reports: Optional[Any] = None
        self._reports_db: Optional[Any] = None

    async def activate(self, api: PluginApi) -> None:
        from lacite.engine import Engine
        from lacite.events import EventBroadcaster
        from lacite.mcp import McpServer
        from lacite.report_store import ReportStore
        from lacite.session_log import SessionLog

        if api.library is None:
            raise RuntimeError("LaCite braucht die Berechtigung library.read")
        settings = api.settings
        core = settings.core() if settings is not None else None
        self.engine = Engine(api.library, settings,
                             refused_roots=refused_roots(core.base_dir if core is not None else ""))
        # The live session: MCP calls (threadpool) -> ring buffer -> SSE (event loop).
        self.events = EventBroadcaster()
        self.events.bind_loop(asyncio.get_running_loop())
        self.session = SessionLog(publish=self.events.publish)
        # The Schlussberichte: our own database (contract 2.7), else in memory.
        storage = getattr(api, "storage", None)
        if storage is not None:
            self._reports_db = storage.open_plugin_db("reports")
            self.reports = ReportStore(self._reports_db)
        else:
            self.reports = ReportStore.in_memory()
        session = self.session
        self.mcp = McpServer(api.library, sync_all=lambda: self.engine.sync_all(force=True),
                             version=self.manifest.version, log=self.session.record,
                             reports=self.reports, connections=lambda: self.engine.connections(),
                             lookups=lambda: session.count_since("submit_report"))
        router = _build_router(self)
        if router is not None:
            api.routes.register_router(router)
        if self.manifest.nav is not None:
            api.ui.register_nav_item(self.manifest.nav)
        self.engine.start()

    async def deactivate(self) -> None:
        if self.engine is not None:
            # stop() joins the poll thread: never block the event loop on it
            await asyncio.to_thread(self.engine.stop)
        if self.mcp is not None:
            self.mcp.log = None
        if self.session is not None:
            self.session.publish = None
        if self.events is not None:
            self.events.close()  # open live windows' streams end
        if self._reports_db is not None:
            self._reports_db.close()
        self.engine = None
        self.reports = None
        self._reports_db = None
        self.mcp = None
        self.session = None
        self.events = None


def _build_router(plugin: "LaCitePlugin") -> Any:
    try:
        from lacite.api import build_router
    except ModuleNotFoundError as exc:
        if exc.name not in ("fastapi", "pydantic", "starlette"):
            raise
        return None  # contract-only test run: no web framework installed
    return build_router(plugin.engine, plugin.mcp, version=plugin.manifest.version,
                        session=plugin.session, events=plugin.events, reports=plugin.reports)


# The core registry loads this module and reads ``plugin``.
plugin = LaCitePlugin()
