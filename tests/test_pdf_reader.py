import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium

from pdf_fixtures import make_pdf, png_pixels

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if importlib.util.find_spec('pdf_reader'):
    import pdf_reader
else:
    pdf_reader = None


class PdfReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pdf_reader, 'T12 pdf_reader is missing')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'generated.pdf'
        self.path.write_bytes(make_pdf())

    def test_mixed_pdf_keeps_all_pages(self):
        pages = pdf_reader.read_pdf(self.path, lambda: False)
        self.assertEqual([page.page_number for page in pages], [1, 2, 3, 4])
        for page in pages:
            self.assertTrue(page.needs_vision)
            self.assertFalse(page.readable)  # T12 has not performed OCR/vision.
            width, height, pixels = png_pixels(page.image_png)
            self.assertEqual((width, height), (400, 400))
            if page.page_number == 2:
                self.assertEqual(page.text, '')
            else:
                self.assertIn(f'Page {page.page_number}', page.text)
                self.assertIn('Product', page.text)
                self.assertIn('Loan', page.text)
            if page.page_number != 1:
                # Real image content survives, with RGB order, in scan/mixed/overlay.
                self.assertIn(b'\xff\x00\x00' * 20, pixels)
                self.assertIn(b'\x00\x00\xff' * 20, pixels)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_more_than_100_pages_and_blank_page_are_preserved(self):
        self.path.write_bytes(make_pdf(('blank',) * 101))
        pages = pdf_reader.read_pdf(self.path, lambda: False)
        self.assertEqual(len(pages), 101)
        self.assertEqual(pages[-1].page_number, 101)
        self.assertEqual(pages[-1].text, '')
        self.assertTrue(pages[-1].image_png)

    def test_corrupt_missing_and_encrypted_are_explicit_errors(self):
        for data, code in [(b'%PDF-1.4\nbroken', 'parse'),
                           (make_pdf(password='secret'), 'encrypted'),
                           (make_pdf(password=''), 'encrypted'),
                           (make_pdf(()), 'empty')]:
            self.path.write_bytes(data)
            with self.subTest(code=code), self.assertRaises(pdf_reader.PdfReadError) as caught:
                pdf_reader.read_pdf(self.path, lambda: False)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn(str(self.path), str(caught.exception))
        self.path.unlink()
        with self.assertRaises(pdf_reader.PdfReadError) as caught:
            pdf_reader.read_pdf(self.path, lambda: False)
        self.assertEqual(caught.exception.code, 'parse')

    def test_broken_middle_page_fails_instead_of_returning_partial_pages(self):
        self.path.write_bytes(make_pdf(broken_page=2))
        with self.assertRaises(pdf_reader.PdfReadError) as caught:
            pdf_reader.read_pdf(self.path, lambda: False)
        self.assertEqual(caught.exception.page_number, 2)
        self.assertEqual(caught.exception.code, 'page')

    def test_extraction_and_render_failures_close_document(self):
        for method in ['get_textpage', 'render']:
            documents = []
            original = pdfium.PdfDocument
            def open_document(*args):
                document = original(*args)
                documents.append(document)
                return document
            with self.subTest(method=method), patch.object(pdf_reader.pdfium, 'PdfDocument',
                    side_effect=open_document), patch.object(pdfium.PdfPage, method,
                    side_effect=pdfium.PdfiumError('sensitive native message')):
                with self.assertRaises(pdf_reader.PdfReadError) as caught:
                    pdf_reader.read_pdf(self.path, lambda: False)
                self.assertEqual(caught.exception.page_number, 1)
                self.assertNotIn('sensitive', str(caught.exception))
            self.assertTrue(all(not document.raw for document in documents))

    def test_cancel_before_open_and_between_pages(self):
        with patch.object(pdf_reader.pdfium, 'PdfDocument') as open_document:
            with self.assertRaises(asyncio.CancelledError):
                pdf_reader.read_pdf(self.path, lambda: True)
            open_document.assert_not_called()
        loaded, rendered, documents = [], [], []
        original = pdfium.PdfDocument.get_page
        original_render = pdfium.PdfPage.render
        original_open = pdfium.PdfDocument
        def open_document(*args):
            document = original_open(*args)
            documents.append(document)
            return document
        def get_page(document, index):
            loaded.append(index)
            return original(document, index)
        def render(page, **kwargs):
            bitmap = original_render(page, **kwargs)
            rendered.append(True)
            return bitmap
        def cancelled():
            return bool(rendered)
        with patch.object(original_open, 'get_page', get_page), patch.object(
                pdfium.PdfPage, 'render', render), patch.object(
                pdf_reader.pdfium, 'PdfDocument', side_effect=open_document):
            with self.assertRaises(asyncio.CancelledError):
                pdf_reader.read_pdf(self.path, cancelled)
        self.assertEqual(loaded, [0])
        self.assertEqual(rendered, [True])
        self.assertTrue(all(not document.raw for document in documents))

    def test_cancel_while_waiting_for_pdfium_lock(self):
        calls = []
        def cancelled():
            calls.append(True)
            return len(calls) > 1
        with patch.object(pdf_reader, '_pdfium_lock') as lock:
            lock.acquire.return_value = False
            with self.assertRaises(asyncio.CancelledError):
                pdf_reader.read_pdf(self.path, cancelled)
            lock.release.assert_not_called()

    def test_raised_cancellation_is_not_converted_to_processing_error(self):
        def cancelled():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            pdf_reader.read_pdf(self.path, cancelled)


if __name__ == '__main__':
    unittest.main()
