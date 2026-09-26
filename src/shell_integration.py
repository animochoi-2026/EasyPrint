"""Opt-in per-user Explorer verbs. Never changes default file associations."""
from __future__ import annotations
import ctypes
import os
import subprocess
import sys
import winreg
from .constants import get_base_dir, is_frozen
from .models import SUPPORTED_EXTENSIONS

VERB = "EasyPrint.Print"


def launch_command():
    if is_frozen():
        argv = [sys.executable]
    else:
        executable = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        argv = [executable, os.path.join(get_base_dir(), "main.py")]
    return subprocess.list2cmdline(argv) + ' -- "%1"'


def register_menu():
    command = launch_command()
    for ext in SUPPORTED_EXTENSIONS:
        path = rf"Software\Classes\SystemFileAssociations\{ext}\shell\{VERB}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "EasyPrint로 인쇄…")
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Document")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, sys.executable)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def unregister_menu():
    for ext in SUPPORTED_EXTENSIONS:
        path = rf"Software\Classes\SystemFileAssociations\{ext}\shell\{VERB}"
        for target in (path + r"\command", path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, target)
            except FileNotFoundError:
                pass
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
