"""The pure rules: connections, the .bib target, folder refusal, texcheck."""

from __future__ import annotations

import json
import os

import pytest

from lacite import connections as conns
from lacite import sync, texcheck


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------

def test_normalize_defaults_name_filename_and_source(tmp_path):
    conn = conns.normalize({"folder": " C:\\Users\\x\\Dissertation\\ "})
    assert conn["name"] == "Dissertation"
    assert conn["filename"] == "literatur.bib"
    assert conn["source"] == {"kind": "library"}
    assert conn["id"].startswith("c")


@pytest.mark.parametrize("raw, filename", [
    ("quellen", "quellen.bib"), ("Quellen.BIB", "Quellen.BIB"), ("", "literatur.bib")])
def test_filename_rule(raw, filename):
    assert conns.normalize_filename(raw) == filename


@pytest.mark.parametrize("bad", ["../x.bib", "a/b.bib", "a\\b.bib", ".bib", "c:x.bib", "a?.bib"])
def test_filename_refuses_paths_and_odd_names(bad):
    with pytest.raises(conns.InvalidConnection) as err:
        conns.normalize_filename(bad)
    assert err.value.code == "error.lacite.filename_invalid"


def test_source_rule():
    assert conns.normalize_source({"kind": "collection", "id": "7"}) == {"kind": "collection", "id": 7}
    for bad in ({"kind": "collection"}, {"kind": "collection", "id": 0}, {"kind": "x"}, "lib"):
        with pytest.raises(conns.InvalidConnection):
            conns.normalize_source(bad)


def test_folder_is_required():
    with pytest.raises(conns.InvalidConnection) as err:
        conns.normalize({"folder": "  "})
    assert err.value.code == "error.lacite.folder_required"


def test_parse_and_dumps_roundtrip_and_tolerate_garbage():
    items = [conns.normalize({"id": "c1", "folder": "/a", "name": "A"}),
             conns.normalize({"id": "c2", "folder": "/b", "source": {"kind": "collection", "id": 3}})]
    text = conns.dumps(items)
    assert " " not in text.replace("literatur.bib", "")  # compact
    assert conns.parse(text) == items
    assert conns.parse("") == [] and conns.parse("{not json") == [] and conns.parse('{"a":1}') == []
    broken = json.dumps([{"id": "c1", "folder": "/a"}, {"folder": ""}, {"id": "c1", "folder": "/b"}, 5])
    assert [c["id"] for c in conns.parse(broken)] == ["c1"]


def test_one_target_file_per_connection():
    a = conns.normalize({"id": "c1", "folder": "C:\\Diss", "filename": "literatur.bib"})
    same = conns.normalize({"id": "c2", "folder": "c:/diss/", "filename": "Literatur.bib"})
    other_name = conns.normalize({"id": "c3", "folder": "C:\\Diss", "filename": "quellen.bib"})
    with pytest.raises(conns.InvalidConnection) as err:
        conns.check_unique(same, [a])
    assert err.value.code == "error.lacite.target_taken"
    conns.check_unique(other_name, [a])  # one folder, several files: fine
    conns.check_unique(a, [a])           # editing itself: fine


# ---------------------------------------------------------------------------
# the .bib target
# ---------------------------------------------------------------------------

def test_target_content_has_the_header_and_one_trailing_newline():
    text = sync.target_content("@article{a,\n  title = {A}\n}\n\n\n")
    assert text.startswith(sync.HEADER_MARK)
    assert text.splitlines()[0] == sync.HEADER
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert sync.target_content("\n") == sync.HEADER + "\n\n"
    assert sync.HEADER.isascii()


def test_bib_keys_skip_string_and_comment():
    bib = "@string{x = {y}}\n@Article{ a ,\n}\n@comment{zz,}\n@book(b,\n)\n"
    assert sync.bib_keys(bib) == ["a", "b"]
    assert sync.count_entries(bib) == 2


def test_foreign_detection():
    assert sync.is_foreign(None) is False
    assert sync.is_foreign(b"@article{a,}") is True
    assert sync.is_foreign(sync.target_content("").encode()) is False
    assert sync.is_foreign(b"\xef\xbb\xbf" + sync.target_content("").encode()) is False


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    path = tmp_path / "literatur.bib"
    sync.atomic_write(path, "one")
    sync.atomic_write(path, "two")
    assert path.read_text() == "two"
    assert sorted(os.listdir(tmp_path)) == ["literatur.bib"]


def test_atomic_write_never_creates_a_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync.atomic_write(tmp_path / "missing" / "x.bib", "x")
    assert not (tmp_path / "missing").exists()


def test_atomic_write_cleans_up_on_failure(tmp_path, monkeypatch):
    def boom(src, dst):
        raise PermissionError("locked")
    monkeypatch.setattr(sync.os, "replace", boom)
    with pytest.raises(PermissionError):
        sync.atomic_write(tmp_path / "x.bib", "x")
    assert os.listdir(tmp_path) == []


def test_needs_write_compares_bytes():
    assert sync.needs_write(None, "a")
    assert not sync.needs_write(b"a", "a")
    assert sync.needs_write(b"a", "b")


# ---------------------------------------------------------------------------
# folder refusal
# ---------------------------------------------------------------------------

def test_check_folder_accepts_a_project(project):
    assert sync.check_folder(str(project)) is None


def test_check_folder_refusals(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    assert sync.check_folder("") == "error.lacite.folder_required"
    assert sync.check_folder("relative/dir") == "error.lacite.folder_not_absolute"
    assert sync.check_folder(str(tmp_path / "nope")) == "error.lacite.folder_missing"
    assert sync.check_folder(str(tmp_path / "file.txt")) == "error.lacite.folder_not_a_folder"
    assert sync.check_folder(os.path.abspath(os.sep)) == "error.lacite.folder_is_root"


def test_check_folder_refuses_the_library_and_addon_folders(tmp_path):
    library = tmp_path / "Literatur"
    (library / "Alle").mkdir(parents=True)
    assert sync.check_folder(str(library / "Alle"), refused_roots=[library]) == \
        "error.lacite.folder_inside_localbib"
    assert sync.check_folder(str(library), refused_roots=[library]) == \
        "error.lacite.folder_inside_localbib"
    assert sync.check_folder(str(tmp_path), refused_roots=[library]) is None


def test_check_folder_refuses_the_settings_folder_and_its_logs(tmp_path):
    settings = tmp_path / "LocalBib"
    (settings / "logs").mkdir(parents=True)
    (settings / "plugins.json").write_text("{}")
    (settings / "llm.json").write_text("{}")
    assert sync.check_folder(str(settings / "logs")) == "error.lacite.folder_inside_localbib"
    assert sync.check_folder(str(settings)) == "error.lacite.folder_inside_localbib"


# ---------------------------------------------------------------------------
# texcheck
# ---------------------------------------------------------------------------

def test_cite_keys_follow_the_atlas_rule():
    tex = ("\\cite{a, b} \\parencite[S.~3]{c} \\textcite[vgl.][12]{d}\n"
           "% \\cite{commented}\n\\cites{e}{f} \\citestyle{x} \\cite{a} 50\\% \\cite{g}")
    assert texcheck.extract_cite_keys(tex) == ["a", "b", "c", "d", "e", "f", "g"]


def test_bibtex_inclusion_and_snippet():
    scan = texcheck.scan_text("\\bibliographystyle{plain}\n\\bibliography{refs,literatur}", "literatur.bib")
    assert scan["includes"] and not scan["biblatex"]
    assert texcheck.snippet("literatur.bib", False) == "\\bibliography{literatur}"


def test_biblatex_inclusion_and_snippet():
    tex = "\\usepackage[style=authoryear]{biblatex}\n\\addbibresource{tex/literatur.bib}"
    scan = texcheck.scan_text(tex, "literatur.bib")
    assert scan["includes"] and scan["biblatex"]
    assert "\\addbibresource{literatur.bib}" in texcheck.snippet("literatur.bib", True)
    assert "\\printbibliography" in texcheck.snippet("literatur.bib", True)
    assert texcheck.snippet_line("quellen.bib", True) == "\\addbibresource{quellen.bib}"


def test_biblatex_without_inclusion_and_commented_inclusion():
    scan = texcheck.scan_text("\\usepackage{biblatex}\n% \\addbibresource{literatur.bib}", "literatur.bib")
    assert scan["biblatex"] and not scan["includes"]
    other = texcheck.scan_text("\\bibliography{quellen}", "literatur.bib")
    assert not other["includes"]


def test_summarize_finds_main_tex_and_missing_keys():
    files = [("kapitel/eins.tex", "\\cite{Smith2020} \\cite{Meier2019} % \\cite{Ghost}"),
             ("main.tex", "\\documentclass{book}\n\\bibliography{literatur}\n\\cite{Chen.2024}")]
    out = texcheck.summarize(files, "literatur.bib", ["Smith2020"])
    assert out["main_tex"] == "main.tex"
    assert out["included_in"] == "main.tex"
    assert out["missing_keys"] == ["Meier2019", "Chen.2024"]
    assert out["cited"] == 3 and out["tex_files"] == 2


def test_folder_scan_skips_hidden_and_deep_folders(tmp_path):
    (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.tex").write_text("x")
    (tmp_path / "a" / "b" / "c" / "deep.tex").write_text("x")
    (tmp_path / "a" / "b" / "c" / "d" / "deeper.tex").write_text("x")
    (tmp_path / ".git" / "hidden.tex").write_text("x")
    names = [n for n, _ in texcheck.tex_files(str(tmp_path))]
    assert names == ["main.tex", "a/b/c/deep.tex"]
    assert texcheck.tex_files(str(tmp_path / "missing")) == []


def test_check_rescans_only_changed_tex_files(tmp_path, monkeypatch):
    main = tmp_path / "main.tex"
    main.write_text(r"\documentclass{article}\cite{A}")
    (tmp_path / "ch1.tex").write_text(r"\cite{B}")
    reads = []
    real_read = texcheck._read
    monkeypatch.setattr(texcheck, "_read", lambda p: reads.append(p.name) or real_read(p))
    texcheck._SCAN_CACHE.clear()
    first = texcheck.check(str(tmp_path), "literatur.bib", [])
    assert sorted(reads) == ["ch1.tex", "main.tex"] and first["cited"] == 2
    reads.clear()
    assert texcheck.check(str(tmp_path), "literatur.bib", ["A"])["missing_keys"] == ["B"]
    assert reads == []  # nothing changed: served from the cache
    main.write_text(r"\documentclass{article}\cite{A,C}\bibliography{literatur}")
    os.utime(main, ns=(main.stat().st_atime_ns, main.stat().st_mtime_ns + 10_000_000))
    out = texcheck.check(str(tmp_path), "literatur.bib", ["A"])
    assert reads == ["main.tex"]
    assert out["included_in"] == "main.tex" and out["missing_keys"] == ["B", "C"]
