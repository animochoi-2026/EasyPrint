"""Word direct-print regression tests. No real printer or Word is launched."""
import os
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import document_printing as dp
from src.app import App
from src.models import PrintItem, PrintStatus


def fake_word(mapping=None):
    # Each pair is (printed page number, section), in physical-page order.
    mapping = mapping or [(1, 1), (2, 1), (1, 2), (2, 2), (3, 2)]
    word = Mock()
    doc = word.Documents.Open.return_value
    doc.ComputeStatistics.return_value = len(mapping)
    def goto(**kwargs):
        n = kwargs['Count']
        page, section = mapping[n - 1]
        return SimpleNamespace(Information=lambda key: {1: page, 2: section, 3: n}[key])
    doc.GoTo.side_effect = goto
    word.Options.UpdateLinksAtOpen = True
    word.Options.UpdateFieldsAtPrint = True
    word.Options.UpdateLinksAtPrint = True
    word.Options.PrintReverse = True
    return word, doc


class WordDirectTests(unittest.TestCase):
    def run_job(self, word, pages='', copies=1, cancel=None, source='test.docx'):
        pipe = Mock()
        with patch('win32com.client.DispatchEx', return_value=word), \
             patch('win32process.GetWindowThreadProcessId', return_value=(1, 123)):
            dp._word_print(source, 'Chosen printer', copies, pages, pipe, cancel)
        return pipe

    def test_range_uses_physical_pages_across_numbering_restart(self):
        word, doc = fake_word()
        pipe = self.run_job(word, '2-4', copies=3)
        args = doc.PrintOut.call_args.kwargs
        self.assertEqual(args['Pages'], 'p2s1,p1s2-p2s2')
        self.assertEqual(args['Range'], 4)
        self.assertEqual(args['Copies'], 3)
        self.assertTrue(args['Collate'])
        self.assertFalse(args['Background'])
        self.assertFalse(args['PrintToFile'])
        word.WordBasic.FilePrintSetup.assert_called_once_with(Printer='Chosen printer', DoNotSetAsSysDefault=1)
        doc.ExportAsFixedFormat.assert_not_called()
        doc.Repaginate.assert_called_once()
        doc.Close.assert_called_once_with(SaveChanges=0)
        word.Quit.assert_called_once_with(SaveChanges=0)
        self.assertTrue(word.Options.UpdateLinksAtOpen)
        self.assertTrue(word.Options.UpdateFieldsAtPrint)
        self.assertTrue(word.Options.PrintReverse)
        self.assertEqual(pipe.send.call_args.args[0], ('ok', None))

    def test_disjoint_pages_and_all_pages(self):
        for pages, expected, mode in [('1,3,5', 'p1s1,p1s2,p3s2', 4), ('', '', 0)]:
            with self.subTest(pages=pages):
                word, doc = fake_word()
                self.run_job(word, pages)
                self.assertEqual(doc.PrintOut.call_args.kwargs['Pages'], expected)
                self.assertEqual(doc.PrintOut.call_args.kwargs['Range'], mode)

    def test_invalid_ranges_never_fall_back_to_all(self):
        for pages in ['0', '6', '2-6', '4-2', '1,,3', 'abc']:
            with self.subTest(pages=pages):
                word, doc = fake_word()
                pipe = self.run_job(word, pages)
                doc.PrintOut.assert_not_called()
                self.assertEqual(pipe.send.call_args.args[0][0], 'error')
                word.Quit.assert_called_once()

    def test_unresolvable_physical_page_blocks_print(self):
        word, doc = fake_word()
        doc.GoTo.side_effect = None
        doc.GoTo.return_value.Information.side_effect = lambda key: {1: 1, 2: 2, 3: 4}[key]
        pipe = self.run_job(word, '3')
        doc.PrintOut.assert_not_called()
        self.assertIn('3쪽', pipe.send.call_args.args[0][1])

    def test_invalid_copy_count_blocks_print(self):
        for copies in (0, -1, 32768):
            word, doc = fake_word()
            self.run_job(word, copies=copies)
            doc.PrintOut.assert_not_called()

    def test_cancel_before_print(self):
        word, doc = fake_word()
        event = threading.Event()
        doc.Repaginate.side_effect = event.set
        pipe = self.run_job(word, '2', cancel=event)
        doc.PrintOut.assert_not_called()
        self.assertIn('취소', pipe.send.call_args.args[0][1])

    def test_missing_word_is_distinct_from_document_failure(self):
        pipe = Mock()
        with patch('win32com.client.DispatchEx', side_effect=RuntimeError('class not registered')):
            dp._word_print('test.docx', None, 1, '', pipe)
        self.assertEqual(pipe.send.call_args.args[0], ('unavailable', dp.WORD_UNAVAILABLE_MESSAGE))
        word, doc = fake_word()
        word.Documents.Open.side_effect = RuntimeError('bad document')
        pipe = self.run_job(word)
        self.assertEqual(pipe.send.call_args.args[0], ('error', 'bad document'))
        doc.PrintOut.assert_not_called()
        word.Quit.assert_called_once()

    def test_preflight_does_not_open_document_or_change_printer(self):
        word, doc = fake_word()
        pipe = self.run_job(word, source=None)
        word.Documents.Open.assert_not_called()
        word.WordBasic.FilePrintSetup.assert_not_called()
        doc.PrintOut.assert_not_called()
        self.assertEqual(pipe.send.call_args.args[0], ('ok', None))

    def test_print_failure_does_not_retry_or_convert(self):
        word, doc = fake_word()
        doc.PrintOut.side_effect = RuntimeError('spooler failure')
        pipe = self.run_job(word, '2')
        doc.PrintOut.assert_called_once()
        doc.ExportAsFixedFormat.assert_not_called()
        self.assertEqual(pipe.send.call_args.args[0], ('error', 'spooler failure'))

    def test_docx_uses_local_copy_and_removes_it_without_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, '한글 파일.docx')
            source.write_bytes(b'fixture')
            seen = []
            def run(local, printer, copies, page_range, abort, stage):
                self.assertNotEqual(local, str(source))
                self.assertEqual(Path(local).read_bytes(), b'fixture')
                self.assertEqual(os.listdir(os.path.dirname(local)), ['source.docx'])
                self.assertEqual((printer, copies, page_range), ('Chosen', 2, '2-3'))
                seen.append(local)
            with patch.object(dp, '_run_word_job', side_effect=run), patch.object(dp, 'print_pdf') as pdf:
                self.assertEqual(dp.print_document(str(source), 'Chosen', 2, '2-3'), (True, None, False))
                pdf.assert_not_called()
            self.assertFalse(os.path.exists(seen[0]))
            self.assertEqual(source.read_bytes(), b'fixture')

    def test_missing_word_exception_reaches_app_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'test.docx')
            source.touch()
            stage = Mock()
            with patch.object(dp, '_run_word_job', side_effect=dp.WordUnavailableError('missing')) as run:
                with self.assertRaises(dp.WordUnavailableError):
                    dp.print_document(str(source), None, on_stage=stage)
                self.assertFalse(os.path.exists(run.call_args.args[0]))
            stage.assert_called_with(None)

    def test_parent_protocol_preserves_unavailable_type(self):
        for result in [('unavailable', 'missing'), ('error', 'bad document'), ('ok', None)]:
            with self.subTest(result=result):
                messages = deque([result])
                receiver, sender, child, ctx = Mock(), Mock(), Mock(), Mock()
                receiver.poll.side_effect = lambda *args: bool(messages)
                receiver.recv.side_effect = messages.popleft
                child.is_alive.return_value = False
                child.pid = 123
                ctx.Pipe.return_value = receiver, sender
                ctx.Process.return_value = child
                with patch.object(dp.multiprocessing, 'get_context', return_value=ctx):
                    if result[0] == 'ok':
                        dp._run_word_job()
                    else:
                        expected = dp.WordUnavailableError if result[0] == 'unavailable' else RuntimeError
                        with self.assertRaises(expected):
                            dp._run_word_job()
                child.join.assert_called_once()
                receiver.close.assert_called_once()

    def test_pre_cancelled_job_never_starts_child(self):
        event = threading.Event()
        event.set()
        with patch.object(dp.multiprocessing, 'get_context') as context:
            context.return_value.Pipe.return_value = (Mock(), Mock())
            child = context.return_value.Process.return_value
            child.is_alive.return_value = False
            child.pid = None
            with self.assertRaisesRegex(RuntimeError, '취소'):
                dp._run_word_job(abort_event=event)
            child.start.assert_not_called()


class WordBatchTests(unittest.TestCase):
    def make_worker(self, paths):
        items = [PrintItem(path) for path in paths]
        worker = SimpleNamespace(
            items=items, _lock=threading.Lock(), _run_ids={i.id for i in items},
            _run_printer='Chosen printer', _stop_requested=False,
            _abort_event=threading.Event(), _rerender=Mock(),
            _refresh_item_and_counts=Mock(), _set_item_stage_note=Mock(),
            _on_worker_finished=Mock(), _show_printer_fault_dialog=Mock(),
            _show_word_unavailable_dialog=Mock(), _maybe_run_auto_finish_action=Mock(),
        )
        worker.after = lambda _, fn, *args: fn(*args)
        worker._pause_remaining_items = lambda: App._pause_remaining_items(worker)
        worker._print_one = lambda item: App._print_one(worker, item)
        return worker

    def test_missing_word_blocks_entire_mixed_batch_before_any_print(self):
        worker = self.make_worker(['first.pdf', 'second.docx', 'last.jpg'])
        with patch('src.app.check_word_available', side_effect=dp.WordUnavailableError('missing')), \
             patch('src.app.check_file_ready') as ready, patch('src.app.log_event'):
            App._worker_loop(worker)
        ready.assert_not_called()
        self.assertTrue(all(i.status == PrintStatus.PAUSED for i in worker.items))
        worker._show_word_unavailable_dialog.assert_called_once_with('missing')
        worker._on_worker_finished.assert_called_once()
        worker._maybe_run_auto_finish_action.assert_not_called()

    def test_pdf_only_does_not_require_word(self):
        worker = self.make_worker(['first.pdf', 'unselected.docx'])
        worker._run_ids = {worker.items[0].id}
        with patch('src.app.check_word_available') as preflight, \
             patch('src.app.check_file_ready', return_value=(True, None)), \
             patch('src.app.print_pdf', return_value=(True, None, False)), patch('src.app.log_event'):
            App._worker_loop(worker)
        preflight.assert_not_called()
        self.assertEqual(worker.items[0].status, PrintStatus.DONE)
        self.assertEqual(worker.items[1].status, PrintStatus.WAITING)

    def test_word_failure_after_preflight_stops_following_pdf(self):
        worker = self.make_worker(['first.docx', 'next.pdf'])
        with patch('src.app.check_word_available'), patch('src.app.log_event'), \
             patch('src.app.check_file_ready', return_value=(True, None)), \
             patch('src.app.print_document', side_effect=dp.WordUnavailableError('failed later')), \
             patch('src.app.print_pdf') as pdf:
            App._worker_loop(worker)
        pdf.assert_not_called()
        self.assertTrue(all(i.status == PrintStatus.PAUSED for i in worker.items))
        worker._show_word_unavailable_dialog.assert_called_once_with('failed later')
        worker._maybe_run_auto_finish_action.assert_not_called()

    def test_cancel_during_preflight_pauses_without_missing_word_popup(self):
        worker = self.make_worker(['first.docx', 'next.pdf'])
        def cancel(event):
            event.set()
            raise RuntimeError('사용자 요청으로 취소됨')
        with patch('src.app.check_word_available', side_effect=cancel), patch('src.app.log_event'):
            App._worker_loop(worker)
        worker._show_word_unavailable_dialog.assert_not_called()
        self.assertTrue(all(i.status == PrintStatus.PAUSED for i in worker.items))
        worker._maybe_run_auto_finish_action.assert_not_called()

    def test_popup_explains_word_requirement(self):
        worker = self.make_worker([])
        with patch('src.app.messagebox.showwarning') as warning:
            App._show_word_unavailable_dialog(worker, dp.WORD_UNAVAILABLE_MESSAGE)
        self.assertIn('Microsoft Word', warning.call_args.args[1])
        self.assertIn('중지', warning.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
