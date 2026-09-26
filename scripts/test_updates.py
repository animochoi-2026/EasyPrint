"""Update security/transaction/UI tests; no network or physical printing."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import updates as up
from src.update_installer import install_payload
from src.update_ui import UpdateUI, persist_update_state
from src.app import App
from src.models import PrintItem
from src.version import UPDATE_REPOSITORY


def payload(folder, version='1.0.1', content=b'new'):
    folder.mkdir(parents=True)
    (folder / '_internal').mkdir()
    (folder / 'EasyPrint.exe').write_bytes(content)
    (folder / '_internal' / 'EasyPrintUpdater.exe').write_bytes(b'helper')
    (folder / 'README.txt').write_text('readme', encoding='utf-8')
    hashes = {p.relative_to(folder).as_posix(): up.file_hash(p) for p in folder.rglob('*') if p.is_file()}
    (folder / up.MANIFEST).write_text(json.dumps({
        'schema': 1, 'product': 'EasyPrint', 'version': version, 'files': hashes,
    }), encoding='utf-8')
    return folder


def release_data(version='1.0.1'):
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False, 'body': 'Changes',
            'assets': [{'name': f'EasyPrint-{version}-windows-x64.zip', 'state': 'uploaded',
                        'browser_download_url': f'https://github.com/{UPDATE_REPOSITORY}/releases/download/v{version}/EasyPrint-{version}-windows-x64.zip',
                        'size': 123, 'digest': 'sha256:' + 'a' * 64}]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='easyprint_update_test_')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_numeric_versions_not_lexical(self):
        self.assertGreater(up.version_tuple('v1.10.0'), up.version_tuple('1.9.0'))
        self.assertEqual(up.version_tuple('1.0.0'), up.version_tuple('1.0.0.0'))
        for value in ['1.0', '1.0.0-beta', '../../1', None]:
            with self.assertRaises(ValueError):
                up.version_tuple(value)

    def test_ignore_current_old_prerelease_and_draft(self):
        self.assertIsNone(up.parse_release(release_data('1.0.0'), '1.0.0'))
        self.assertIsNone(up.parse_release(release_data('0.9.9'), '1.0.0'))
        for key in ['draft', 'prerelease']:
            data = release_data()
            data[key] = True
            self.assertIsNone(up.parse_release(data, '1.0.0'))

    def test_release_requires_exact_asset_and_hash(self):
        self.assertEqual(up.parse_release(release_data(), '1.0.0').version, '1.0.1')
        for key, bad in [('digest', None), ('size', up.MAX_ZIP + 1), ('name', 'other.zip'),
                         ('browser_download_url', 'https://example.com/EasyPrint.zip')]:
            data = release_data()
            data['assets'][0][key] = bad
            with self.assertRaises(ValueError):
                up.parse_release(data, '1.0.0')

    def test_no_zip_slip_or_user_data_overwrite(self):
        for path in ['../escape', '/absolute', 'C:/file', '_internal/../x', '_internal/a:stream',
                     '_internal/CON.txt', '_internal/end.', '_internal/back\\slash', 'settings.json',
                     'queue_state.json', '_internal//double', '_internal/./dot']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                up.safe_member(path)

    def test_zip_traversal_is_rejected_before_extracting(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('EasyPrint/EasyPrint.exe', b'innocent')
            z.writestr('EasyPrint/../escape', b'bad')
        with self.assertRaises(ValueError):
            up.extract_payload(archive, self.root / 'out', '1.0.1')
        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_case_insensitive_duplicate_rejected(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('EasyPrint/_internal/a.dll', b'one')
            z.writestr('EasyPrint/_internal/A.dll', b'two')
        with self.assertRaises(ValueError):
            up.extract_payload(archive, self.root / 'out', '1.0.1')

    def test_manifest_detects_tampering_extra_and_missing(self):
        source = payload(self.root / 'payload')
        up.verify_payload(source, '1.0.1')
        with self.assertRaises(ValueError):
            up.verify_payload(source, '1.0.2')
        (source / 'EasyPrint.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            up.verify_payload(source, '1.0.1')

    def test_install_preserves_settings_queue_documents_and_backup(self):
        source = payload(self.root / 'payload')
        target = payload(self.root / 'installed', '1.0.0', b'old')
        for name in ['settings.json', 'queue_state.json', 'print.log', 'personal.docx']:
            (target / name).write_bytes(b'user content')
        backup = install_payload(source, target, '1.0.1')
        self.assertEqual((backup / 'EasyPrint.exe').read_bytes(), b'old')
        self.assertEqual((target / 'EasyPrint.exe').read_bytes(), b'new')
        for name in ['settings.json', 'queue_state.json', 'print.log', 'personal.docx']:
            self.assertEqual((target / name).read_bytes(), b'user content')

    def test_failure_rolls_back_old_executable_and_libraries(self):
        source = payload(self.root / 'payload')
        target = payload(self.root / 'installed', '1.0.0', b'old')
        original_replace = os.replace
        failed = False
        def locked(src, dst):
            nonlocal failed
            if not failed and Path(src).name == '_internal' and Path(src).parent.name == 'new':
                failed = True
                raise PermissionError('simulated file lock')
            return original_replace(src, dst)
        with patch('src.update_installer.os.replace', side_effect=locked), self.assertRaises(PermissionError):
            install_payload(source, target, '1.0.1')
        self.assertTrue(failed)
        self.assertEqual((target / 'EasyPrint.exe').read_bytes(), b'old')
        up.verify_payload(target / next(p.name for p in target.iterdir() if p.name.startswith('.easyprint_update_')) / 'new', '1.0.1')
        self.assertEqual(json.loads((target / up.MANIFEST).read_text())['version'], '1.0.0')

    def test_downgrade_blocked(self):
        source = payload(self.root / 'payload', '1.0.0')
        target = payload(self.root / 'installed', '1.0.1', b'old')
        with self.assertRaises(ValueError):
            install_payload(source, target, '1.0.0')
        self.assertEqual((target / 'EasyPrint.exe').read_bytes(), b'old')

    def test_download_hash_mismatch_never_extracts(self):
        release = up.parse_release(release_data(), '1.0.0')
        with patch.object(up, '_open', return_value=io.BytesIO(b'x' * 123)), \
             patch.object(up, 'extract_payload') as extract:
            with self.assertRaises(ValueError):
                up.download_update(release)
            extract.assert_not_called()

    def test_download_cancel_never_installs(self):
        release = up.parse_release(release_data(), '1.0.0')
        cancel = threading.Event()
        cancel.set()
        with patch.object(up, '_open', return_value=io.BytesIO(b'x' * 123)), self.assertRaises(RuntimeError):
            up.download_update(release, cancel=cancel)

    def test_valid_download_and_verified_extraction(self):
        source = payload(self.root / 'payload')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for path in source.rglob('*'):
                if path.is_file():
                    archive.write(path, 'EasyPrint/' + path.relative_to(source).as_posix())
        data = buffer.getvalue()
        meta = release_data()
        meta['assets'][0]['size'] = len(data)
        meta['assets'][0]['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()
        with patch.object(up, '_open', return_value=io.BytesIO(data)):
            work = up.download_update(up.parse_release(meta, '1.0.0'))
        try:
            up.verify_payload(work / 'payload', '1.0.1')
        finally:
            import shutil
            shutil.rmtree(work)

    def test_update_ui_blocks_printing_and_scheduled_checks(self):
        for printing, scheduled in [(True, None), (False, object())]:
            app = SimpleNamespace(_in_progress=printing, _schedule_timer=scheduled, update_btn=Mock(), after=Mock())
            with patch('src.update_ui.is_frozen', return_value=False):
                ui = UpdateUI(app)
            with patch('src.update_ui.messagebox.showinfo') as info, patch('src.update_ui.threading.Thread') as thread:
                ui.check(True)
                info.assert_called_once()
                thread.assert_not_called()

    def test_no_install_without_user_consent(self):
        app = SimpleNamespace(_in_progress=False, _schedule_timer=None, update_btn=Mock(), after=Mock())
        with patch('src.update_ui.is_frozen', return_value=True):
            ui = UpdateUI(app)
            with patch('src.update_ui.messagebox.askyesno', return_value=False), patch('src.update_ui.threading.Thread') as thread:
                ui.offer(up.parse_release(release_data(), '1.0.0'))
                thread.assert_not_called()

    def test_late_cancel_and_invalid_pending_options_never_launch_helper(self):
        app = SimpleNamespace(_in_progress=False, _schedule_timer=None, update_btn=Mock(),
                              after=Mock(), items=[PrintItem('file.pdf')], list_view=Mock())
        with patch('src.update_ui.is_frozen', return_value=False):
            ui = UpdateUI(app)
        with patch('src.update_ui.launch_installer') as launch:
            ui.cancel.set()
            ui.prepare_restart(up.parse_release(release_data(), '1.0.0'))
            launch.assert_not_called()
            ui.cancel.clear()
            app.list_view.commit_pending_options.return_value = False
            ui.prepare_restart(up.parse_release(release_data(), '1.0.0'))
            launch.assert_not_called()
            app.list_view.commit_pending_options.assert_called_once_with({app.items[0].id})

    def test_update_resume_restores_queue_without_auto_print_or_crash_prompt(self):
        item = PrintItem('local.pdf')
        app = SimpleNamespace(_settings={'update_resume': True}, items=[])
        with patch('src.app.load_queue_snapshot', return_value=[item]), patch('src.app.clear_queue_snapshot'), \
             patch('src.app.save_settings'), patch('src.app.messagebox.askyesno') as prompt:
            App._check_crash_recovery(app)
        prompt.assert_not_called()
        self.assertEqual(app.items, [item])
        self.assertNotIn('update_resume', app._settings)

    def test_update_state_is_saved_without_silent_errors(self):
        app = SimpleNamespace(_settings={'existing': 1}, items=[PrintItem('한글 이름.pdf', copies=2)],
                              geometry=lambda: '1140x780')
        with patch('src.update_ui.get_base_dir', return_value=str(self.root)):
            persist_update_state(app)
        self.assertEqual(json.loads((self.root / 'queue_state.json').read_text(encoding='utf-8'))[0]['copies'], 2)
        self.assertTrue(json.loads((self.root / 'settings.json').read_text(encoding='utf-8'))['update_resume'])
        with patch('src.update_ui.get_base_dir', return_value=str(self.root)), \
             patch('src.update_ui.os.replace', side_effect=PermissionError('read only')), self.assertRaises(PermissionError):
            persist_update_state(app)


if __name__ == '__main__':
    unittest.main()
