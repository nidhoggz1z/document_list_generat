"""Run inside the image, optionally with --require-clone3-eperm and a legacy seccomp profile."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading

libc = ctypes.CDLL(None, use_errno=True)
clone3_result = libc.syscall(435, 0, 0)  # Linux amd64 clone3; invalid args cannot create a child.
clone3_errno = ctypes.get_errno()
if '--require-clone3-eperm' in sys.argv:
    assert clone3_result == -1 and clone3_errno == errno.EPERM

completed = []
threads = [threading.Thread(target=lambda: completed.append(True)) for _ in range(4)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=10)
assert len(completed) == 4
with ThreadPoolExecutor(max_workers=4) as pool:
    assert list(pool.map(lambda n: n * n, range(8))) == [n * n for n in range(8)]

import anyio
import pypdfium2 as pdfium
from fastapi.testclient import TestClient

async def async_threads():
    assert await asyncio.to_thread(lambda: 42) == 42
    assert await anyio.to_thread.run_sync(lambda: 21) == 21

asyncio.run(async_threads())
assert os.confstr('CS_GNU_LIBC_VERSION') == 'glibc 2.31'
assert sys.version_info[:2] == (3, 12)
document = pdfium.PdfDocument.new()
page = document.new_page(200, 100)
bitmap = page.render(scale=1)
assert bitmap.width == 200 and bitmap.height == 100
bitmap.close()
page.close()
document.close()

with tempfile.TemporaryDirectory() as temporary:
    os.environ['TASK_STORAGE_ROOT'] = str(Path(temporary) / 'tasks')
    sys.path.insert(0, '/home/app')
    from main import create_app
    from docx_reader import read_docx
    with TestClient(create_app(log_dir=Path(temporary) / 'logs')) as client:
        health = client.get('/health')
        assert health.status_code == 200, health.text
        projects = client.get('/projects').json()['projects']
        assert len(projects) == health.json()['document_count'] > 0
        for project in projects:
            response = client.post('/retrieve', json={'param': project['name']})
            assert response.status_code == 200, response.text
            assert response.text == read_docx(Path('/home/app/list') / project['filename'])

print(json.dumps({
    'python': platform.python_version(),
    'glibc': os.confstr('CS_GNU_LIBC_VERSION'),
    'clone3_errno': clone3_errno,
    'seccomp': next(line.strip() for line in Path('/proc/self/status').read_text().splitlines()
                    if line.startswith('Seccomp:')),
    'thread_creation': 'passed',
    'thread_pool': 'passed',
    'asyncio_and_anyio_threads': 'passed',
    'pdfium_native_render': 'passed',
    'health': health.json(),
    'full_text_retrieval_verified': len(projects),
}, indent=2))
