"""Offline download transport; model HTTP clients remain unaffected."""
from unittest.mock import patch
import httpx


def pdf_download(content):
    def handle(request):
        return httpx.Response(200, content=content() if callable(content) else content)
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)
    return patch('task_files.PDFHTTPClient', side_effect=factory)
