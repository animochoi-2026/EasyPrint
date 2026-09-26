"""Regression checks; never launches a printer or an application worker."""
import sys
import threading
import tkinter as tk
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.app import App
from src.models import PrintItem
from src.print_list_view import PrintListView
from src import printing


class PageRangeCommitTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.item = PrintItem('sample.pdf', page_count=10)
        self.view = PrintListView(
            self.root,
            on_range_change=lambda i, v: setattr(i, 'page_range', v),
            on_copies_change=self.set_copies,
        )
        self.view.set_items([self.item])
        self.app = SimpleNamespace(
            _in_progress=False, _lock=threading.Lock(), items=[self.item],
            list_view=self.view, _cancel_schedule_timer=Mock(),
            control_panel=Mock(), _abort_event=threading.Event(),
            _worker_loop=Mock(),
            _current_printer=Mock(return_value="Test printer"),
            reprint_one_btn=Mock(), reprint_from_btn=Mock(),
        )

    def tearDown(self):
        self.root.destroy()

    def set_copies(self, item, value):
        item.copies = max(1, value)
        self.view.refresh_item(item.id)

    @property
    def row(self):
        return self.view._rows[self.item.id]

    def start(self):
        with patch('src.app.threading.Thread') as thread, patch('src.app.log_event'):
            App._on_start_clicked(self.app)
            return thread.called

    def test_start_commits_without_enter_and_sends_range(self):
        self.row.range_var.set('2-3,5')
        self.row.copies_var.set('2')
        self.assertTrue(self.start())
        self.assertEqual(self.item.page_range, '2-3,5')
        self.assertEqual(self.item.copies, 2)
        with patch.object(printing, 'get_sumatra_path', return_value=sys.executable), \
             patch.object(printing, 'prepare_print_path', return_value=nullcontext('local_fast.pdf')), \
             patch.object(printing, 'log_event'), \
             patch.object(printing, 'run_print_process', return_value=(True, None, False)) as run:
            printing.print_pdf(self.item.file_path, None, self.item.copies, self.item.page_range)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index('-print-settings') + 1], '2-3,5,2x')
            self.assertEqual(args[-1], 'local_fast.pdf')

    def test_invalid_ranges_block_worker(self):
        for raw in ('2~3', '11', '4-2', '1,,3'):
            with self.subTest(raw=raw), patch('src.print_list_view.messagebox.showwarning') as warning:
                self.row.range_var.set(raw)
                self.assertFalse(self.start())
                warning.assert_called_once()
                self.assertEqual(self.row.range_var.get(), raw)
                self.assertFalse(self.app._in_progress)

    def test_refresh_and_rebuild_preserve_invalid_edit(self):
        self.row.range_var.set('2~3')
        self.view.refresh_item(self.item.id)
        self.view.set_items([self.item])
        self.assertEqual(self.row.range_var.get(), '2~3')
        with patch('src.print_list_view.messagebox.showwarning'):
            self.assertFalse(self.start())

    def test_invalid_edit_does_not_print_previous_range(self):
        self.row.range_var.set('2-3')
        self.row._emit_range_change()
        self.row.range_var.set('2~3')
        with patch('src.print_list_view.messagebox.showwarning'):
            self.assertFalse(self.start())

    def test_explicit_empty_range_still_means_all(self):
        self.assertTrue(self.start())
        self.assertEqual(self.item.page_range, '')


if __name__ == '__main__':
    unittest.main()
