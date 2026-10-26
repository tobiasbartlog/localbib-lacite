"""What the LaTeX project does with LaCite's file (pure rules + one folder scan).

For one connection: is the ``.bib`` included (``\\bibliography{x}`` for
BibTeX/natbib, ``\\addbibresource{x.bib}`` for biblatex)? Does the project use
biblatex (so the snippet to show is the biblatex one)? Which cited keys are
missing from the source? Comments are ignored throughout.

For the missing keys, the project's *other* ``.bib`` files (an old one the
project used before LaCite) are searched: :func:`bib_blocks` cuts a ``.bib``
text into its ``@``-blocks, :func:`find_entries` takes each key's entry from
the first file that holds it, :func:`excerpt` joins them (with the
``@string`` macros they may use) into the BibTeX text the core's import
previews. :func:`bib_paths` / :func:`read_bibs` are the folder half: the
named files first, then every other ``.bib`` up to three levels, never the
connection's own target, size-limited, never raising.

The citation rules are a deliberate copy of the key rules of Atlas's
``citations.extract_cite_keys`` (P3: a Bundle never imports another Bundle):
every ``\\…cite…`` command with its optional arguments, multi-cites
(``\\cites{a}{b}``), comma-separated keys, order of first appearance.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Iterator, Optional

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


def _named_bibs(text: str) -> list[str]:
    """The ``.bib`` files a comment-free ``.tex`` text names, in order:
    every ``\\bibliography{a,b}`` argument (``.bib`` added when it has none)
    and every ``\\addbibresource{x.bib}`` as written."""
    found: list[tuple[int, str]] = []
    for m in _BIBLIOGRAPHY_RE.finditer(text):
        for entry in m.group(1).split(","):
            entry = entry.strip()
            if entry:
                found.append((m.start(), entry if entry.lower().endswith(".bib") else entry + ".bib"))
    for m in _ADDBIBRESOURCE_RE.finditer(text):
        entry = m.group(1).strip()
        if entry:
            found.append((m.start(), entry))
    out: list[str] = []
    for _, entry in sorted(found, key=lambda item: item[0]):
        if entry not in out:
            out.append(entry)
    return out


def named_bibs(tex: str) -> list[str]:
    """:func:`_named_bibs` of a raw ``.tex`` text (comments ignored)."""
    return _named_bibs(strip_comments(tex or ""))


def scan_text(tex: str, filename: str) -> dict:
    """The pure check of one ``.tex`` text against ``filename``:
    ``{includes, biblatex, documentclass, keys, bib_files}``."""
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
        "bib_files": _named_bibs(text),
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
    bib_files, snippet, snippet_line}``. ``main_tex`` is the file with
    ``\\documentclass`` (else the first one), ``included_in`` the first file
    that includes ``filename``, ``missing_keys`` the cited keys the source
    does not hold, in order of first appearance, ``bib_files`` every ``.bib``
    the files name (``filename`` included), as written."""
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
    bib_files: list[str] = []
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
        for entry in scan.get("bib_files", ()):
            if entry not in bib_files:
                bib_files.append(entry)
    have = set(source_keys)
    return {
        "tex_files": len(names),
        "main_tex": main_tex or (names[0] if names else None),
        "included_in": included_in,
        "biblatex": biblatex,
        "cited": len(cited),
        "missing_keys": [k for k in cited if k not in have],
        "bib_files": bib_files,
        "snippet": snippet(filename, biblatex),
        "snippet_line": snippet_line(filename, biblatex),
    }


# --- entries of other .bib files (missing keys) --------------------------------

_BLOCK_START_RE = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_DELIM_RE = re.compile(r'[{}()"]')
_MACRO_TYPES = {"string", "preamble", "comment"}


def _block_end(text: str, start: int, opener: str) -> Optional[int]:
    """The index just past the block whose delimiter ``opener`` sits at
    ``start``; ``None`` when it never closes (a cut-off file). Braces are
    counted as BibTeX counts them, a ``(``-block closes at a ``)`` outside
    every brace and outside every ``"…"`` value (``note = "see (p. 3)"``).
    A quote counts only outside braces — inside them it is a character
    (``{"}``), as in BibTeX. A ``{``-block ends at its brace whatever the
    quotes say: BibTeX keeps braces balanced inside a quoted value."""
    depth = 0
    quoted = False
    for m in _DELIM_RE.finditer(text, start + 1):
        ch = m.group()
        if ch == '"':
            if depth == 0 and opener == "(":
                quoted = not quoted
        elif ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                return m.end() if opener == "{" else None
            depth -= 1
        elif ch == ")" and opener == "(" and depth == 0 and not quoted:
            return m.end()
    return None


def bib_blocks(bibtex: str) -> list[tuple[str, str, str]]:
    """``(type, key, text)`` of every ``@``-block of a ``.bib`` text in
    order; ``type`` lower-cased, ``key`` empty for ``@string``,
    ``@preamble`` and ``@comment``. Text between blocks is skipped (BibTeX
    reads it as a comment), a block that never closes is dropped — a cut-off
    last entry, or one entry with an unbalanced brace, whose followers are
    still read from their own ``@``. Never raises."""
    text = bibtex or ""
    out: list[tuple[str, str, str]] = []
    pos = 0
    while True:
        m = _BLOCK_START_RE.search(text, pos)
        if m is None:
            break
        end = _block_end(text, m.end() - 1, m.group(2))
        if end is None:
            pos = m.end()  # one broken entry must not hide every later one
            continue
        kind = m.group(1).lower()
        key = ""
        if kind not in _MACRO_TYPES:
            head = text[m.end():end - 1]
            comma = head.find(",")
            key = (head[:comma] if comma != -1 else head).strip()
        out.append((kind, key, text[m.start():end]))
        pos = end
    return out


def find_entries(keys: Iterable[str], bibs: Iterable[tuple[str, str]]) -> dict:
    """Each key's entry from the first of ``bibs`` (``(name, text)`` in
    priority order) that holds it, keys matched exactly — the key must stay
    the one ``\\cite`` uses. ``{found: {key: name}, entries: {key: text},
    strings: {name: [@string blocks]}}``; ``bibs`` is read lazily and no
    further once every key is found."""
    wanted = {k for k in keys if k}
    found: dict[str, str] = {}
    entries: dict[str, str] = {}
    strings: dict[str, list[str]] = {}
    if not wanted:
        return {"found": found, "entries": entries, "strings": strings}
    for name, text in bibs:
        blocks = bib_blocks(text)
        took = False
        for kind, key, block in blocks:
            if key and key in wanted and key not in found:
                found[key] = name
                entries[key] = block
                took = True
        if took:
            strings[name] = [block for kind, _, block in blocks if kind == "string"]
        if len(found) == len(wanted):
            break
    return {"found": found, "entries": entries, "strings": strings}


def excerpt(result: dict, keys: Iterable[str]) -> str:
    """The BibTeX text of ``keys``' entries in a :func:`find_entries`
    result, in the order of ``keys``, after the ``@string`` macros of the
    files they come from (an entry may use them). Empty when none is
    there."""
    order = [k for k in dict.fromkeys(keys) if k in result["entries"]]
    blocks: list[str] = []
    for name in dict.fromkeys(result["found"][k] for k in order):
        for block in result["strings"].get(name, ()):
            if block not in blocks:
                blocks.append(block)
    blocks.extend(result["entries"][k] for k in order)
    return "\n\n".join(blocks) + "\n" if blocks else ""


_TITLE_FIELD_RE = re.compile(r"(?<![A-Za-z])title\s*=\s*", re.IGNORECASE)
TITLE_CHARS = 200


def _field_value(text: str, start: int) -> str:
    """The raw value of a BibTeX field that starts at ``start``: a braced
    or quoted value with its nesting counted, else everything up to the next
    comma (a macro or a number)."""
    if start >= len(text):
        return ""
    opener = text[start]
    if opener in "{\"":
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if opener == "{" and depth == 0:
                    return text[start + 1:i]
            elif ch == '"' and opener == '"' and i > start and depth == 0:
                return text[start + 1:i]
        return text[start + 1:]
    end = text.find(",", start)
    return text[start:end if end != -1 else len(text)].rstrip("})")


def entry_info(block: str) -> dict:
    """``{type, title}`` of one ``@``-block for a list a person reads: the
    entry type lower-cased (``report``), the title without braces and quotes,
    whitespace folded, at most :data:`TITLE_CHARS` characters; ``""`` where a
    part is missing. Never raises."""
    text = block or ""
    m = _BLOCK_START_RE.search(text)
    kind = m.group(1).lower() if m else ""
    title = ""
    t = _TITLE_FIELD_RE.search(text, m.end() if m else 0)
    if t is not None:
        raw = _field_value(text, t.end())
        title = " ".join(raw.replace("{", "").replace("}", "").split())
        if len(title) > TITLE_CHARS:
            title = title[:TITLE_CHARS].rstrip() + "…"
    return {"type": kind, "title": title}


SUGGEST_CUTOFF = 0.85


def suggest_key(key: str, candidates: Iterable[str]) -> Optional[str]:
    """A key the library holds that the cited ``key`` is probably a typo
    of: the same key in another case first, else the closest one with a
    similarity of at least :data:`SUGGEST_CUTOFF` (``Mueller.2018a`` →
    ``Mueller.2018``). ``None`` when nothing is that close — a hint, never a
    replacement: the user decides. Never raises."""
    wanted = (key or "").strip()
    if not wanted:
        return None
    pool = [c for c in dict.fromkeys(candidates or ()) if c and c != wanted]
    folded = wanted.casefold()
    same = next((c for c in pool if c.casefold() == folded), None)
    if same is not None:
        return same
    close = difflib.get_close_matches(wanted, pool, n=1, cutoff=SUGGEST_CUTOFF)
    return close[0] if close else None


# --- the folder scan -----------------------------------------------------------

MAX_FILES = 200
MAX_DEPTH = 3
MAX_BYTES = 2_000_000
_SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".vscode", ".idea", "build", "out"}


def _project_files(folder: str, suffix: str, limit: int = MAX_FILES) -> list[tuple[str, Path]]:
    """``(relative name, path)`` of the ``suffix`` files in ``folder`` and up
    to three levels below (hidden and build folders skipped, at most
    ``limit`` files). Never raises: an unreadable folder ends the walk."""
    root = Path(folder)
    out: list[tuple[str, Path]] = []
    try:
        for current, dirs, files in os.walk(root):
            rel = Path(current).relative_to(root)
            depth = len(rel.parts)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS
                             and depth < MAX_DEPTH)
            for name in sorted(files):
                if not name.lower().endswith(suffix):
                    continue
                out.append(((rel / name).as_posix(), Path(current) / name))
                if len(out) >= limit:
                    return out
    except (OSError, ValueError):
        return out
    return out


def _tex_paths(folder: str) -> list[tuple[str, Path]]:
    return _project_files(folder, ".tex")


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


# --- the other .bib files of the folder ------------------------------------------

MAX_BIB_FILES = 50
MAX_BIB_BYTES = 8_000_000


def _identity(path: Path) -> str:
    """One spelling per file (case and links folded), for "the same file"."""
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def same_file(a: Path, b: Path) -> bool:
    return _identity(a) == _identity(b)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _display(root: Path, path: Path) -> str:
    """The name a list shows: relative to the project folder when inside it."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def resolve_named(folder: str, named: Iterable[str], *, main_tex: Optional[str] = None) -> list[tuple[str, Path]]:
    """``(name, path)`` of the named ``.bib`` files that exist, each looked
    up relative to the folder of ``main_tex`` (where LaTeX runs and so
    resolves it), then to the project folder (an absolute name as it is). A
    name not ending in ``.bib`` is ignored, so a ``.tex`` cannot point the
    search at another kind of file. Never raises."""
    root = Path(folder)
    bases = [root]
    if main_tex:
        bases.insert(0, (root / main_tex).parent)
    out: list[tuple[str, Path]] = []
    for entry in named:
        entry = (entry or "").strip()
        if not entry.lower().endswith(".bib"):
            continue
        try:
            given = Path(entry).expanduser()
            candidates = [given] if given.is_absolute() else [base / entry for base in bases]
        except (OSError, ValueError, RuntimeError):
            continue
        for candidate in candidates:
            if _is_file(candidate):
                out.append((_display(root, candidate), candidate))
                break
    return out


def bib_paths(folder: str, named: Iterable[tuple[str, Path]], *, exclude: Path) -> list[tuple[str, Path]]:
    """Where to look for missing keys, in order: the ``named`` files
    (:func:`resolve_named`), then every other ``.bib`` in the folder and up
    to three levels below (the ``.tex`` walk's rules), each file once, at
    most :data:`MAX_BIB_FILES`. ``exclude`` — the connection's own target —
    is never in the list. Never raises."""
    seen = {_identity(exclude)}
    out: list[tuple[str, Path]] = []
    walked = _project_files(folder, ".bib", MAX_BIB_FILES)
    for name, path in [*named, *walked]:
        ident = _identity(path)
        if ident in seen:
            continue
        seen.add(ident)
        out.append((name, path))
        if len(out) >= MAX_BIB_FILES:
            break
    return out


def read_bib(path: Path) -> Optional[str]:
    """A ``.bib`` file's text, read up to 8 MB (a cut-off last entry is
    dropped by :func:`bib_blocks`): UTF-8 (a BOM tolerated), else Windows-1252
    — the encoding of many older ``.bib`` files. ``None`` when unreadable."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BIB_BYTES)
    except OSError:
        return None
    if len(data) == MAX_BIB_BYTES:
        cut = data.rfind(b"\n")
        data = data[:cut + 1] if cut != -1 else data
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_bibs(paths: Iterable[tuple[str, Path]]) -> Iterator[tuple[str, str]]:
    """``(name, text)`` of each readable file, read only when asked for —
    :func:`find_entries` stops once every key is found."""
    for name, path in paths:
        text = read_bib(path)
        if text is not None:
            yield name, text
