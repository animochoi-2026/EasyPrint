"""Verify release ZIPs without shell-dependent Python -c quoting."""
import sys
import zipfile
def verify(path):
    with zipfile.ZipFile(path) as archive:
        required = {'EasyPrint/EasyPrint.exe', 'EasyPrint/README.txt'}
        if not required.issubset(archive.namelist()):
            raise RuntimeError('Release executable or README is missing')
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f'ZIP CRC failed: {bad}')
        print('ZIP integrity OK:', len(archive.namelist()), 'entries')
if __name__ == '__main__':
    verify(sys.argv[1])
