"""The sync engine over the contract fakes: write rules, sources, tombstones."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest
from plugin_api.testing import InMemorySettings

from lacite import connections as conns
from lacite import sync
from lacite.engine import STATE_ERROR, STATE_FOREIGN, STATE_OK, STATE_PAUSED, Engine


@pytest.fixture
def engine(library):
    return Engine(library, InMemorySettings())


def _target(project, name="literatur.bib"):
    return project / name


def test_create_writes_the_file_and_stores_compact_json(engine, project):
    out = engine.create({"folder": str(project)})
    conn = out["connection"]
    assert conn["name"] == "Dissertation"
    assert out["sync"]["written"] is True and conn["status"]["state"] == STATE_OK
    assert conn["status"]["entries"] == 3
    text = _target(project).read_text(encoding="utf-8")
    assert text.startswith(sync.HEADER) and "@article{Smith2020," in text
    stored = engine.settings.get(conns.SETTINGS_KEY)
    assert isinstance(stored, str) and json.loads(stored)[0]["folder"] == str(project)
    # the document check: main.tex cites Smith2020, does not include the file yet
    doc = conn["document"]
    assert doc["main_tex"] == "main.tex" and doc["included_in"] is None
    assert doc["snippet_line"] == "\\bibliography{literatur}"


def test_no_rewrite_when_nothing_changed(engine, project):
    engine.create({"folder": str(project)})
    path = _target(project)
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    results = engine.sync_all(force=True)
    assert results[0]["written"] is False
    assert path.stat().st_mtime_ns == before


def test_a_library_change_rewrites_once(engine, library, project):
    engine.create({"folder": str(project)})
    library.add({"citekey": "New2025", "title": "New"})
    assert engine.sync_all()[0]["written"] is True
    assert "New2025" in _target(project).read_text(encoding="utf-8")
    assert engine.sync_all()[0]["written"] is False


def test_a_deleted_target_comes_back(engine, project):
    engine.create({"folder": str(project)})
    _target(project).unlink()
    assert engine.sync_all()[0]["written"] is True
    assert _target(project).exists()


def test_only_the_one_target_file_is_touched(engine, project):
    (project / "other.bib").write_text("@misc{x,}\n")
    engine.create({"folder": str(project)})
    assert sorted(os.listdir(project)) == ["literatur.bib", "main.tex", "other.bib"]
    assert (project / "other.bib").read_text() == "@misc{x,}\n"


def test_foreign_file_asks_first_and_stores_nothing(engine, project):
    _target(project).write_text("@misc{mine,}\n")
    out = engine.create({"folder": str(project)})
    assert out == {"needs_confirm": "foreign_file", "file": str(_target(project))}
    assert engine.connections() == []
    assert _target(project).read_text() == "@misc{mine,}\n"
    out = engine.create({"folder": str(project)}, overwrite=True)
    assert out["connection"]["status"]["state"] == STATE_OK
    assert _target(project).read_text(encoding="utf-8").startswith(sync.HEADER)


def test_a_file_that_lost_its_header_is_not_overwritten_silently(engine, project, library):
    conn = engine.create({"folder": str(project)})["connection"]
    _target(project).write_text("% my own now\n")
    library.add({"citekey": "X1", "title": "x"})
    result = engine.sync(engine.get(conn["id"]))
    assert result["state"] == STATE_FOREIGN and result["written"] is False
    assert _target(project).read_text() == "% my own now\n"
    assert engine.sync(engine.get(conn["id"]), overwrite=True)["written"] is True


def test_collection_scope(engine, library, project):
    cid = library.add_collection("Paper H2", refs=["Niu.2026"])
    conn = engine.create({"folder": str(project), "filename": "quellen.bib",
                          "source": {"kind": "collection", "id": cid}})["connection"]
    assert conn["status"]["entries"] == 1
    assert conn["status"]["source"]["name"] == "Paper H2"
    text = (project / "quellen.bib").read_text(encoding="utf-8")
    assert "Niu.2026" in text and "Smith2020" not in text
    # main.tex cites Smith2020, which this source does not hold
    assert conn["document"]["missing_keys"] == ["Smith2020"]


def test_a_deleted_collection_pauses_and_keeps_the_file(engine, library, project):
    cid = library.add_collection("Konferenz 2025", refs=["Niu.2026"])
    conn = engine.create({"folder": str(project), "source": {"kind": "collection", "id": cid}})["connection"]
    before = _target(project).read_bytes()
    library.delete_collection(cid)
    library.add({"citekey": "Late", "title": "late"})
    result = engine.sync(engine.get(conn["id"]))
    assert result["state"] == STATE_PAUSED
    assert result["error"] == "error.lacite.collection_deleted"
    assert result["source"]["name"] == "Konferenz 2025" and result["source"]["deleted"] is True
    assert _target(project).read_bytes() == before
    # "Neue Quelle wählen": the library again
    out = engine.update(conn["id"], {"source": {"kind": "library"}})
    assert out["connection"]["status"]["state"] == STATE_OK
    assert "Late" in _target(project).read_text(encoding="utf-8")


def test_a_deleted_collection_cannot_be_chosen(engine, library, project):
    cid = library.add_collection("Gone")
    library.delete_collection(cid)
    with pytest.raises(conns.InvalidConnection) as err:
        engine.create({"folder": str(project), "source": {"kind": "collection", "id": cid}})
    assert err.value.code == "error.lacite.collection_missing"
    assert "Gone" not in [c["name"] for c in engine.sources()["collections"]]


def test_a_missing_folder_is_an_error_and_never_created(engine, project, tmp_path):
    conn = engine.create({"folder": str(project)})["connection"]
    gone = tmp_path / "moved"
    project.rename(gone)
    result = engine.sync(engine.get(conn["id"]))
    assert result["state"] == STATE_ERROR and result["error"] == "error.lacite.folder_missing"
    assert not project.exists()


def test_refused_roots_apply_to_create(library, tmp_path):
    base = tmp_path / "Literatur"
    base.mkdir()
    engine = Engine(library, InMemorySettings(), refused_roots=[base])
    with pytest.raises(conns.InvalidConnection) as err:
        engine.create({"folder": str(base)})
    assert err.value.code == "error.lacite.folder_inside_localbib"
    assert list(base.iterdir()) == []


def test_two_connections_cannot_share_a_file(engine, project):
    engine.create({"folder": str(project)})
    with pytest.raises(conns.InvalidConnection) as err:
        engine.create({"folder": str(project), "name": "again"})
    assert err.value.code == "error.lacite.target_taken"
    engine.create({"folder": str(project), "filename": "zweite.bib"})
    assert len(engine.connections()) == 2


def test_remove_keeps_the_file(engine, project):
    conn = engine.create({"folder": str(project)})["connection"]
    assert engine.remove(conn["id"]) is True
    assert engine.connections() == []
    assert _target(project).exists()
    assert engine.remove(conn["id"]) is False


def test_moving_to_another_file_keeps_the_old_one(engine, project):
    conn = engine.create({"folder": str(project)})["connection"]
    engine.update(conn["id"], {"filename": "quellen.bib", "name": "Neu"})
    assert _target(project).exists() and (project / "quellen.bib").exists()
    assert engine.get(conn["id"])["name"] == "Neu"


def test_a_connection_removed_meanwhile_is_not_synced(engine, project):
    conn = engine.create({"folder": str(project)})["connection"]
    stale = engine.get(conn["id"])          # what a running poll still holds
    engine.remove(conn["id"])
    _target(project).unlink()
    assert engine.sync(stale) is None
    assert not _target(project).exists()
    assert conn["id"] not in engine._status  # no leaked status either


def test_a_moved_connection_syncs_its_new_target(engine, project):
    conn = engine.create({"folder": str(project)})["connection"]
    stale = engine.get(conn["id"])
    engine.update(conn["id"], {"filename": "quellen.bib"})
    _target(project).unlink()
    engine.sync(stale, force=True)
    assert not _target(project).exists() and (project / "quellen.bib").exists()


def test_connections_sharing_a_source_export_it_once(library, project):
    calls = []
    real = library.export_bibtex

    def counting():
        calls.append(1)
        return real()

    library.export_bibtex = counting
    eng = Engine(library, InMemorySettings())
    eng.create({"folder": str(project)})
    eng.create({"folder": str(project), "filename": "zweite.bib"})
    calls.clear()
    assert len(eng.sync_all(force=True)) == 2
    assert len(calls) == 1


def test_an_unreadable_target_is_a_code_not_a_crash(engine, project):
    (project / "literatur.bib").mkdir()
    with pytest.raises(conns.InvalidConnection) as err:
        engine.create({"folder": str(project)})
    assert err.value.code == "error.lacite.target_unreadable"


def test_collection_events_never_wait_for_a_running_sync(engine):
    with engine._lock:  # a sync holding the lock on another thread
        done = []
        t = threading.Thread(target=lambda: (engine.on_event({"type": "collection"}), done.append(1)))
        t.start()
        t.join(timeout=1)
        assert done == [1]
    engine.stop()


def test_sources_list_counts(engine, library):
    library.add_collection("A", refs=["Smith2020", "Niu.2026"])
    out = engine.sources()
    assert out["library"]["count"] == 3
    assert out["collections"][0]["item_count"] == 2


def test_collection_events_sync_after_a_debounce(library, project, monkeypatch):
    import lacite.engine as engine_mod

    monkeypatch.setattr(engine_mod, "EVENT_DEBOUNCE_SECONDS", 0.05)
    cid = library.add_collection("C", refs=["Smith2020"])
    eng = Engine(library, InMemorySettings())
    eng.create({"folder": str(project), "source": {"kind": "collection", "id": cid}})
    eng.start(interval=3600)
    try:
        assert library.listener_count == 1
        library.set_collection_refs(cid, ["Smith2020", "Niu.2026"])
        deadline = time.time() + 3
        while "Niu.2026" not in _target(project).read_text(encoding="utf-8") and time.time() < deadline:
            time.sleep(0.02)
        assert "Niu.2026" in _target(project).read_text(encoding="utf-8")
        eng.on_event({"type": "paper"})   # unknown types are ignored
        eng.on_event("garbage")           # and never raise
    finally:
        eng.stop()
    assert library.listener_count == 0


def test_the_poll_picks_up_paper_changes(library, project):
    eng = Engine(library, InMemorySettings())
    eng.create({"folder": str(project)})
    eng.start(interval=0.05)
    try:
        library.add({"citekey": "Polled", "title": "p"})
        deadline = time.time() + 3
        while "Polled" not in _target(project).read_text(encoding="utf-8") and time.time() < deadline:
            time.sleep(0.02)
        assert "Polled" in _target(project).read_text(encoding="utf-8")
    finally:
        eng.stop()


def test_a_broken_library_never_kills_the_sync(project):
    class Broken:
        def export_bibtex(self):
            raise RuntimeError("db locked")

    eng = Engine(Broken(), InMemorySettings())
    eng.settings.set(conns.SETTINGS_KEY, conns.dumps([conns.normalize({"folder": str(project)})]))
    (result,) = eng.sync_all()
    assert result["state"] == STATE_ERROR and result["error"] == "error.lacite.sync_failed"


def test_an_older_host_without_collections(project):
    class OldLibrary:
        def export_bibtex(self):
            return "@misc{a,}\n"

    eng = Engine(OldLibrary(), InMemorySettings())
    assert eng.sources()["collections_supported"] is False
    with pytest.raises(conns.InvalidConnection) as err:
        eng.create({"folder": str(project), "source": {"kind": "collection", "id": 1}})
    assert err.value.code == "error.lacite.collections_unsupported"
    assert eng.create({"folder": str(project)})["connection"]["status"]["entries"] == 1
