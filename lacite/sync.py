"""The ``.bib`` rules and the one write LaCite does.

Pure rules (no I/O): :func:`target_content` (header + the library's BibTeX),
:func:`count_entries`, :func:`bib_keys`, :func:`is_lacite_file`. Small file
system steps, each named for what it does: :func:`check_folder` (may this
folder be written?), :func:`read_target`, :func:`atomic_write`.

The write rules (docs/lacite/PLAN.md): write atomically (a temp file in the
same folder + ``os.replace``), only when the bytes differ from the bytes on
disk, never any other file, never delete, never create a folder. A target
that exists without LaCite's header line is a *foreign* file: the caller asks
the user before it is overwritten.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional

#: The first line of every file LaCite writes; it is how LaCite recognises
#: its own file again (ASCII, so every BibTeX/biber reads it as a comment).
HEADER_MARK = "% LaCite"
HEADER = (
    "% LaCite (LocalBib) haelt diese Datei aktuell. Aenderungen hier werden "
    "beim naechsten Abgleich ueberschrieben - bearbeite die Eintraege in LocalBib."
)

_ENTRY_RE = re.compile(r"^\s*@\s*([A-Za-z]+)\s*[{(]\s*([^,\s]+)\s*,", re.MULTILINE)
_SKIP_TYPES = {"string", "preamble", "comment"}

#: Files a settings folder holds next to each other: a folder that contains
#: them (or lies inside one that does) is LocalBib's own, never a LaTeX project.
_SETTINGS_MARKERS = ("plugins.json", "llm.json")


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def target_content(bibtex: str) -> str:
    """What the target file holds: the header line, a blank line, the
    entries, exactly one trailing newline."""
    body = (bibtex or "").strip("\n")
    return HEADER + "\n\n" + (body + "\n" if body.strip() else "")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bib_keys(bibtex: str) -> list[str]:
    """The entry keys of a ``.bib`` text in order (``@string`` & co. skipped)."""
    return [m.group(2) for m in _ENTRY_RE.finditer(bibtex or "")
            if m.group(1).lower() not in _SKIP_TYPES]


def count_entries(bibtex: str) -> int:
    return len(bib_keys(bibtex))


def is_lacite_file(first_bytes: bytes) -> bool:
    """Whether a file's first line is LaCite's header (BOM tolerated)."""
    text = first_bytes.decode("utf-8", errors="replace").lstrip("﻿")
    return text.startswith(HEADER_MARK)


# ---------------------------------------------------------------------------
# Folder rules
# ---------------------------------------------------------------------------

def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved(path: str | Path) -> Optional[Path]:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def check_folder(folder: str, *, refused_roots: Iterable[str | Path] = ()) -> Optional[str]:
    """``None`` when LaCite may keep a file in ``folder``, else the error code.

    Refused: a relative or missing path, a file, a drive/file system root,
    anything inside a refused root (the library base folder, the Add-on's
    own folders — the caller passes them) and anything inside LocalBib's
    settings folder (recognised by ``plugins.json`` + ``llm.json``; its
    ``logs`` and backups live there). The re-implementation of the core's
    ``backup.check_folder`` idea, never an import of it."""
    raw = (folder or "").strip()
    if not raw:
        return "error.lacite.folder_required"
    if not Path(raw).expanduser().is_absolute():
        return "error.lacite.folder_not_absolute"
    path = _resolved(raw)
    if path is None or not path.exists():
        return "error.lacite.folder_missing"
    if not path.is_dir():
        return "error.lacite.folder_not_a_folder"
    if path.parent == path:
        return "error.lacite.folder_is_root"
    for root in refused_roots:
        if not root:
            continue
        resolved = _resolved(root)
        if resolved is not None and _inside(path, resolved):
            return "error.lacite.folder_inside_localbib"
    for candidate in (path, *path.parents):
        if all((candidate / marker).is_file() for marker in _SETTINGS_MARKERS):
            return "error.lacite.folder_inside_localbib"
    return None


# ---------------------------------------------------------------------------
# The target file
# ---------------------------------------------------------------------------

def target_path(folder: str, filename: str) -> Path:
    return Path(folder).expanduser() / filename


def read_target(path: Path) -> Optional[bytes]:
    """The target's bytes, ``None`` when it does not exist. Any other
    ``OSError`` (a folder of that name, locked, no permission) is raised."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def target_exists(path: Path) -> bool:
    """Whether something already sits at ``path`` (without reading it)."""
    try:
        return path.exists()
    except OSError:
        return True


def needs_write(current: Optional[bytes], content: str) -> bool:
    return current != content.encode("utf-8")


def is_foreign(current: Optional[bytes]) -> bool:
    """An existing file LaCite did not write (no header as its first line)."""
    return current is not None and not is_lacite_file(current[:256])


def atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically: a temp file in the same
    folder, flushed, then ``os.replace``. The folder must exist — LaCite never
    creates one. The temp file is removed when anything fails, so the folder
    never keeps more than the one target."""
    folder = path.parent
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    fd, tmp = tempfile.mkstemp(prefix=".lacite-", suffix=".tmp", dir=str(folder))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
