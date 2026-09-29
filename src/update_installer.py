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
import time

from .updates import MANIFEST, ROOT_FILES, verify_payload, version_tuple


def install_payload(payload, target, version, *, rollback=False, expected_current=None):
    payload, target = Path(payload).resolve(), Path(target).resolve()
    verify_payload(payload, version)
    if not (target / "EasyPrint.exe").is_file() or not (target / "_internal").is_dir():
        raise ValueError("기존 EasyPrint 설치 폴더를 확인할 수 없습니다.")
    if target == Path(target.anchor) or target == Path.home() or payload == target:
        raise ValueError("안전하지 않은 설치 대상입니다.")
    old_manifest = target / MANIFEST
    if old_manifest.exists():
        old = json.loads(old_manifest.read_text(encoding="utf-8"))
        if expected_current is not None and old.get("version") != expected_current:
            raise ValueError("설치 버전이 변경되었습니다. 다시 확인해주세요.")
        if old.get("product") != "EasyPrint":
            raise ValueError("기존 프로그램 정보를 확인할 수 없습니다.")
        if rollback:
            if expected_current is None or version_tuple(version) >= version_tuple(old.get("version")):
                raise ValueError("명시적으로 확인한 이전 버전으로만 롤백할 수 있습니다.")
        elif version_tuple(version) <= version_tuple(old.get("version")):
            raise ValueError("같거나 이전 버전으로 덮어쓰지 않습니다.")
    elif rollback or expected_current is not None:
        raise ValueError("현재 버전 정보가 없어 버전을 변경할 수 없습니다.")
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
    try:
        (transaction / 'completed.json').write_text(json.dumps({
            'product': 'EasyPrint', 'target': str(target), 'version': version,
        }), encoding='utf-8')
    except OSError:
        pass  # Bookkeeping failure must not turn a successful install into failure.
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
        deadline = time.monotonic() + 120
        while True:
            if (work / "cancel").exists():
                return
            result = kernel.WaitForSingleObject(handle, 200)
            if result == 0:
                break
            if result != 258 or time.monotonic() >= deadline:
                raise RuntimeError("EasyPrint가 종료되지 않아 업데이트를 취소했습니다.")
    finally:
        kernel.CloseHandle(handle)
    if (work / "cancel").exists():
        return
    backup = install_payload(work / "payload", target, version,
        rollback=job.get('rollback') is True, expected_current=job.get('expected_current'))
    with (target / "update.log").open("a", encoding="utf-8") as log:
        log.write(f"Installed {version}; previous files: {backup}\n")
    # Cleanup is best effort only after successful replacement; failed jobs keep
    # every recovery file. Never let a cleanup error prevent restarting the app.
    try:
        from .update_cleanup import prune_backups
        keep = job.get('retained_versions')
        if keep:
            removed = prune_backups(target, keep, version)
            with (target / 'update.log').open('a', encoding='utf-8') as log:
                log.write(f'Removed obsolete backup versions: {removed}\n')
        shutil.rmtree(work / 'payload')  # This job's already verified private payload.
        (work / 'release.zip').unlink(missing_ok=True)
        (work / 'finished.json').write_text(json.dumps({
            'product': 'EasyPrint', 'target': str(target),
        }), encoding='utf-8')
    except Exception as exc:
        with (target / 'update.log').open('a', encoding='utf-8') as log:
            log.write(f'Cleanup deferred: {exc}\n')
    subprocess.Popen([str(target / "EasyPrint.exe")], cwd=target, creationflags=0x08000000)


def launch_installer(work, target, helper, version, *, rollback=False, expected_current=None, retained_versions=None):
    """Use the currently installed trusted helper, not a downloaded program."""
    work = Path(work)
    if not Path(helper).is_file():
        raise ValueError("업데이트 도우미가 없습니다. 배포페이지에서 다시 내려받아주세요.")
    probe = Path(tempfile.mkdtemp(prefix=".easyprint_write_test_", dir=target))
    probe.rmdir()
    shutil.copyfile(helper, work / "EasyPrintUpdater.exe")
    (work / "job.json").write_text(json.dumps({
        "target": str(Path(target).resolve()), "version": version, "pid": os.getpid(),
        "rollback": rollback, "expected_current": expected_current,
        "retained_versions": sorted(retained_versions) if retained_versions else None,
    }), encoding="utf-8")
    return subprocess.Popen([str(work / "EasyPrintUpdater.exe"), str(work / "job.json")],
                            cwd=work, creationflags=0x08000000)


def stop_installer(process, work):
    """Cancel the worker, not only the outer PyInstaller onefile process."""
    (Path(work) / "cancel").write_text("cancel", encoding="ascii")
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=0x08000000, timeout=10)
        process.wait(timeout=5)
