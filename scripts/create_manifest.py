"""Create a complete, hashed inventory of application-owned release files."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.updates import MANIFEST, file_hash, safe_member, verify_payload
from src.version import APP_VERSION


def create(folder):
    folder = Path(folder)
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if name == MANIFEST:
                continue
            safe_member(name)
            files[name] = file_hash(path)
    (folder / MANIFEST).write_text(json.dumps({
        'schema': 1, 'product': 'EasyPrint', 'version': APP_VERSION, 'files': files,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    verify_payload(folder, APP_VERSION)
    print('Manifest verified:', APP_VERSION, len(files), 'files')


if __name__ == '__main__':
    create(sys.argv[1])
