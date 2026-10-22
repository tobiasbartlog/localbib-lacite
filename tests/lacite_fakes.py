"""Seeds shared by the LaCite tests (a module of its own: ``conftest`` is
not importable by name when several Bundles' tests run in one session)."""

from __future__ import annotations

from pathlib import Path

from plugin_api import load_manifest
from plugin_api.testing import InMemoryLibrary

MANIFEST = load_manifest(Path(__file__).resolve().parent.parent)


def seeded_library(**kwargs) -> InMemoryLibrary:
    return InMemoryLibrary([
        {"citekey": "Smith2020", "title": "Clay printing", "year": 2020, "authors": "Smith, A.",
         "abstract": "About clay.", "passages": [
             {"text": "Printed clay layers bond at 80 degrees.", "page_start": 3,
              "printed_page_start": "347"},
             {"text": "Electrolysis at 80 degrees is more efficient.", "page_start": 4},
         ]},
        {"citekey": "Niu.2026", "title": "Hydrogen storage", "year": 2026, "doi": "10.1/niu"},
        {"citekey": "Doe2019", "title": "No text", "year": 2019, "abstract": "Only an abstract."},
    ], **kwargs)
