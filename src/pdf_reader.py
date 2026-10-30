"""Read every PDF page locally; full-page vision/OCR remains the T13 stage."""
import asyncio
import struct
import threading
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium
import pypdfium2.raw as raw

# PDFium must never be called concurrently, even for different documents.
_pdfium_lock = threading.Lock()


class PdfReadError(Exception):
    """Safe processing failure; native messages and local paths are not exposed."""

    def __init__(self, message, *, code, page_number=None):
        super().__init__(message)
        self.code = code
        self.page_number = page_number


@dataclass(frozen=True)
class PdfPage:
    page_number: int
    text: str
    image_png: bytes
    # Rendering does not establish semantic readability, including text overlays.
    readable: bool = False
    needs_vision: bool = True


def _check_cancel(cancel_check):
    if cancel_check():
        raise asyncio.CancelledError()


def _png(bitmap):
    """Encode the explicitly requested RGB bitmap with stdlib, without Pillow."""
    width, height = bitmap.width, bitmap.height
    buffer = bytes(bitmap.buffer)
    rows = b''.join(b'\0' + buffer[y * bitmap.stride:y * bitmap.stride + width * 3]
                    for y in range(height))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data)))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def read_pdf(path: str | Path, cancel_check: Callable[[], bool | None]) -> list[PdfPage]:
    """Return all pages in order, or raise; never publish a partial page list.

    cancel_check is synchronous: true or CancelledError stops processing. This
    function is blocking; async callers must arrange thread execution and a
    thread-visible cancellation signal. All pages retain a 2x full-page RGB PNG
    plus unmodified extracted text. readable stays false until vision confirms
    the whole page. No links, attachments, OCR or model calls are performed.
    """
    _check_cancel(cancel_check)
    # Waiting readers also observe cancellation without touching native handles.
    while not _pdfium_lock.acquire(timeout=0.05):
        _check_cancel(cancel_check)
    page_number = None
    try:
        _check_cancel(cancel_check)
        # Own the raw load: the 5.14.0 convenience constructor rejects zero-page
        # documents before registering a closer, leaking their native handle.
        data = Path(path).read_bytes()
        handle = raw.FPDF_LoadMemDocument64(data, len(data), None)
        if not handle:
            raise pdfium.PdfiumError('PDF load failed', err_code=raw.FPDF_GetLastError())
        with closing(pdfium.PdfDocument(handle)) as document:
            if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
                raise PdfReadError('PDF 已加密，无法完整读取', code='encrypted')
            count = len(document)
            if count == 0:
                raise PdfReadError('PDF 没有页面', code='empty')
            pages = []
            for index in range(count):
                page_number = index + 1
                _check_cancel(cancel_check)
                with closing(document[index]) as page:
                    with closing(page.get_textpage()) as textpage:
                        text = textpage.get_text_bounded(errors='strict')
                    _check_cancel(cancel_check)
                    with closing(page.render(scale=2, force_bitmap_format=raw.FPDFBitmap_BGR,
                                             rev_byteorder=True)) as bitmap:
                        image = _png(bitmap)
                _check_cancel(cancel_check)
                pages.append(PdfPage(page_number, text, image))
            return pages
    except pdfium.PdfiumError as error:
        if error.err_code in (raw.FPDF_ERR_PASSWORD, raw.FPDF_ERR_SECURITY):
            raise PdfReadError('PDF 已加密或安全机制不受支持', code='encrypted') from None
        raise PdfReadError('PDF 页面读取失败' if page_number else 'PDF 无法解析',
                           code='page' if page_number else 'parse',
                           page_number=page_number) from None
    except (OSError, ValueError, OverflowError):
        raise PdfReadError('PDF 页面读取失败' if page_number else 'PDF 无法解析',
                           code='page' if page_number else 'parse',
                           page_number=page_number) from None
    finally:
        _pdfium_lock.release()
