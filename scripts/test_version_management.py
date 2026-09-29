"""Version selection, explicit rollback and conservative retention; no real printing."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_updates import payload, release_data
from src import updates as up
from src import update_installer as installer
from src.update_cleanup import prune_backups, prune_finished_downloads
from src.update_ui import UpdateUI
from src.version import APP_VERSION


def release(version):
    return up.parse_release(release_data(version), None)


class VersionManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='easyprint_versions_test_')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def backup(self, target, version, suffix=None):
        transaction = target / ('.easyprint_update_' + (suffix or version))
        payload(transaction / 'backup', version, b'backup')
        (transaction / 'new').mkdir()
        return transaction

    def test_choices_are_numeric_nearest_previous_not_api_order(self):
        choices = up.version_choices([release(v) for v in ['1.10.0', '1.0.0', '1.8.0', '1.9.0']], '1.9.0')
        self.assertEqual(choices.latest.version, '1.10.0')
        self.assertEqual(choices.previous.version, '1.8.0')
        self.assertEqual(choices.retained_versions, {'1.10.0', '1.9.0', '1.8.0'})
        self.assertIsNone(up.version_choices([release('1.0.0')], '1.0.0').previous)

    def test_current_release_is_also_latest_without_duplicate_storage(self):
        choices = up.version_choices([release('1.0.2'), release('1.0.1')], '1.0.2')
        self.assertEqual(choices.retained_versions, {'1.0.2', '1.0.1'})

    def test_catalog_paginates_ignores_nonstable_and_sorts(self):
        first = [dict(release_data('2.0.0'), prerelease=True) for _ in range(100)]
        second = [release_data('1.0.1'), release_data('1.0.2'), {'tag_name': 'unrelated'}]
        with patch.object(up, '_open', side_effect=[io.BytesIO(json.dumps(first).encode()), io.BytesIO(json.dumps(second).encode())]) as opened:
            self.assertEqual([r.version for r in up.list_releases()], ['1.0.2', '1.0.1'])
            self.assertIn('page=2', opened.call_args.args[0])

    def test_invalid_stable_release_blocks_catalog_and_cleanup(self):
        data = release_data('1.0.1')
        data['assets'][0]['digest'] = None
        with patch.object(up, '_open', return_value=io.BytesIO(json.dumps([data]).encode())):
            with self.assertRaises(ValueError):
                up.list_releases()

    def test_explicit_rollback_preserves_user_data_and_newer_backup(self):
        target = payload(self.root / 'installed', '1.0.2', b'newer')
        source = payload(self.root / 'payload', '1.0.1', b'older')
        for name in ['settings.json', 'queue_state.json', 'print.log', 'document.pdf']:
            (target / name).write_bytes(b'keep')
        backup = installer.install_payload(source, target, '1.0.1', rollback=True, expected_current='1.0.2')
        self.assertEqual((target / 'EasyPrint.exe').read_bytes(), b'older')
        self.assertEqual((backup / 'EasyPrint.exe').read_bytes(), b'newer')
        for name in ['settings.json', 'queue_state.json', 'print.log', 'document.pdf']:
            self.assertEqual((target / name).read_bytes(), b'keep')

    def test_rollback_requires_expected_current_and_strictly_lower_version(self):
        target = payload(self.root / 'installed', '1.0.2')
        for index, (version, expected) in enumerate([('1.0.1', None), ('1.0.1', '1.0.3'), ('1.0.2', '1.0.2'), ('1.0.3', '1.0.2')]):
            source = payload(self.root / str(index), version)
            with self.assertRaises(ValueError):
                installer.install_payload(source, target, version, rollback=True, expected_current=expected)
        self.assertFalse(list(target.glob('.easyprint_update_*')))

    def test_rollback_failure_restores_current_version(self):
        target = payload(self.root / 'installed', '1.0.2', b'current')
        source = payload(self.root / 'payload', '1.0.1')
        replace = os.replace
        failed = False
        def locked(src, dst):
            nonlocal failed
            if not failed and Path(src).name == '_internal' and Path(src).parent.name == 'new':
                failed = True
                raise PermissionError('locked')
            return replace(src, dst)
        with patch.object(installer.os, 'replace', side_effect=locked), self.assertRaises(PermissionError):
            installer.install_payload(source, target, '1.0.1', rollback=True, expected_current='1.0.2')
        self.assertEqual((target / 'EasyPrint.exe').read_bytes(), b'current')
        self.assertEqual(json.loads((target / up.MANIFEST).read_text())['version'], '1.0.2')
        self.assertEqual(prune_backups(target, {'1.0.2'}, '1.0.2'), [])
        self.assertEqual(len(list(target.glob('.easyprint_update_*'))), 1)

    def test_only_obsolete_verified_backups_removed(self):
        target = payload(self.root / 'installed', '1.0.2')
        versions = ['1.0.0', '1.0.1', '1.0.2', '1.0.3']
        paths = {v: self.backup(target, v) for v in versions}
        (target / 'personal.pdf').write_bytes(b'user')
        removed = prune_backups(target, {'1.0.1', '1.0.2', '1.0.3'}, '1.0.2')
        self.assertEqual(removed, ['1.0.0'])
        self.assertFalse(paths['1.0.0'].exists())
        self.assertTrue(all(paths[v].exists() for v in versions[1:]))
        self.assertEqual((target / 'personal.pdf').read_bytes(), b'user')

    def test_unknown_damaged_and_incomplete_transactions_are_preserved(self):
        target = payload(self.root / 'installed', '1.0.2')
        paths = [self.backup(target, '1.0.0', str(i)) for i in range(5)]
        (paths[0] / 'user.txt').write_text('keep')
        (paths[1] / 'backup/EasyPrint.exe').write_bytes(b'tampered')
        (paths[2] / 'new/partial.tmp').write_text('recovery')
        (paths[3] / 'backup/personal.pdf').write_text('user')
        (paths[4] / 'completed.json').write_text(json.dumps({'product': 'Other'}))
        self.assertEqual(prune_backups(target, {'1.0.2'}, '1.0.2'), [])
        self.assertTrue(all(p.exists() for p in paths))

    def test_links_and_wrong_install_version_never_deleted(self):
        target = payload(self.root / 'installed', '1.0.2')
        transaction = self.backup(target, '1.0.0')
        with self.assertRaises(ValueError):
            prune_backups(target, {'1.0.3'}, '1.0.3')
        with patch('src.update_cleanup._plain_tree', return_value=False):
            self.assertEqual(prune_backups(target, {'1.0.2'}, '1.0.2'), [])
        self.assertTrue(transaction.exists())

    def test_cleanup_refuses_to_remove_current_or_four_versions(self):
        target = payload(self.root / 'installed', '1.0.2')
        for keep in [{'1.0.1'}, {'1.0.0', '1.0.1', '1.0.2', '1.0.3'}]:
            with self.assertRaises(ValueError):
                prune_backups(target, keep, '1.0.2')

    def test_finished_temp_cleanup_is_installation_scoped_and_skips_user_data(self):
        for name, owner in [('ours', 'installed'), ('other', 'elsewhere'), ('unknown', 'installed')]:
            work = self.root / ('easyprint_update_' + name)
            work.mkdir()
            (work / 'EasyPrintUpdater.exe').write_bytes(b'helper')
            (work / 'finished.json').write_text(json.dumps({'product': 'EasyPrint', 'target': str((self.root / owner).resolve())}))
        (self.root / 'easyprint_update_unknown/user.txt').write_text('keep')
        with patch('src.update_cleanup.tempfile.gettempdir', return_value=str(self.root)):
            self.assertEqual(prune_finished_downloads(self.root / 'installed'), ['easyprint_update_ours'])
        self.assertTrue((self.root / 'easyprint_update_other').exists())
        self.assertTrue((self.root / 'easyprint_update_unknown/user.txt').exists())

    def test_current_selection_and_same_latest_do_not_reinstall(self):
        ui = object.__new__(UpdateUI)
        ui.dismiss_versions = Mock()
        ui.offer = Mock()
        ui.version_options = {'current': None, 'latest': release(APP_VERSION)}
        ui.version_selection = Mock()
        for selected in ['current', 'latest']:
            ui.version_selection.get.return_value = selected
            ui.apply_selection()
        ui.offer.assert_not_called()

    def test_ui_passes_explicit_rollback_and_post_install_retention(self):
        app = SimpleNamespace(_in_progress=False, _schedule_timer=None, update_btn=Mock(), after=Mock(), items=[], list_view=Mock())
        with patch('src.update_ui.is_frozen', return_value=False):
            ui = UpdateUI(app)
        ui.work = self.root
        ui.releases = [release('1.0.2'), release('1.0.1'), release('1.0.0')]
        with patch('src.update_ui.launch_installer', return_value=Mock()) as launch:
            ui.prepare_restart(release('1.0.1'))
        self.assertTrue(launch.call_args.kwargs['rollback'])
        self.assertEqual(launch.call_args.kwargs['expected_current'], APP_VERSION)
        self.assertEqual(launch.call_args.kwargs['retained_versions'], {'1.0.2', '1.0.1', '1.0.0'})

    def test_launch_job_contains_consent_and_retention(self):
        work = self.root / 'work'
        target = self.root / 'target'
        work.mkdir()
        target.mkdir()
        helper = self.root / 'helper.exe'
        helper.write_bytes(b'helper')
        with patch.object(installer.subprocess, 'Popen'):
            installer.launch_installer(work, target, helper, '1.0.1', rollback=True, expected_current='1.0.2', retained_versions={'1.0.2', '1.0.1', '1.0.0'})
        job = json.loads((work / 'job.json').read_text())
        self.assertTrue(job['rollback'])
        self.assertEqual(job['expected_current'], '1.0.2')
        self.assertEqual(job['retained_versions'], ['1.0.0', '1.0.1', '1.0.2'])

    def test_successful_job_prunes_only_after_install_and_restarts(self):
        target = payload(self.root / 'installed', '1.0.1')
        obsolete = self.backup(target, '1.0.0')
        work = self.root / 'job'
        payload(work / 'payload', '1.0.2')
        (work / 'release.zip').write_bytes(b'archive')
        job = work / 'job.json'
        job.write_text(json.dumps({'target': str(target), 'version': '1.0.2', 'pid': 1,
                                   'expected_current': '1.0.1', 'retained_versions': ['1.0.2', '1.0.1']}))
        kernel = Mock()
        kernel.WaitForSingleObject.return_value = 0
        with patch.object(installer, '_parent_handle', return_value=(kernel, 1)), patch.object(installer.subprocess, 'Popen') as start:
            installer.apply_job(job)
        self.assertFalse(obsolete.exists())
        self.assertTrue((work / 'finished.json').is_file())
        self.assertFalse((work / 'payload').exists())
        self.assertFalse((work / 'release.zip').exists())
        self.assertEqual(json.loads((target / up.MANIFEST).read_text())['version'], '1.0.2')
        start.assert_called_once()

    def test_failed_install_job_never_prunes(self):
        target = payload(self.root / 'installed', '1.0.2')
        old = self.backup(target, '1.0.0')
        work = self.root / 'job'
        payload(work / 'payload', '1.0.1')
        job = work / 'job.json'
        job.write_text(json.dumps({'target': str(target), 'version': '1.0.1', 'pid': 1,
                                   'retained_versions': ['1.0.1']}))
        kernel = Mock()
        kernel.WaitForSingleObject.return_value = 0
        with patch.object(installer, '_parent_handle', return_value=(kernel, 1)), patch.object(installer.subprocess, 'Popen') as start:
            with self.assertRaises(ValueError):
                installer.apply_job(job)
        self.assertTrue(old.exists())
        self.assertTrue((work / 'payload').exists())
        self.assertFalse((work / 'finished.json').exists())
        start.assert_not_called()

    def test_real_dialog_has_three_choices_and_missing_previous_disabled(self):
        app = tk.Tk()
        app.withdraw()
        app._in_progress = False
        app._schedule_timer = None
        app.update_btn = ttk.Button(app)
        try:
            with patch('src.update_ui.is_frozen', return_value=False):
                ui = UpdateUI(app)
            ui.show_versions(up.version_choices([release(APP_VERSION)]))
            app.update_idletasks()
            body = ui.dialog.winfo_children()[0]
            buttons = [w for w in body.winfo_children() if isinstance(w, ttk.Radiobutton)]
            self.assertEqual(len(buttons), 3)
            self.assertTrue(buttons[2].instate(['disabled']))
            self.assertEqual(ui.version_selection.get(), 'current')
            buttons[0].invoke()
            with patch.object(ui, 'offer') as offer:
                ui.apply_selection()
                offer.assert_not_called()
            ui.close()
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()
