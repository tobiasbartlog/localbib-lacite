"""The connection list: parse, validate and serialise (pure, no I/O).

One **connection** = a LaTeX project folder + a source + a file name. The
list lives as ONE compact JSON string under the Add-on's own ``SettingsApi``
key ``connections`` (``SettingsApi`` stores no lists). Shape of one entry::

    {"id": "c1a2b3c4", "name": "Dissertation", "folder": "C:\\\\...\\\\Diss",
     "filename": "literatur.bib",
     "source": {"kind": "library"} | {"kind": "collection", "id": 12}}

Many connections are allowed; several may share a source, one folder may
hold several files with different names — only the exact pair folder + file
name must be unique, because LaCite writes exactly one file per connection.
"""

from __future__ import annotations

import json
import ntpath
import posixpath
import re
import secrets
from typing import Any, Optional

SETTINGS_KEY = "connections"
DEFAULT_FILENAME = "literatur.bib"
NAME_MAX = 80

_FILENAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]+\.bib$", re.IGNORECASE)


class InvalidConnection(ValueError):
    """A connection that cannot be stored; ``code`` is the UI error code."""

    def __init__(self, code: str, field: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.field = field


def new_id() -> str:
    return "c" + secrets.token_hex(4)


def folder_name(folder: str) -> str:
    """The last path segment of ``folder`` (Windows or POSIX spelling) — the
    default name of a connection."""
    trimmed = (folder or "").strip().rstrip("\\/")
    base = ntpath.basename(trimmed) if "\\" in trimmed else posixpath.basename(trimmed)
    return base or trimmed


def normalize_source(raw: Any) -> dict:
    """``None``/``{}``/``{"kind": "library"}`` -> the whole library."""
    if raw is None:
        return {"kind": "library"}
    if not isinstance(raw, dict):
        raise InvalidConnection("error.lacite.source_invalid", "source")
    if raw.get("kind") in (None, "library"):
        return {"kind": "library"}
    if raw.get("kind") != "collection":
        raise InvalidConnection("error.lacite.source_invalid", "source")
    try:
        cid = int(raw.get("id"))
    except (TypeError, ValueError):
        raise InvalidConnection("error.lacite.source_invalid", "source") from None
    if cid <= 0:
        raise InvalidConnection("error.lacite.source_invalid", "source")
    return {"kind": "collection", "id": cid}


def normalize_filename(raw: Any) -> str:
    name = str(raw or "").strip() or DEFAULT_FILENAME
    if not name.lower().endswith(".bib"):
        name += ".bib"
    if not _FILENAME_RE.match(name) or name.startswith(".") or name in (".bib",):
        raise InvalidConnection("error.lacite.filename_invalid", "filename")
    return name


def normalize(raw: Any, *, existing_id: Optional[str] = None) -> dict:
    """One connection from user input, or :class:`InvalidConnection`.

    The folder is taken as given (trimmed); whether it may be written is
    ``sync.check_folder``'s question, which needs the file system."""
    if not isinstance(raw, dict):
        raise InvalidConnection("error.lacite.connection_invalid")
    folder = str(raw.get("folder") or "").strip()
    if not folder:
        raise InvalidConnection("error.lacite.folder_required", "folder")
    name = str(raw.get("name") or "").strip() or folder_name(folder)
    return {
        "id": existing_id or str(raw.get("id") or "") or new_id(),
        "name": name[:NAME_MAX],
        "folder": folder,
        "filename": normalize_filename(raw.get("filename")),
        "source": normalize_source(raw.get("source")),
    }


def _same_target(a: dict, b: dict) -> bool:
    def norm(folder: str) -> str:
        return folder.strip().rstrip("\\/").replace("\\", "/").casefold()
    return norm(a["folder"]) == norm(b["folder"]) and a["filename"].casefold() == b["filename"].casefold()


def check_unique(conn: dict, others: list[dict]) -> None:
    """Refuse a second connection to the same file (folder + name)."""
    for other in others:
        if other["id"] != conn["id"] and _same_target(conn, other):
            raise InvalidConnection("error.lacite.target_taken", "filename")


def parse(text: Any) -> list[dict]:
    """The stored list. Unreadable text or entries are dropped, never raised:
    a broken setting must not take the Add-on down (the file stays anyway)."""
    if not text:
        return []
    try:
        data = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in data:
        try:
            conn = normalize(raw)
        except InvalidConnection:
            continue
        if conn["id"] in seen:
            continue
        seen.add(conn["id"])
        out.append(conn)
    return out


def dumps(connections: list[dict]) -> str:
    """Compact JSON, the stored form."""
    keys = ("id", "name", "folder", "filename", "source")
    return json.dumps([{k: c[k] for k in keys} for c in connections],
                      ensure_ascii=False, separators=(",", ":"))
