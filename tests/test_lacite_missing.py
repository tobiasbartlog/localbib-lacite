"""Missing cited keys: where an old .bib or the library has them (pure rules, engine, route)."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugin_api.testing import InMemorySettings, make_api

import lacite
from lacite import texcheck
from lacite.engine import Engine
from lacite_fakes import MANIFEST

P = "/api/plugins/lacite"

OLD_BIB = """% my old bibliography
@string{jsb = {Journal of Structural Bridges}}

@article{Chen.2024,
  author = {Chen, Li and Wang, Mei},
  title = {Life-cycle costs of {B}ridges},
  journal = jsb,
  year = {2024}
}

@misc{UnitedNationsEnvironmentProgramme.2025,
  author = {{United Nations Environment Programme}},
  title = {Global Status Report},
  url = {https://www.unep.org/report},
  urldate = {2025-03-01},
  year = {2025}
}

@book{Unused.2001, title = {Not cited}}
"""


# ---------------------------------------------------------------------------
# pure rules
# ---------------------------------------------------------------------------

def test_bib_blocks_cut_entries_strings_and_paren_blocks():
    text = ("mail me: a@b.de\n@string{x = \"y\"}\n@Article{A1, title = {Nested {Braces} here}}\n"
            "@misc(B2, note = {a ) b})\n@comment{whatever}\n@book{Cut, title = {never closes")
    blocks = texcheck.bib_blocks(text)
    assert [(kind, key) for kind, key, _ in blocks] == [
        ("string", ""), ("article", "A1"), ("misc", "B2"), ("comment", "")]
    assert blocks[1][2] == "@Article{A1, title = {Nested {Braces} here}}"
    assert blocks[2][2] == "@misc(B2, note = {a ) b})"


def test_a_paren_block_does_not_end_inside_a_quoted_value():
    text = ('@misc(K, note = "see (p. 3)", year = 2020)\n'
            '@misc(Q, note = "a {"} quote) inside braces", year = 2021)\n'
            "@book{After, title = {read on}}\n")
    blocks = texcheck.bib_blocks(text)
    assert [key for _, key, _ in blocks] == ["K", "Q", "After"]
    assert blocks[0][2] == '@misc(K, note = "see (p. 3)", year = 2020)'
    assert blocks[1][2] == '@misc(Q, note = "a {"} quote) inside braces", year = 2021)'


def test_bib_blocks_read_on_after_an_entry_with_an_unbalanced_brace():
    text = "@article{Broken, title = {One {open brace}\n\n@book{After, title = {still read}}\n"
    assert [key for _, key, _ in texcheck.bib_blocks(text)] == ["After"]


def test_named_bibs_follow_both_commands_and_skip_comments():
    tex = ("\\bibliography{refs, alt/old}\n% \\addbibresource{ghost.bib}\n"
           "\\addbibresource[label=x]{quellen.bib}\n\\bibliography{refs}")
    assert texcheck.named_bibs(tex) == ["refs.bib", "alt/old.bib", "quellen.bib"]
    scan = texcheck.scan_text(tex, "literatur.bib")
    assert scan["bib_files"] == ["refs.bib", "alt/old.bib", "quellen.bib"]
    doc = texcheck.summarize([("a.tex", "\\bibliography{x}"), ("b.tex", "\\addbibresource{x.bib}"
                                                                         "\\addbibresource{y.bib}")],
                             "literatur.bib", [])
    assert doc["bib_files"] == ["x.bib", "y.bib"]


def test_find_entries_takes_the_first_file_and_matches_keys_exactly():
    bibs = [("old.bib", OLD_BIB), ("other.bib", "@article{Chen.2024, title = {Second copy}}\n"
                                                "@article{chen.2023, title = {lower}}")]
    out = texcheck.find_entries(["Chen.2024", "Chen.2023", "Ghost"], bibs)
    assert out["found"] == {"Chen.2024": "old.bib"}
    assert "Life-cycle costs" in out["entries"]["Chen.2024"]
    assert out["strings"] == {"old.bib": ["@string{jsb = {Journal of Structural Bridges}}"]}


def test_find_entries_reads_no_further_once_every_key_is_found():
    read = []

    def bibs():
        for name, text in [("a.bib", "@misc{K1, title={x}}"), ("b.bib", "@misc{K2, title={y}}"),
                           ("c.bib", "@misc{K3, title={z}}")]:
            read.append(name)
            yield name, text
    out = texcheck.find_entries(["K1", "K2"], bibs())
    assert out["found"] == {"K1": "a.bib", "K2": "b.bib"} and read == ["a.bib", "b.bib"]
    assert texcheck.find_entries([], bibs())["found"] == {}


def test_excerpt_holds_exactly_the_found_entries_after_their_macros():
    result = texcheck.find_entries(["UnitedNationsEnvironmentProgramme.2025", "Chen.2024", "Ghost"],
                                   [("old.bib", OLD_BIB)])
    text = texcheck.excerpt(result, ["UnitedNationsEnvironmentProgramme.2025", "Chen.2024", "Ghost"])
    assert text.startswith("@string{jsb")
    assert text.index("UnitedNationsEnvironmentProgramme.2025") < text.index("Chen.2024")
    assert "Unused.2001" not in text and "my old bibliography" not in text
    assert [key for _, key, _ in texcheck.bib_blocks(text) if key] == [
        "UnitedNationsEnvironmentProgramme.2025", "Chen.2024"]
    assert texcheck.excerpt(texcheck.find_entries([], []), []) == ""


def test_entry_info_reads_type_and_title_for_the_list():
    blocks = {key: block for _, key, block in texcheck.bib_blocks(OLD_BIB) if key}
    assert texcheck.entry_info(blocks["Chen.2024"]) == {"type": "article", "title": "Life-cycle costs of Bridges"}
    assert texcheck.entry_info(blocks["UnitedNationsEnvironmentProgramme.2025"]) == {
        "type": "misc", "title": "Global Status Report"}
    # booktitle is not the title; quotes and a bare macro are read too
    assert texcheck.entry_info('@inproceedings{K, booktitle = {Proc.}, title = "A {B} c"}') == {
        "type": "inproceedings", "title": "A B c"}
    assert texcheck.entry_info("@misc{K, title = jsb, year = 2020}")["title"] == "jsb"
    assert texcheck.entry_info("@misc{K, year = 2020}") == {"type": "misc", "title": ""}
    assert texcheck.entry_info("") == {"type": "", "title": ""}
    long = texcheck.entry_info("@book{L, title = {" + "x" * 500 + "}}")["title"]
    assert len(long) == texcheck.TITLE_CHARS + 1 and long.endswith("…")


def test_suggest_key_names_a_close_library_key_or_nothing():
    library = ["Mueller.2018", "Smith2020", "Chen.2011", "UnitedNationsEnvironmentProgramme.2024"]
    assert texcheck.suggest_key("Mueller.2018a", library) == "Mueller.2018"
    assert texcheck.suggest_key("smith2020", library) == "Smith2020"        # only the case differs
    assert texcheck.suggest_key("UnitedNationsEnvironmentProgramme.2025", library) == \
        "UnitedNationsEnvironmentProgramme.2024"
    assert texcheck.suggest_key("BMDV.2022", library) is None              # nothing close: no guess
    assert texcheck.suggest_key("Mueller.2018", ["Mueller.2018"]) is None   # never itself
    assert texcheck.suggest_key("", library) is None
    assert texcheck.suggest_key("X", []) is None


def test_read_bib_falls_back_to_windows_1252(tmp_path):
    latin = tmp_path / "alt.bib"
    latin.write_bytes("@misc{M, title = {Brücke – Überblick}}".encode("cp1252"))
    assert texcheck.read_bib(latin) == "@misc{M, title = {Brücke – Überblick}}"
    utf = tmp_path / "neu.bib"
    utf.write_bytes("\ufeff@misc{N, title = {Größe}}".encode("utf-8"))
    assert texcheck.read_bib(utf) == "@misc{N, title = {Größe}}"
    assert texcheck.read_bib(tmp_path / "missing.bib") is None


def test_read_bib_stops_at_the_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(texcheck, "MAX_BIB_BYTES", 40)
    path = tmp_path / "big.bib"
    path.write_text("@misc{A, title={a}}\n@misc{B, title={a long title that is cut}}\n")
    text = texcheck.read_bib(path)
    assert [k for _, k, _ in texcheck.bib_blocks(text)] == ["A"]


def test_bib_paths_named_first_then_the_folder_never_the_target(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
    for rel in ("literatur.bib", "zz.bib", "sub/old.bib", "sub/deeper/x.bib", "a/b/c/d/too-deep.bib"):
        (tmp_path / rel).write_text("@misc{k,}")
    named = texcheck.resolve_named(str(tmp_path), ["sub/old.bib", "ghost.bib", "main.tex"])
    assert [n for n, _ in named] == ["sub/old.bib"]
    paths = texcheck.bib_paths(str(tmp_path), named, exclude=tmp_path / "literatur.bib")
    assert [n for n, _ in paths] == ["sub/old.bib", "zz.bib", "sub/deeper/x.bib"]


def test_resolve_named_looks_beside_the_main_file_first(tmp_path):
    (tmp_path / "thesis").mkdir()
    (tmp_path / "thesis" / "refs.bib").write_text("@misc{k,}")
    found = texcheck.resolve_named(str(tmp_path), ["refs.bib"], main_tex="thesis/main.tex")
    assert [n for n, _ in found] == ["thesis/refs.bib"]
    assert texcheck.resolve_named(str(tmp_path), ["refs.bib"]) == []
    # LaTeX runs in the main file's folder: its refs.bib wins over one at the root
    (tmp_path / "refs.bib").write_text("@misc{root,}")
    found = texcheck.resolve_named(str(tmp_path), ["refs.bib"], main_tex="thesis/main.tex")
    assert [n for n, _ in found] == ["thesis/refs.bib"]
    assert [n for n, _ in texcheck.resolve_named(str(tmp_path), ["refs.bib"])] == ["refs.bib"]


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

@pytest.fixture
def thesis(project):
    """main.tex cites one key of each kind; the old .bib is still included."""
    (project / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{biblatex}\n\\addbibresource{alt.bib}\n"
        "\\addbibresource{literatur.bib}\n\\begin{document}\n"
        "\\cite{Smith2020, Chen.2024} \\cite{Niu.2026} \\parencite{UnitedNationsEnvironmentProgramme.2025}"
        " \\cite{Ghost} \\cite{Deep.2019}\n\\end{document}\n", encoding="utf-8")
    (project / "alt.bib").write_text(OLD_BIB, encoding="utf-8")
    (project / "kapitel").mkdir()
    (project / "kapitel" / "extra.bib").write_text(
        "@article{Chen.2024, title = {A later copy}}\n@book{Deep.2019, title = {Found below}}\n",
        encoding="utf-8")
    return project


@pytest.fixture
def chapter(library, thesis):
    cid = library.add_collection("Kapitel 2", refs=["Smith2020"])
    engine = Engine(library, InMemorySettings())
    conn = engine.create({"folder": str(thesis), "source": {"kind": "collection", "id": cid}})
    return engine, conn["connection"], cid


def _snapshot(folder):
    return {p: (os.stat(p).st_mtime_ns, os.stat(p).st_size)
            for p in (os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs)}


def test_missing_places_every_key(chapter, thesis):
    engine, conn, cid = chapter
    out = engine.missing(conn)
    assert out["available"] is True and out["collection_id"] == cid
    assert [(k["key"], k["status"], k["file"]) for k in out["keys"]] == [
        ("Chen.2024", "found", "alt.bib"),
        ("Niu.2026", "library", None),
        ("UnitedNationsEnvironmentProgramme.2025", "found", "alt.bib"),
        ("Ghost", "not_found", None),
        ("Deep.2019", "found", "kapitel/extra.bib"),
    ]
    assert out["counts"] == {"found": 3, "library": 1, "not_found": 1}
    niu = next(k for k in out["keys"] if k["key"] == "Niu.2026")
    assert niu["item_id"] and niu["title"] == "Hydrogen storage"
    # the old file is still included next to LaCite's own: the card warns about it
    assert out["named_bib_files"] == ["alt.bib"]
    # a found entry shows its old title and type; nothing close to "Ghost" in the library
    chen = next(k for k in out["keys"] if k["key"] == "Chen.2024")
    assert (chen["title"], chen["entry_type"]) == ("Life-cycle costs of Bridges", "article")
    assert next(k for k in out["keys"] if k["key"] == "Ghost")["suggestion"] is None


def test_missing_suggests_a_library_key_for_a_probable_typo(library, project):
    (project / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\\cite{Smith2020a} \\cite{Nobody.1999}\n"
        "\\end{document}\n", encoding="utf-8")
    engine = Engine(library, InMemorySettings())
    conn = engine.create({"folder": str(project)})["connection"]
    out = engine.missing(conn)
    assert {k["key"]: (k["status"], k["suggestion"]) for k in out["keys"]} == {
        "Smith2020a": ("not_found", "Smith2020"),
        "Nobody.1999": ("not_found", None),
    }


def test_missing_bibtex_holds_only_the_found_and_library_entries(chapter):
    engine, conn, _ = chapter
    text = engine.missing(conn)["bibtex"]
    keys = [key for _, key, _ in texcheck.bib_blocks(text) if key]
    assert keys == ["Chen.2024", "Niu.2026", "UnitedNationsEnvironmentProgramme.2025", "Deep.2019"]
    assert "Life-cycle costs" in text and "A later copy" not in text   # the named file wins
    assert "Unused.2001" not in text and "Smith2020" not in text
    assert "@string{jsb" in text                                       # the macro Chen.2024 uses
    assert "url = {https://www.unep.org/report}" in text               # the entry as it was


def test_missing_never_reads_the_target_and_writes_nothing(chapter, thesis, monkeypatch):
    engine, conn, _ = chapter
    target = thesis / "literatur.bib"
    target.write_text(target.read_text(encoding="utf-8") + "\n@misc{Ghost, title = {planted}}\n",
                      encoding="utf-8")
    read = []
    real = texcheck.read_bib
    monkeypatch.setattr(texcheck, "read_bib", lambda p: read.append(os.path.normcase(str(p))) or real(p))
    before = _snapshot(thesis)
    out = engine.missing(conn)
    assert os.path.normcase(str(target)) not in read
    assert next(k for k in out["keys"] if k["key"] == "Ghost")["status"] == "not_found"
    assert _snapshot(thesis) == before


def test_missing_is_empty_while_the_connection_is_not_ok(engine_with_foreign):
    engine, conn = engine_with_foreign
    out = engine.missing(conn)
    assert out["available"] is False and out["keys"] == [] and out["bibtex"] == ""


@pytest.fixture
def engine_with_foreign(library, thesis):
    engine = Engine(library, InMemorySettings())
    conn = engine.create({"folder": str(thesis)})["connection"]
    (thesis / "literatur.bib").write_text("@misc{mine,}\n")  # someone replaced LaCite's file
    engine.sync(conn)
    return engine, engine.get(conn["id"])


def test_missing_survives_a_broken_lookup(chapter, library, monkeypatch):
    engine, conn, _ = chapter

    def broken(key):
        raise RuntimeError("db locked")
    monkeypatch.setattr(library, "get_reference", broken)
    out = engine.missing(conn)
    assert {k["key"]: k["status"] for k in out["keys"]}["Niu.2026"] == "not_found"


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------

def test_missing_route(library, thesis):
    api = make_api(MANIFEST.permissions, library=library)
    plugin = lacite.LaCitePlugin()
    asyncio.run(plugin.activate(api))
    try:
        app = FastAPI()
        app.include_router(api.routes.routers[0])
        client = TestClient(app)
        conn = client.post(f"{P}/connections", json={"folder": str(thesis)}).json()["connection"]
        out = client.get(f"{P}/connections/{conn['id']}/missing").json()
        # the whole library is the source: nothing is "library", no target Collection
        assert out["collection_id"] is None and out["counts"] == {"found": 3, "library": 0, "not_found": 1}
        assert client.get(f"{P}/connections/nope/missing").json()["detail"]["code"] == \
            "error.lacite.connection_not_found"
    finally:
        asyncio.run(plugin.deactivate())
