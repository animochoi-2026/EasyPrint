"""Keep dependency attribution/license files alongside packaged binaries."""
from importlib import metadata
from pathlib import Path
import shutil
import sys


def collect(app_folder):
    root = Path(app_folder) / '_internal' / 'licenses'
    for package in ('pymupdf', 'Pillow', 'pypdf', 'pywin32', 'tkinterdnd2', 'pyinstaller'):
        distribution = metadata.distribution(package)
        for relative in distribution.files or []:
            name = relative.name.lower()
            if not any(word in name for word in ('license', 'copying', 'notice', 'copyright')):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
                continue
            clean_parts = [p for p in relative.parts if p not in ('..', '.')]
            target = root.joinpath(package, *clean_parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(python_license, root / 'Python-LICENSE.txt')
    print('Dependency licenses collected')


if __name__ == '__main__':
    collect(sys.argv[1])
