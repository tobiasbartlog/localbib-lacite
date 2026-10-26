"""The sync engine: connections in, one ``.bib`` file per connection out.

Holds the connection list (read from and written to the Add-on's own
``SettingsApi`` key ``connections``), a status per connection, and the
background poll. Library change detection needs no core change
(docs/lacite/PLAN.md): every ~30 s the poll exports each connection's source
and compares the content hash with the last one; a Collection event from
``LibraryApi.on_change`` and the "Jetzt aktualisieren" button sync at once.
Paper events are deliberately not in the core; the poll covers them.

A sync writes only through :func:`lacite.sync.atomic_write`, only when the
bytes differ, never a foreign file without ``overwrite``, never a folder that
:func:`lacite.sync.check_folder` refuses, and never deletes anything. A
Collection that was deleted (contract 2.1 tombstone) pauses its connection:
the file stays as it was until the user picks a new source.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from lacite import connections as conns
from lacite import sync, texcheck

logger = logging.getLogger(__name__)

POLL_SECONDS = 30.0
EVENT_DEBOUNCE_SECONDS = 1.5

STATE_OK = "ok"
STATE_SYNCING = "syncing"
STATE_PAUSED = "paused"
STATE_ERROR = "error"
STATE_FOREIGN = "foreign"
STATE_NEW = "new"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SourceUnavailable(Exception):
    """The source cannot be read now; ``state`` says whether that pauses
    (deleted Collection) or is an error, ``code`` is the reason."""

    def __init__(self, state: str, code: str, name: str = "") -> None:
        super().__init__(code)
        self.state = state
        self.code = code
        self.name = name


class Engine:
    def __init__(
        self,
        library: Any,
        settings: Any,
        *,
        refused_roots: Iterable[str | Path] = (),
        clock: Callable[[], str] = _now,
    ) -> None:
        self.library = library
        self.settings = settings
        self.refused_roots = [r for r in refused_roots if r]
        self._clock = clock
        self._lock = threading.RLock()
        # the debounce timer has its own lock: ``on_event`` runs on the core's
        # request thread and must never wait for a running export
        self._timer_lock = threading.Lock()
        self._status: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._timer: Optional[threading.Timer] = None
        self._unsubscribe: Optional[Callable[[], None]] = None

    # --- the stored list -------------------------------------------------
    def connections(self) -> list[dict]:
        raw = self.settings.get(conns.SETTINGS_KEY) if self.settings is not None else None
        return conns.parse(raw)

    def _save(self, items: list[dict]) -> None:
        self.settings.set(conns.SETTINGS_KEY, conns.dumps(items))

    def get(self, conn_id: str) -> Optional[dict]:
        return next((c for c in self.connections() if c["id"] == conn_id), None)

    # --- sources -----------------------------------------------------------
    def _collections_supported(self) -> bool:
        return callable(getattr(self.library, "get_collection", None))

    def source_bibtex(self, source: dict) -> tuple[str, dict]:
        """``(bibtex, info)`` of a source; :class:`SourceUnavailable` when it
        cannot be read. ``info`` = ``{kind, id?, name, deleted}``."""
        if source.get("kind") != "collection":
            return self.library.export_bibtex(), {"kind": "library", "name": "Ganze Bibliothek",
                                                  "deleted": False}
        if not self._collections_supported():
            raise SourceUnavailable(STATE_ERROR, "error.lacite.collections_unsupported")
        found = self.library.get_collection(int(source["id"]))
        if found is None:
            raise SourceUnavailable(STATE_PAUSED, "error.lacite.collection_missing")
        info = {"kind": "collection", "id": found["id"], "name": found.get("name") or "",
                "deleted": bool(found.get("deleted"))}
        if info["deleted"]:
            raise SourceUnavailable(STATE_PAUSED, "error.lacite.collection_deleted", info["name"])
        return self.library.export_collection_bibtex(int(source["id"])), info

    def sources(self) -> dict:
        """What the dialog offers: the library's entry count and every live
        Collection with its count (deleted ones never)."""
        library_count = sync.count_entries(self.library.export_bibtex())
        cols = []
        lister = getattr(self.library, "list_collections", None)
        if callable(lister):
            for c in lister():
                if c.get("deleted"):
                    continue
                cols.append({"id": c["id"], "name": c.get("name") or "",
                             "item_count": c.get("item_count", 0),
                             "archived": bool(c.get("archived"))})
        return {"library": {"count": library_count}, "collections": cols,
                "collections_supported": callable(lister)}

    # --- syncing -------------------------------------------------------------
    def _status_of(self, conn_id: str) -> dict:
        return self._status.setdefault(conn_id, {
            "state": STATE_NEW, "error": None, "last_sync": None, "last_written": None,
            "entries": None, "keys": [], "hash": None, "stat": None, "source": None,
        })

    def sync(self, conn: dict, *, overwrite: bool = False, force: bool = False) -> Optional[dict]:
        """Bring one connection's file up to date; returns its status copy
        plus ``written`` (bool). Never raises.

        Syncs the connection as it is stored *now* (re-read under the lock):
        one edited meanwhile writes its new target, one removed meanwhile
        is skipped and answers ``None``."""
        return self._sync(conn["id"], overwrite=overwrite, force=force, exports=None)

    def _sync(self, conn_id: str, *, overwrite: bool, force: bool,
              exports: Optional[dict]) -> Optional[dict]:
        with self._lock:
            conn = self.get(conn_id)
            if conn is None:
                return None
            st = self._status_of(conn_id)
            written = False
            try:
                self._sync_into(st, conn, overwrite=overwrite, force=force, exports=exports)
                written = st.pop("_written", False)
            except OSError as exc:  # locked, read-only, folder gone mid-way
                logger.warning("LaCite: %s nicht geschrieben: %s", conn.get("filename"), exc)
                st.update(state=STATE_ERROR, error="error.lacite.write_failed")
            except Exception as exc:  # a broken source must never kill the poll
                logger.warning("LaCite: Abgleich von %s fehlgeschlagen: %s", conn.get("name"), exc)
                st.update(state=STATE_ERROR, error="error.lacite.sync_failed")
            out = self._public_status(st)
            out["written"] = written
            return out

    def _sync_into(self, st: dict, conn: dict, *, overwrite: bool, force: bool,
                   exports: Optional[dict]) -> None:
        refused = sync.check_folder(conn["folder"], refused_roots=self.refused_roots)
        if refused:
            st.update(state=STATE_ERROR, error=refused)
            return
        # one export per source and pass: connections sharing a source reuse it
        source_key = (conn["source"].get("kind"), conn["source"].get("id"))
        try:
            if exports is not None and source_key in exports:
                bib, info = exports[source_key]
            else:
                bib, info = self.source_bibtex(conn["source"])
                if exports is not None:
                    exports[source_key] = (bib, info)
        except SourceUnavailable as gone:
            st.update(state=gone.state, error=gone.code,
                      source={"kind": "collection", "id": conn["source"].get("id"),
                              "name": gone.name, "deleted": gone.state == STATE_PAUSED})
            return
        content = sync.target_content(bib)
        digest = sync.content_hash(content)
        path = sync.target_path(conn["folder"], conn["filename"])
        stat = _stat(path)
        st["source"] = info
        if (not force and not overwrite and st["state"] == STATE_OK
                and digest == st["hash"] and stat is not None and stat == st["stat"]):
            st["last_sync"] = self._clock()
            return  # nothing changed in the library or on disk
        current = sync.read_target(path)
        if sync.is_foreign(current) and not overwrite:
            st.update(state=STATE_FOREIGN, error="error.lacite.foreign_file")
            return
        if sync.needs_write(current, content):
            sync.atomic_write(path, content)
            st["last_written"] = self._clock()
            st["_written"] = True
        keys = sync.bib_keys(bib)
        st.update(state=STATE_OK, error=None, last_sync=self._clock(), hash=digest,
                  stat=_stat(path), entries=len(keys), keys=keys)

    def sync_all(self, *, force: bool = False) -> list[dict]:
        return self._sync_all(force=force)

    def _sync_all(self, *, force: bool = False, background: bool = False) -> list[dict]:
        """Every stored connection, each synced under the lock as it is
        stored then (see :meth:`sync`). A background pass stops between
        connections once :meth:`stop` was called."""
        exports: dict = {}
        out = []
        for c in self.connections():
            if background and self._stop.is_set():
                break
            result = self._sync(c["id"], overwrite=False, force=force, exports=exports)
            if result is not None:
                out.append({"id": c["id"], "name": c["name"], **result})
        return out

    @staticmethod
    def _public_status(st: dict) -> dict:
        return {k: st[k] for k in ("state", "error", "last_sync", "last_written", "entries", "source")}

    # --- views -----------------------------------------------------------------
    def view(self, conn: dict, *, with_texcheck: bool = True) -> dict:
        with self._lock:
            st = self._status_of(conn["id"])
            out = {**conn, "status": self._public_status(st)}
            keys = list(st["keys"])
        if with_texcheck and out["status"]["state"] in (STATE_OK, STATE_FOREIGN, STATE_PAUSED):
            out["document"] = texcheck.check(conn["folder"], conn["filename"], keys)
            if out["status"]["state"] != STATE_OK:
                out["document"]["missing_keys"] = []  # no source keys to compare with
        else:
            out["document"] = None
        return out

    def views(self) -> list[dict]:
        return [self.view(c) for c in self.connections()]

    def _reference(self, key: str) -> Optional[dict]:
        try:
            return self.library.get_reference(key)
        except Exception as exc:  # one broken lookup must not lose the list
            logger.warning("LaCite: %s nicht nachgeschlagen: %s", key, exc)
            return None

    def _library_bib(self) -> str:
        try:
            return self.library.export_bibtex() or ""
        except Exception as exc:
            logger.warning("LaCite: Bibliothek nicht exportiert: %s", exc)
            return ""

    def missing(self, conn: dict) -> dict:
        """Where the connection's missing cited keys can come from; reads
        only, writes nothing, never raises for the disk.

        Each key is ``library`` (the library holds it — the source does not,
        e.g. an Item outside the Collection), ``found`` (an entry with exactly
        this key in another ``.bib`` of the project: the named ones first,
        then the others up to three levels, never the connection's own file)
        or ``not_found``. ``bibtex`` holds the entries of the first two
        groups — ``found`` cut from the old file, ``library`` from the
        library's own export, so the core's import matches it instead of
        creating it again — ready for ``LocalBib.importBibtex``. A ``found``
        key carries the old entry's ``title`` and ``entry_type`` for the list,
        a ``not_found`` one a ``suggestion``: a library key it is probably a
        typo of (:func:`texcheck.suggest_key`), else ``None``. Missing keys
        are known only while the connection is ``ok`` (``available``)."""
        view = self.view(conn)
        doc = view.get("document") or {}
        available = view["status"]["state"] == STATE_OK and bool(doc)
        keys = list(doc.get("missing_keys") or []) if available else []
        source = conn["source"]
        target = sync.target_path(conn["folder"], conn["filename"])
        named = texcheck.resolve_named(conn["folder"], doc.get("bib_files") or [],
                                       main_tex=doc.get("main_tex"))
        named = [(n, p) for n, p in named if not texcheck.same_file(p, target)]
        refs = {key: self._reference(key) for key in keys}
        lookup = [k for k in keys if refs[k] is None]
        bibs = (texcheck.read_bibs(texcheck.bib_paths(conn["folder"], named, exclude=target))
                if lookup else ())
        found = texcheck.find_entries(lookup, bibs)
        old_files = dict(found["found"])  # key -> old .bib, before the library's own join in
        in_library = [k for k in keys if refs[k] is not None]
        nowhere = [k for k in lookup if k not in old_files]
        library_bib = self._library_bib() if (in_library or nowhere) else ""
        if in_library:
            own = texcheck.find_entries(in_library, [("", library_bib)])
            for key in own["found"]:
                found["found"][key] = ""
                found["entries"][key] = own["entries"][key]
        library_keys = sync.bib_keys(library_bib) if nowhere else []
        items = []
        for key in keys:
            ref = refs[key]
            if ref is not None:
                items.append({"key": key, "status": "library", "file": None,
                              "item_id": ref.get("id"), "title": ref.get("title") or "",
                              "entry_type": "", "suggestion": None})
            elif key in old_files:
                info = texcheck.entry_info(found["entries"].get(key, ""))
                items.append({"key": key, "status": "found", "file": old_files[key],
                              "item_id": None, "title": info["title"],
                              "entry_type": info["type"], "suggestion": None})
            else:
                items.append({"key": key, "status": "not_found", "file": None,
                              "item_id": None, "title": "", "entry_type": "",
                              "suggestion": texcheck.suggest_key(key, library_keys)})
        counts = {s: sum(1 for i in items if i["status"] == s) for s in ("found", "library", "not_found")}
        return {
            "id": conn["id"],
            "available": available,
            "collection_id": source.get("id") if source.get("kind") == "collection" else None,
            "keys": items,
            "counts": counts,
            "named_bib_files": [n for n, _ in named],
            "bibtex": texcheck.excerpt(found, keys),
        }

    # --- writes to the list ----------------------------------------------------
    def _check(self, conn: dict, others: list[dict]) -> None:
        conns.check_unique(conn, others)
        refused = sync.check_folder(conn["folder"], refused_roots=self.refused_roots)
        if refused:
            raise conns.InvalidConnection(refused, "folder")
        if conn["source"]["kind"] == "collection":
            if not self._collections_supported():
                raise conns.InvalidConnection("error.lacite.collections_unsupported", "source")
            found = self.library.get_collection(conn["source"]["id"])
            if found is None or found.get("deleted"):
                raise conns.InvalidConnection("error.lacite.collection_missing", "source")

    def _foreign(self, conn: dict) -> bool:
        try:
            current = sync.read_target(sync.target_path(conn["folder"], conn["filename"]))
        except OSError:  # a folder of that name, locked, no permission
            raise conns.InvalidConnection("error.lacite.target_unreadable", "filename") from None
        return sync.is_foreign(current)

    def create(self, raw: dict, *, overwrite: bool = False) -> dict:
        """Store and sync a new connection. A foreign target answers
        ``{"needs_confirm": "foreign_file", "file": path}`` and stores
        nothing until it comes back with ``overwrite``."""
        with self._lock:
            conn = conns.normalize({**raw, "id": None})
            items = self.connections()
            self._check(conn, items)
            if self._foreign(conn) and not overwrite:
                return {"needs_confirm": "foreign_file",
                        "file": str(sync.target_path(conn["folder"], conn["filename"]))}
            items.append(conn)
            self._save(items)
            result = self.sync(conn, overwrite=overwrite, force=True)
            return {"connection": self.view(conn), "sync": result}

    def update(self, conn_id: str, raw: dict, *, overwrite: bool = False) -> Optional[dict]:
        with self._lock:
            items = self.connections()
            old = next((c for c in items if c["id"] == conn_id), None)
            if old is None:
                return None
            merged = {**old, **{k: v for k, v in raw.items() if v is not None}}
            conn = conns.normalize(merged, existing_id=conn_id)
            self._check(conn, items)
            moved = (conn["folder"], conn["filename"]) != (old["folder"], old["filename"])
            if moved and self._foreign(conn) and not overwrite:
                return {"needs_confirm": "foreign_file",
                        "file": str(sync.target_path(conn["folder"], conn["filename"]))}
            items = [conn if c["id"] == conn_id else c for c in items]
            self._save(items)
            if moved or conn["source"] != old["source"]:
                self._status.pop(conn_id, None)
            result = self.sync(conn, overwrite=overwrite, force=True)
            return {"connection": self.view(conn), "sync": result}

    def remove(self, conn_id: str) -> bool:
        """Drop the connection only — its file stays where it is."""
        with self._lock:
            items = self.connections()
            kept = [c for c in items if c["id"] != conn_id]
            if len(kept) == len(items):
                return False
            self._save(kept)
            self._status.pop(conn_id, None)
            return True

    # --- background ------------------------------------------------------------
    def on_event(self, event: dict) -> None:
        """``LibraryApi.on_change`` callback: quick, never raises — schedules
        one debounced sync of every connection for a Collection event."""
        try:
            if not isinstance(event, dict) or event.get("type") != "collection":
                return
            with self._timer_lock:
                if self._stop.is_set():
                    return
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = threading.Timer(EVENT_DEBOUNCE_SECONDS, self._quiet_sync)
                self._timer.daemon = True
                self._timer.start()
        except Exception as exc:
            logger.warning("LaCite: Ereignis nicht verarbeitet: %s", exc)

    def _quiet_sync(self) -> None:
        if not self._stop.is_set():
            self._sync_all(background=True)

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self._sync_all(background=True)
            except Exception as exc:  # keep polling
                logger.warning("LaCite: Abgleich fehlgeschlagen: %s", exc)

    def start(self, interval: float = POLL_SECONDS) -> None:
        on_change = getattr(self.library, "on_change", None)
        if callable(on_change):
            self._unsubscribe = on_change(self.on_event)
        self._thread = threading.Thread(target=self._run, args=(interval,),
                                        name="lacite-sync", daemon=True)
        self._thread.start()
        threading.Thread(target=self._quiet_sync, name="lacite-first-sync", daemon=True).start()

    def stop(self) -> None:
        """Blocks up to 5 s for the poll thread: call it off the event loop."""
        self._stop.set()
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                pass
            self._unsubscribe = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def _stat(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
