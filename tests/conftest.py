"""Shared fixtures of the LaCite tests — the contract package and its fakes only.

These tests never import the LocalBib core: the isolated CI job installs just
this Bundle, ``plugin_api`` and the Bundle's own dependencies and runs them
there. The Bundle root goes on ``sys.path`` the way the core loader puts it
there, so ``import lacite`` resolves to the package next to ``plugin.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
for entry in (BUNDLE_ROOT, BUNDLE_ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from lacite_fakes import seeded_library  # noqa: E402


@pytest.fixture
def library():
    return seeded_library()


@pytest.fixture
def project(tmp_path) -> Path:
    """A LaTeX project folder with one main.tex."""
    folder = tmp_path / "Dissertation"
    folder.mkdir()
    (folder / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nwie \\cite{Smith2020} zeigt\n"
        "\\end{document}\n", encoding="utf-8")
    return folder
