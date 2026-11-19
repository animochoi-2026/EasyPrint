import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from src import pdf_flatten as pdf


class PdfPreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.original = Path(temp.name) / 'exam.pdf'
        with pymupdf.open() as doc:
            doc.new_page().insert_text((40, 40), 'Exam')
            doc.save(self.original)
        self.cache = Path(pdf._cache_path_for(str(self.original)))

    def test_default_skips_scan_and_cache_write_with_missing_or_stale_cache(self):
        for stale in (False, True):
            if stale:
                self.cache.write_bytes(self.original.read_bytes())
            with self.subTest(stale=stale), patch.object(pdf, 'should_flatten_for_print') as scan, \
                 pdf.prepare_print_path(str(self.original)) as local:
                self.assertNotEqual(Path(local), self.original)
                self.assertEqual(Path(local).read_bytes(), self.original.read_bytes())
                scan.assert_not_called()
                self.assertEqual(self.cache.exists(), stale)
            self.assertFalse(Path(local).exists())

    def test_opt_in_builds_cache_and_default_reuses_it(self):
        with patch.object(pdf, 'should_flatten_for_print', return_value=True), patch.object(pdf, 'log_event'), \
             pdf.prepare_print_path(str(self.original), create_fast_pdf=True) as local:
            self.assertEqual(Path(local).read_bytes(), self.cache.read_bytes())
        for source, enabled in ((self.original, False), (self.original, True), (self.cache, True)):
            with self.subTest(source=source, enabled=enabled), patch.object(pdf, 'should_flatten_for_print') as scan, \
                 pdf.prepare_print_path(str(source), create_fast_pdf=enabled) as local:
                self.assertEqual(Path(local).name, self.cache.name)
                self.assertEqual(Path(local).read_bytes(), self.cache.read_bytes())
                scan.assert_not_called()
