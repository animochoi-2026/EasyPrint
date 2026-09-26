"""Forward Explorer launches to one app in this Windows session/build folder."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import win32api
import win32event
import win32ts
from .constants import get_base_dir


class InstanceInbox:
    def __init__(self):
        key = hashlib.sha256(os.path.normcase(get_base_dir()).encode()).hexdigest()[:20]
        session = win32ts.ProcessIdToSessionId(os.getpid())
        self.root = Path(os.environ['LOCALAPPDATA']) / 'EasyPrint' / f'inbox-{key}-{session}'
        self.root.mkdir(parents=True, exist_ok=True)
        self.handle = win32event.CreateMutex(None, False, f'Local\\EasyPrint-{key}')
        self.primary = win32api.GetLastError() != 183

    def send(self, paths):
        token = uuid.uuid4().hex
        pending = self.root / (token + '.tmp')
        ready = self.root / (token + '.json')
        pending.write_text(json.dumps({'time': time.time(), 'paths': paths}, ensure_ascii=False), encoding='utf-8')
        os.replace(pending, ready)

    def receive(self):
        paths, received = [], False
        for request in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
            try:
                data = json.loads(request.read_text(encoding='utf-8'))
                if 0 <= time.time() - data['time'] < 120:
                    paths.extend(p for p in data['paths'] if isinstance(p, str))
                    received = True
            except (OSError, ValueError, KeyError, TypeError):
                pass
            finally:
                request.unlink(missing_ok=True)
        return received, paths

    def close(self):
        self.handle.Close()
