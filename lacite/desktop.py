"""The two desktop helpers of the LaCite page: pick a folder, open a folder.

LocalBib runs on the user's own machine, so a route may open a native
dialog. :func:`pick_folder` tries ``tkinter.filedialog.askdirectory`` (topmost
root window, its own thread), then — on Windows, where the frozen ``.exe``
ships without tkinter — the shell's own folder dialog through ``ctypes``
(``SHBrowseForFolderW``). When neither is there it raises
:class:`PickerUnavailable`; the page then shows its "Pfad einfügen" field,
which always exists as the fallback. :func:`open_folder` hands a folder to
the system file manager (``os.startfile`` on Windows).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable, Optional

PICK_TIMEOUT_SECONDS = 600
_pick_lock = threading.Lock()


class PickerUnavailable(RuntimeError):
    """No native folder dialog on this machine (headless, no tkinter)."""


class PickerBusy(RuntimeError):
    """A folder dialog is already open."""


def _tk_pick(initial: str) -> Optional[str]:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        chosen = filedialog.askdirectory(parent=root, initialdir=initial or None,
                                         title="LaTeX-Projektordner wählen", mustexist=True)
    finally:
        root.destroy()
    return os.path.normpath(chosen) if chosen else None


def _win_pick(initial: str) -> Optional[str]:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    BIF_RETURNONLYFSDIRS = 0x0001
    BIF_NEWDIALOGSTYLE = 0x0040

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    try:
        display = ctypes.create_unicode_buffer(260)
        info = BROWSEINFOW()
        info.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = "LaTeX-Projektordner wählen"
        info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            if not shell32.SHGetPathFromIDListW(pidl, path):
                return None
            return path.value or None
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        ole32.CoUninitialize()


def _pickers() -> list[Callable[[str], Optional[str]]]:
    pickers: list[Callable[[str], Optional[str]]] = []
    try:
        import tkinter  # noqa: F401

        pickers.append(_tk_pick)
    except Exception:  # ImportError in the frozen build, TclError setups
        pass
    if sys.platform == "win32":
        pickers.append(_win_pick)
    return pickers


def pick_folder(initial: str = "", *, pickers: Optional[list] = None,
                timeout: float = PICK_TIMEOUT_SECONDS) -> Optional[str]:
    """Open the native folder dialog; the chosen path, or ``None`` when the
    user cancelled. Each picker runs in its own thread (GUI toolkits want
    their whole life on one thread). Raises :class:`PickerUnavailable` when
    no picker works, :class:`PickerBusy` while another dialog is open."""
    candidates = _pickers() if pickers is None else pickers
    if not candidates:
        raise PickerUnavailable("no folder dialog available")
    if not _pick_lock.acquire(blocking=False):
        raise PickerBusy("a folder dialog is already open")
    try:
        for picker in candidates:
            box: dict = {}

            def run(picker=picker, box=box) -> None:
                try:
                    box["value"] = picker(initial)
                except Exception as exc:  # no display, no Tcl, COM failure
                    box["error"] = exc

            thread = threading.Thread(target=run, name="lacite-pick-folder", daemon=True)
            thread.start()
            thread.join(timeout)
            if thread.is_alive():
                raise PickerUnavailable("folder dialog did not return")
            if "error" not in box:
                return box.get("value")
        raise PickerUnavailable("every folder dialog failed")
    finally:
        _pick_lock.release()


def open_folder(folder: str) -> None:
    """Show ``folder`` in the system file manager. Raises ``FileNotFoundError``
    for a missing folder, ``OSError`` when the system cannot open it."""
    if not folder or not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    if sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise OSError(f"{opener} not available") from exc
