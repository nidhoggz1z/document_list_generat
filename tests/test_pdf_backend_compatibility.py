import tempfile
import unittest
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from pdf_fixtures import make_pdf


class BackendCompatibilityTests(unittest.TestCase):
    def test_selected_backend_is_pinned(self):
        requirements = (Path(__file__).resolve().parents[1] / 'src/requirements.txt').read_text()
        self.assertIn('pypdfium2==5.14.0', requirements.splitlines())
        self.assertEqual(version('pypdfium2'), '5.14.0')

    def test_selected_backend_extracts_and_renders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'generated.pdf'
            path.write_bytes(make_pdf())
            with pdfium.PdfDocument(path) as document:
                self.assertEqual(len(document), 4)
                for index in range(len(document)):
                    with closing(document[index]) as page, closing(page.get_textpage()) as text:
                        content = text.get_text_bounded(errors='strict')
                        if index == 1:
                            self.assertEqual(content, '')
                        else:
                            self.assertIn(f'Page {index + 1}', content)
                        with closing(page.render(scale=2, force_bitmap_format=raw.FPDFBitmap_BGR,
                                                 rev_byteorder=True)) as bitmap:
                            self.assertEqual((bitmap.width, bitmap.height), (400, 400))
                            self.assertGreater(len(set(bytes(bitmap.buffer))), 1)

    def test_corrupt_and_password_protected_have_distinct_errors(self):
        for data, expected in [(b'%PDF-1.4\nbroken', raw.FPDF_ERR_FORMAT),
                               (make_pdf(password='secret'), raw.FPDF_ERR_PASSWORD)]:
            with self.subTest(expected=expected):
                with self.assertRaises(pdfium.PdfiumError) as caught:
                    pdfium.PdfDocument(data)
                self.assertEqual(caught.exception.err_code, expected)
        with pdfium.PdfDocument(make_pdf(password='secret'), password='secret') as document:
            self.assertEqual(len(document), 4)


if __name__ == '__main__':
    unittest.main()
