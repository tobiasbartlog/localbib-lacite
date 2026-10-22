"""What the LaTeX project does with LaCite's file (pure rules + one folder scan).

For one connection: is the ``.bib`` included (``\\bibliography{x}`` for
BibTeX/natbib, ``\\addbibresource{x.bib}`` for biblatex)? Does the project use
biblatex (so the snippet to show is the biblatex one)? Which cited keys are
missing from the source? Comments are ignored throughout.

The citation rules are a deliberate copy of the key rules of Atlas's
``citations.extract_cite_keys`` (P3: a Bundle never imports another Bundle):
every ``\\…cite…`` command with its optional arguments, multi-cites
(``\\cites{a}{b}``), comma-separated keys, order of first appearance.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Optional

# --- citation keys (copied from the Atlas rule, see module docstring) -------

_CITE_RE = re.compile(r"\\([A-Za-z]*[cC]ite[A-Za-z]*)\*?")
_NOT_CITES = {"citestyle", "citesetup", "citetrackertrue", "citetrackerfalse"}
_OPT_RE = re.compile(r"\s*(\[[^\]]*\]|\([^)]*\))")
_KEYS_RE = re.compile(r"\s*\{([^{}]*)\}")


def strip_comments(text: str) -> str:
    out = []
    for line in text.split("\n"):
        cut = len(line)
        i = 0
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def extract_cite_keys(tex: str) -> list[str]:
    """Every cited key of ``tex`` in the order of first appearance."""
    text = strip_comments(tex or "")
    keys: list[str] = []
    seen: set[str] = set()
    pos = 0
    while True:
        m = _CITE_RE.search(text, pos)
        if m is None:
            break
        command = m.group(1)
        pos = m.end()
        if command.lower() in _NOT_CITES:
            continue
        multi = command.lower().endswith("cites")
        while True:
            while True:
                opt = _OPT_RE.match(text, pos)
                if opt is None:
                    break
                pos = opt.end()
            group = _KEYS_RE.match(text, pos)
            if group is None:
                break
            pos = group.end()
            for key in group.group(1).split(","):
                key = key.strip()
                if key and key != "*" and key not in seen:
                    seen.add(key)
                    keys.append(key)
            if not multi:
                break
    return keys


# --- bibliography commands ---------------------------------------------------

_BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\s*\{([^{}]*)\}")
_ADDBIBRESOURCE_RE = re.compile(r"\\(?:addbibresource|addglobalbib|addsectionbib)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_BIBLATEX_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\bbiblatex\b[^{}]*\}")
_DOCUMENTCLASS_RE = re.compile(r"\\documentclass\b")


def _basename(name: str) -> str:
    name = name.strip()
    return PureWindowsPath(name).name if "\\" in name else PurePosixPath(name).name


def _names_file(entry: str, filename: str, *, with_ext: bool) -> bool:
    """Whether one ``\\bibliography``/``\\addbibresource`` argument names
    ``filename`` (case-insensitive, a relative path allowed)."""
    base = _basename(entry).casefold()
    target = filename.casefold()
    stem = target[:-4] if target.endswith(".bib") else target
    if with_ext:
        return base == target
    return base in (stem, target)


def scan_text(tex: str, filename: str) -> dict:
    """The pure check of one ``.tex`` text against ``filename``:
    ``{includes, biblatex, documentclass, keys}``."""
    text = strip_comments(tex or "")
    includes = False
    for m in _BIBLIOGRAPHY_RE.finditer(text):
        if any(_names_file(e, filename, with_ext=False) for e in m.group(1).split(",")):
            includes = True
    for m in _ADDBIBRESOURCE_RE.finditer(text):
        if _names_file(m.group(1), filename, with_ext=True):
            includes = True
    return {
        "includes": includes,
        "biblatex": bool(_BIBLATEX_RE.search(text) or _ADDBIBRESOURCE_RE.search(text)),
        "documentclass": bool(_DOCUMENTCLASS_RE.search(text)),
        "keys": extract_cite_keys(tex),
    }


def snippet(filename: str, biblatex: bool) -> str:
    """The lines that include ``filename`` (the design's copy)."""
    if biblatex:
        return ("% in die Präambel\n\\addbibresource{" + filename + "}\n\n"
                "% ans Ende, wo das Verzeichnis stehen soll\n\\printbibliography")
    stem = filename[:-4] if filename.lower().endswith(".bib") else filename
    return "\\bibliography{" + stem + "}"


def snippet_line(filename: str, biblatex: bool) -> str:
    """The one line a card shows when the file is not included yet."""
    if biblatex:
        return "\\addbibresource{" + filename + "}"
    return snippet(filename, False)


def summarize(files: Iterable[tuple[str, str]], filename: str, source_keys: Iterable[str]) -> dict:
    """Combine the scans of ``(relative name, text)`` pairs:

    ``{tex_files, main_tex, included_in, biblatex, cited, missing_keys,
    snippet, snippet_line}``. ``main_tex`` is the file with
    ``\\documentclass`` (else the first one), ``included_in`` the first file
    that includes ``filename``, ``missing_keys`` the cited keys the source
    does not hold, in order of first appearance."""
    return summarize_scans(((name, scan_text(text, filename)) for name, text in files),
                           filename, source_keys)


def summarize_scans(scans: Iterable[tuple[str, dict]], filename: str,
                    source_keys: Iterable[str]) -> dict:
    """:func:`summarize` over ``(relative name, scan_text result)`` pairs."""
    names: list[str] = []
    main_tex: Optional[str] = None
    included_in: Optional[str] = None
    biblatex = False
    cited: list[str] = []
    seen: set[str] = set()
    for name, scan in scans:
        names.append(name)
        if scan["documentclass"] and main_tex is None:
            main_tex = name
        if scan["includes"] and included_in is None:
            included_in = name
        biblatex = biblatex or scan["biblatex"]
        for key in scan["keys"]:
            if key not in seen:
                seen.add(key)
                cited.append(key)
    have = set(source_keys)
    return {
        "tex_files": len(names),
        "main_tex": main_tex or (names[0] if names else None),
        "included_in": included_in,
        "biblatex": biblatex,
        "cited": len(cited),
        "missing_keys": [k for k in cited if k not in have],
        "snippet": snippet(filename, biblatex),
        "snippet_line": snippet_line(filename, biblatex),
    }


# --- the folder scan -----------------------------------------------------------

MAX_FILES = 200
MAX_DEPTH = 3
MAX_BYTES = 2_000_000
_SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".vscode", ".idea", "build", "out"}


def _tex_paths(folder: str) -> list[tuple[str, Path]]:
    """``(relative name, path)`` of the ``.tex`` files in ``folder`` and up
    to three levels below (hidden and build folders skipped, at most 200
    files). Never raises: an unreadable folder ends the walk."""
    root = Path(folder)
    out: list[tuple[str, Path]] = []
    try:
        for current, dirs, files in os.walk(root):
            rel = Path(current).relative_to(root)
            depth = len(rel.parts)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS
                             and depth < MAX_DEPTH)
            for name in sorted(files):
                if not name.lower().endswith(".tex"):
                    continue
                out.append(((rel / name).as_posix(), Path(current) / name))
                if len(out) >= MAX_FILES:
                    return out
    except OSError:
        return out
    return out


def _read(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return handle.read(MAX_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


def tex_files(folder: str) -> list[tuple[str, str]]:
    """``(relative name, text)`` of the ``.tex`` files :func:`_tex_paths`
    finds, each read up to 2 MB. Never raises: an unreadable file is skipped."""
    out: list[tuple[str, str]] = []
    for name, path in _tex_paths(folder):
        text = _read(path)
        if text is not None:
            out.append((name, text))
    return out


#: ``(path, filename) -> ((mtime_ns, size), scan)``: the cards refresh every
#: few seconds, the ``.tex`` files change far less often — a file is read and
#: scanned again only when its stamp changes.
_SCAN_CACHE: dict[tuple[str, str], tuple[tuple[int, int], dict]] = {}
_SCAN_CACHE_MAX = 2000


def _cached_scans(folder: str, filename: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for name, path in _tex_paths(folder):
        try:
            st = path.stat()
        except OSError:
            continue
        stamp = (st.st_mtime_ns, st.st_size)
        key = (str(path), filename)
        hit = _SCAN_CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            out.append((name, hit[1]))
            continue
        text = _read(path)
        if text is None:
            continue
        scan = scan_text(text, filename)
        if len(_SCAN_CACHE) >= _SCAN_CACHE_MAX:
            _SCAN_CACHE.clear()
        _SCAN_CACHE[key] = (stamp, scan)
        out.append((name, scan))
    return out


def check(folder: str, filename: str, source_keys: Iterable[str]) -> dict:
    """:func:`summarize` over the folder's ``.tex`` files; an unchanged file
    comes from the scan cache instead of being read again."""
    return summarize_scans(_cached_scans(folder, filename), filename, source_keys)
