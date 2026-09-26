"""Regression checks for the September workspace release; no physical printing."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from PIL import Image
from src.app import App
from src.models import PrintItem, PrintStatus, supported_extension
from src.document_printing import image_to_pdf, print_document, _word_print
from src import shell_integration as shell


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='easyprint_test_')
        self.patches = [patch('src.app.load_settings', return_value={}),
                        patch('src.app.load_queue_snapshot', return_value=None),
                        patch('src.app.clear_queue_snapshot'), patch('src.app.save_queue_snapshot'),
                        patch('src.app.save_settings'), patch.object(App, '_load_printers'),
                        patch('src.app.log_event')]
        for p in self.patches:
            p.start()
        self.app = App()
        self.app.withdraw()

    def tearDown(self):
        self.app._in_progress = False
        self.app._on_close_request()
        for p in reversed(self.patches):
            p.stop()
        self.work.cleanup()

    def test_incremental_rows_and_scroll_clamp(self):
        self.app.deiconify()
        for n in range(40):
            self.app.items.append(PrintItem(f'{n}.pdf', page_count=3))
            self.app._rerender()
            self.app.update()
        view = self.app.list_view
        first = view._rows[self.app.items[0].id]
        first.range_var.set('2-3')
        view.canvas.yview_moveto(1)
        self.app.items.append(PrintItem('last.pdf'))
        self.app._rerender()
        self.app.update()
        self.assertIs(first, view._rows[self.app.items[0].id])
        self.assertEqual(first.range_var.get(), '2-3')
        rows = [view._rows[i.id] for i in self.app.items]
        self.assertEqual([int(r.grid_info()['row']) for r in rows], list(range(41)))
        for left, right in zip(rows, rows[1:]):
            self.assertEqual(left.winfo_y() + left.winfo_height(), right.winfo_y())
        self.app.items = self.app.items[:1]
        self.app._rerender()
        self.app.update()
        self.assertEqual(view.canvas.yview(), (0.0, 1.0))
        self.assertEqual(first.winfo_y(), 0)

    def test_reprint_from_selection_keeps_earlier_items_and_printer(self):
        self.app.items = [PrintItem(f'{n}.pdf', status=PrintStatus.DONE, page_count=5) for n in range(4)]
        self.app._rerender()
        self.app.control_panel.printer_var.set('Second printer')
        with patch('src.app.messagebox.askyesno', return_value=True), patch('src.app.threading.Thread'):
            self.app._on_reprint_request(self.app.items[2].id, True)
        self.assertEqual(self.app._run_ids, {i.id for i in self.app.items[2:]})
        self.assertEqual(self.app._run_printer, 'Second printer')
        self.assertTrue(all(i.status == PrintStatus.DONE for i in self.app.items[:2]))
        self.assertTrue(all(i.status == PrintStatus.WAITING for i in self.app.items[2:]))
        self.assertEqual(str(self.app.control_panel.printer_combo['state']), 'disabled')
        self.app._on_worker_finished()
        self.assertEqual(str(self.app.control_panel.printer_combo['state']), 'readonly')

    def test_single_reprint_does_not_include_other_pending(self):
        self.app.items = [PrintItem('pending.pdf'), PrintItem('done.pdf', status=PrintStatus.DONE)]
        self.app._rerender()
        with patch('src.app.messagebox.askyesno', return_value=True), patch('src.app.threading.Thread'):
            self.app._on_reprint_request(self.app.items[1].id, False)
        self.assertEqual(self.app._run_ids, {self.app.items[1].id})

    def test_pdf_page_count_after_add_without_focus(self):
        source = os.path.join(self.work.name, 'count.pdf')
        with pymupdf.open() as pdf:
            for _ in range(7):
                pdf.new_page()
            pdf.save(source)
        self.app.add_files([source])
        deadline = time.monotonic() + 5
        while not self.app.items[0].page_count_checked and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.02)
        item = self.app.items[0]
        self.assertEqual(item.page_count, 7)
        self.assertIn('7', self.app.list_view._rows[item.id].detail_label['text'])

    def test_image_conversion_and_range(self):
        source = os.path.join(self.work.name, 'transparent.png')
        target = os.path.join(self.work.name, 'image.pdf')
        Image.new('RGBA', (400, 200), (0, 0, 0, 0)).save(source)
        image_to_pdf(source, target)
        with pymupdf.open(target) as doc:
            self.assertEqual(doc.page_count, 1)
            self.assertGreater(doc[0].rect.width, doc[0].rect.height)
            pix = doc[0].get_pixmap()
            self.assertEqual(pix.pixel(400, 200), (255, 255, 255))
        with patch('src.document_printing.print_pdf', return_value=(True, None, False)) as printer:
            self.assertFalse(print_document(source, None, page_range='2')[0])
            printer.assert_not_called()
            self.assertTrue(print_document(source, None, copies=3, page_range='1')[0])
            self.assertEqual(printer.call_args.args[2:4], (3, '1'))
            self.assertFalse(os.path.exists(printer.call_args.args[0]))

    def test_word_direct_print_contract_and_cleanup(self):
        word, pipe = Mock(), Mock()
        word.Documents.Open.return_value.ComputeStatistics.return_value = 5
        with patch('win32com.client.DispatchEx', return_value=word), patch('win32process.GetWindowThreadProcessId', return_value=(1, 2)):
            _word_print('source.docx', 'Test printer', 2, '', pipe)
        self.assertEqual(word.AutomationSecurity, 3)
        self.assertTrue(word.Documents.Open.call_args.kwargs['ReadOnly'])
        word.Documents.Open.return_value.ExportAsFixedFormat.assert_not_called()
        word.Documents.Open.return_value.PrintOut.assert_called_once()
        self.assertEqual(word.Documents.Open.return_value.PrintOut.call_args.kwargs['Copies'], 2)
        word.Documents.Open.return_value.Close.assert_called_once_with(SaveChanges=0)
        word.Quit.assert_called_once_with(SaveChanges=0)
        with patch('win32com.client.DispatchEx', side_effect=RuntimeError('not installed')):
            failed = Mock()
            _word_print('source.docx', None, 1, '', failed)
        self.assertEqual(failed.send.call_args.args[0][0], 'unavailable')

    def test_shell_registration_is_scoped_and_quoted(self):
        with patch.object(shell, 'is_frozen', return_value=True), patch.object(shell.sys, 'executable', 'C:\\Some Folder\\EasyPrint.exe'):
            self.assertEqual(shell.launch_command(), '"C:\\Some Folder\\EasyPrint.exe" -- "%1"')
        with patch.object(shell.winreg, 'CreateKey') as create, patch.object(shell.winreg, 'SetValueEx'), patch.object(shell.ctypes, 'windll'):
            shell.register_menu()
            self.assertTrue(all(c.args[0] == shell.winreg.HKEY_CURRENT_USER for c in create.call_args_list))
            self.assertTrue(all('SystemFileAssociations' in c.args[1] for c in create.call_args_list))
        for ext in ('.PDF', '.JPG', '.png', '.docx', '.bmp', '.hwpx'):
            self.assertIsNotNone(supported_extension('file' + ext))

    def test_instance_inbox_collects_files_without_duplicate_windows(self):
        from src.instance import InstanceInbox
        with patch('src.instance.get_base_dir', return_value=self.work.name), patch.dict(os.environ, {'LOCALAPPDATA': self.work.name}):
            first = InstanceInbox()
            second = InstanceInbox()
            try:
                self.assertTrue(first.primary)
                self.assertFalse(second.primary)
                second.send(['C:/문서/한글 공백.pdf', 'C:/photo.jpg'])
                received, paths = first.receive()
                self.assertTrue(received)
                self.assertEqual(paths, ['C:/문서/한글 공백.pdf', 'C:/photo.jpg'])
                self.assertEqual(first.receive(), (False, []))
            finally:
                second.close()
                first.close()

    def test_worker_only_prints_selected_batch(self):
        self.app.items = [PrintItem('before.pdf'), PrintItem('chosen.pdf'), PrintItem('after.pdf')]
        chosen = self.app.items[1]
        self.app._run_ids = {chosen.id}
        def fake_print(item):
            item.status = PrintStatus.DONE
            return None
        with patch.object(self.app, '_print_one', side_effect=fake_print) as printed, patch.object(self.app, 'after'):
            self.app._worker_loop()
        printed.assert_called_once_with(chosen)
        self.assertEqual(self.app.items[0].status, PrintStatus.WAITING)
        self.assertEqual(self.app.items[2].status, PrintStatus.WAITING)

    def test_all_controls_fit_at_minimum_window_size(self):
        self.app.geometry('1040x740')
        self.app.deiconify()
        self.app.update()
        panel = self.app.control_panel
        last = panel.status_count_label2
        self.assertLessEqual(last.winfo_y() + last.winfo_height(), panel.winfo_height())
        self.assertTrue(panel.shell_install_btn.winfo_ismapped())
        self.assertTrue(panel.schedule_cancel_btn.winfo_ismapped())

    def test_drop_tcl_paths_and_no_phantom_items(self):
        from types import SimpleNamespace
        source = os.path.join(self.work.name, '한글 이름 (1).jpg')
        Image.new('RGB', (10, 10)).save(source)
        event = SimpleNamespace(data=self.app.tk.call('list', source))
        self.assertEqual(self.app._on_files_dropped(event), 'copy')
        self.assertEqual(len(self.app.items), 1)
        self.assertEqual(len(self.app.list_view._rows), 1)

    def test_dispatches_images_and_docx_to_document_engine(self):
        self.app._run_printer = 'Second printer'
        for ext in ('.png', '.jpg', '.docx'):
            item = PrintItem('test' + ext, page_range='1', copies=2)
            with patch('src.app.check_file_ready', return_value=(True, None)), patch('src.app.print_document', return_value=(True, None, False)) as document, patch('src.app.print_hwp') as hwp, patch.object(self.app, 'after'):
                self.app._print_one(item)
                self.assertEqual(document.call_args.args[:4], (item.file_path, 'Second printer', 2, '1'))
                hwp.assert_not_called()
                self.assertEqual(item.status, PrintStatus.DONE)


if __name__ == '__main__':
    unittest.main()
