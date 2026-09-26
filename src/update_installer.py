"""Trusted, separate updater. Replaces app-owned paths only; preserves user data."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .updates import MANIFEST, ROOT_FILES, verify_payload, version_tuple


def install_payload(payload, target, version):
    payload, target = Path(payload).resolve(), Path(target).resolve()
    verify_payload(payload, version)
    if not (target / "EasyPrint.exe").is_file() or not (target / "_internal").is_dir():
        raise ValueError("기존 EasyPrint 설치 폴더를 확인할 수 없습니다.")
    if target == Path(target.anchor) or target == Path.home() or payload == target:
        raise ValueError("안전하지 않은 설치 대상입니다.")
    old_manifest = target / MANIFEST
    if old_manifest.exists():
        old = json.loads(old_manifest.read_text(encoding="utf-8"))
        if old.get("product") != "EasyPrint" or version_tuple(version) <= version_tuple(old.get("version")):
            raise ValueError("같거나 이전 버전으로 덮어쓰지 않습니다.")
    names = sorted({"_internal"} | {p.name for p in payload.iterdir() if p.is_file()})
    for name in names:
        if name != "_internal" and name not in ROOT_FILES:
            raise ValueError("알 수 없는 업데이트 파일입니다.")
        existing = target / name
        if existing.is_symlink() or existing.is_junction():
            raise ValueError("링크로 연결된 프로그램 파일은 자동 업데이트할 수 없습니다.")
    # Backups and staging stay on the target volume, so rename is atomic.
    transaction = Path(tempfile.mkdtemp(prefix=".easyprint_update_", dir=target))
    staged, backup = transaction / "new", transaction / "backup"
    moved_old, moved_new = [], []
    try:
        shutil.copytree(payload, staged)
        verify_payload(staged, version)
        backup.mkdir()
        for name in names:
            if (target / name).exists():
                os.replace(target / name, backup / name)
                moved_old.append(name)
            os.replace(staged / name, target / name)
            moved_new.append(name)
    except Exception:
        # Never delete the old tree. Move new files aside and restore backups.
        for name in reversed(moved_new):
            os.replace(target / name, staged / name)
        for name in reversed(moved_old):
            os.replace(backup / name, target / name)
        raise
    # Keep the previous binaries for recovery. No settings/log/queue moved.
    return backup


def _parent_handle(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        raise OSError(ctypes.get_last_error(), "EasyPrint 프로세스를 확인할 수 없습니다.")
    return kernel, handle


def apply_job(job_path):
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    work = job_path.parent
    target = Path(job["target"]).resolve()
    version = job["version"]
    version_tuple(version)
    verify_payload(work / "payload", version)
    kernel, handle = _parent_handle(int(job["pid"]))
    try:
        (work / "ready").write_text("ready", encoding="ascii")
        # Do not kill the app or replace its files while it's still running.
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError("EasyPrint가 종료되지 않아 업데이트를 취소했습니다.")
    finally:
        kernel.CloseHandle(handle)
    backup = install_payload(work / "payload", target, version)
    with (target / "update.log").open("a", encoding="utf-8") as log:
        log.write(f"Installed {version}; previous files: {backup}\n")
    subprocess.Popen([str(target / "EasyPrint.exe")], cwd=target, creationflags=0x08000000)


def launch_installer(work, target, helper, version):
    """Use the currently installed trusted helper, not a downloaded program."""
    work = Path(work)
    if not Path(helper).is_file():
        raise ValueError("업데이트 도우미가 없습니다. 배포페이지에서 다시 내려받아주세요.")
    probe = Path(tempfile.mkdtemp(prefix=".easyprint_write_test_", dir=target))
    probe.rmdir()
    shutil.copyfile(helper, work / "EasyPrintUpdater.exe")
    (work / "job.json").write_text(json.dumps({
        "target": str(Path(target).resolve()), "version": version, "pid": os.getpid(),
    }), encoding="utf-8")
    return subprocess.Popen([str(work / "EasyPrintUpdater.exe"), str(work / "job.json")],
                            cwd=work, creationflags=0x08000000)

