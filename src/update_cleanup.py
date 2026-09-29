"""Conservative cleanup of completed EasyPrint transactions, never user files."""
import json
from pathlib import Path
import shutil
import tempfile

from .updates import MANIFEST, verify_payload, version_tuple


def _plain_tree(path):
    return not any(p.is_symlink() or p.is_junction() for p in [path, *path.rglob('*')])


def prune_backups(target, retained_versions, current):
    """Only complete, verified app backups directly inside this installation."""
    target = Path(target).resolve()
    keep = {version_tuple(v) for v in retained_versions}
    if version_tuple(current) not in keep or len(keep) > 3:
        raise ValueError("백업 보존 버전이 올바르지 않습니다.")
    installed = json.loads((target / MANIFEST).read_text(encoding='utf-8'))
    if installed.get('product') != 'EasyPrint' or installed.get('version') != current:
        raise ValueError("현재 설치 버전을 확인하지 못해 정리를 중지했습니다.")
    removed = []
    for transaction in target.glob('.easyprint_update_*'):
        try:
            if not transaction.is_dir() or not _plain_tree(transaction) or transaction.resolve().parent != target:
                continue
            if {p.name for p in transaction.iterdir()} - {'backup', 'new', 'completed.json'}:
                continue
            staged = transaction / 'new'
            if staged.exists() and (not staged.is_dir() or any(staged.iterdir())):
                continue  # Incomplete/failed transactions are recovery data.
            marker = transaction / 'completed.json'
            if marker.exists():
                state = json.loads(marker.read_text(encoding='utf-8'))
                if state.get('product') != 'EasyPrint' or state.get('target') != str(target):
                    continue
            elif not staged.is_dir():
                continue  # Legacy installer leaves an empty new/ after success.
            backup = transaction / 'backup'
            manifest = json.loads((backup / MANIFEST).read_text(encoding='utf-8'))
            version = manifest.get('version')
            if version_tuple(version) in keep:
                continue
            verify_payload(backup, version)
            # Exact resolved child, no junctions, only verified app-owned content.
            shutil.rmtree(transaction)
            removed.append(version)
        except (OSError, ValueError, TypeError):
            continue  # Locked, unrecognized or damaged data stays untouched.
    return removed


def prune_finished_downloads(target):
    """Delete only marked successful jobs for this installation, after helper exit."""
    target = str(Path(target).resolve())
    temp = Path(tempfile.gettempdir()).resolve()
    removed = []
    for work in temp.glob('easyprint_update_*'):
        try:
            marker = work / 'finished.json'
            if not marker.is_file() or not _plain_tree(work) or work.resolve().parent != temp:
                continue
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data.get('product') != 'EasyPrint' or data.get('target') != target:
                continue
            allowed = {'finished.json', 'job.json', 'ready', 'cancel', 'EasyPrintUpdater.exe'}
            if {p.name for p in work.iterdir()} - allowed or any(p.is_dir() for p in work.iterdir()):
                continue
            # On Windows an executing helper cannot be removed. Leave all else
            # until it exits; retry at the next version check.
            helper = work / 'EasyPrintUpdater.exe'
            helper.unlink(missing_ok=True)
            shutil.rmtree(work)
            removed.append(work.name)
        except (OSError, ValueError, TypeError):
            continue
    return removed
