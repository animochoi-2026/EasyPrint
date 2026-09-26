"""Fetch the exact unmodified upstream portable PDF print engine."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

VERSION = '3.6.1'
EXPECTED_EXE_SHA256 = '719f689b34f47be8ca105ce8484948474dafde0e106bab599e4a89326070c3d0'
URL = 'https://www.sumatrapdfreader.org/dl/rel/3.6.1/SumatraPDF-3.6.1-64.zip'


def main():
    target = Path(__file__).resolve().parents[1] / 'assets' / 'SumatraPDF.exe'
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != EXPECTED_EXE_SHA256:
            raise RuntimeError('Existing SumatraPDF.exe differs from the pinned official version; not overwriting')
        print('SumatraPDF 3.6.1 verified')
        return
    with urllib.request.urlopen(URL, timeout=60) as response:
        payload = response.read(32 * 1024 * 1024)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = [n for n in archive.namelist() if n.lower().endswith('.exe')]
        if len(files) != 1:
            raise RuntimeError('Unexpected Sumatra archive')
        content = archive.read(files[0])
    if hashlib.sha256(content).hexdigest() != EXPECTED_EXE_SHA256:
        raise RuntimeError('Official Sumatra download failed the pinned checksum')
    target.parent.mkdir(exist_ok=True)
    with target.open('xb') as output:
        output.write(content)
    print('SumatraPDF downloaded and verified')


if __name__ == '__main__':
    main()
