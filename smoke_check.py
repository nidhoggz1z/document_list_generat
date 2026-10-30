"""Verify the actual deployment archive over HTTP; always stop the test server."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'src'))
from docx_reader import read_docx

source_files = sorted((root / 'list').rglob('*材料清单.docx'))
source_files = [p for p in source_files if not p.name.startswith('~$')]
expected_count = len(source_files)
assert expected_count > 0


def request(url, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    # Local verification must not pass through the machine's corporate proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)


def expect_error(base, name, status):
    try:
        with request(base + '/retrieve', {'param': name}):
            raise AssertionError('Expected HTTP error for project: ' + name)
    except urllib.error.HTTPError as error:
        assert error.code == status, (name, error.code)


def expect_text(base, name, text):
    with request(base + '/retrieve', {'param': name}) as response:
        assert response.headers.get_content_type() == 'text/plain'
        assert response.read().decode('utf-8') == text


with tempfile.TemporaryDirectory(prefix='file-retrieval-smoke-') as temp:
    with tarfile.open(root / 'src.tar') as archive:
        members = archive.getmembers()
        assert len([m for m in members if m.name.endswith('.docx')]) == expected_count
        assert 'main.py' in archive.getnames()
        assert not any('__pycache__' in m.name or m.name.startswith(('src/', 'data/')) for m in members)
        archive.extractall(temp, filter='data')
    for source in source_files:
        packed = Path(temp) / source.relative_to(root)
        assert packed.read_bytes() == source.read_bytes(), source.name
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, '-B', '-m', 'uvicorn', 'main:app', '--app-dir', temp,
         '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, 'DOCUMENT_DIR': str(Path(temp) / 'list'),
             'TASK_STORAGE_ROOT': str(Path(temp) / 'task_storage')},
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    try:
        base = 'http://127.0.0.1:' + str(port)
        deadline = time.monotonic() + 15
        while True:
            try:
                with request(base + '/health') as response:
                    assert json.load(response)['document_count'] == expected_count
                break
            except (OSError, urllib.error.URLError):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Test server failed to start')
                time.sleep(0.1)
        with request(base + '/projects') as response:
            projects = json.load(response)['projects']
        assert len(projects) == expected_count
        verified_contents = 0
        sources = {p.stem.removesuffix('材料清单').removesuffix('_'): p for p in source_files}
        examples = []
        for item in projects:
            with request(base + '/retrieve', {'param': item['name']}) as response:
                assert response.headers.get_content_type() == 'text/plain'
                assert response.headers.get_content_charset() == 'utf-8'
                assert 'Content-Disposition' not in response.headers
                content = response.read().decode('utf-8')
            assert content == read_docx(sources[item['name']]), item['name']
            verified_contents += 1
            if item['name'] in ('流动资金贷款', '流动资金循环贷款', '外汇流动资金贷款', '外汇流动资金循环贷款', '个人委托贷款业务'):
                examples.append({'param': item['name'], 'content_characters': len(content)})
        assert verified_contents == expected_count
        for name in ('流贷', '流动资金', '循环贷款', '流动资金贷款材料清单', '流动资金贷款.docx', '流贷类产品'):
            expect_error(base, name, 404)
        # Mutate only the extracted temporary list while the same server stays running.
        live_name = '动态更新验证' + str(port)
        live_path = Path(temp) / 'list' / (live_name + '材料清单.docx')
        shutil.copyfile(source_files[0], live_path)
        expect_text(base, live_name, read_docx(source_files[0]))
        with request(base + '/health') as response:
            assert json.load(response)['document_count'] == expected_count + 1
        replacement = source_files[-1]
        shutil.copyfile(replacement, live_path)
        expect_text(base, live_name, read_docx(replacement))
        duplicate = live_path.with_name(live_name + '_材料清单.docx')
        shutil.copyfile(replacement, duplicate)
        expect_error(base, live_name, 409)
        duplicate.unlink()
        expect_text(base, live_name, read_docx(replacement))
        renamed_name = live_name + '改名'
        renamed_path = live_path.with_name(renamed_name + '材料清单.docx')
        live_path.rename(renamed_path)
        expect_error(base, live_name, 404)
        expect_text(base, renamed_name, read_docx(replacement))
        renamed_path.write_bytes(b'incomplete upload')
        expect_error(base, renamed_name, 503)
        shutil.copyfile(replacement, renamed_path)
        expect_text(base, renamed_name, read_docx(replacement))
        renamed_path.unlink()
        expect_error(base, renamed_name, 404)
        with request(base + '/health') as response:
            assert json.load(response)['document_count'] == expected_count
        schema = json.loads((root / 'openapi-hiagent.json').read_text(encoding='utf-8'))
        assert schema['openapi'] == '3.0.3'
        assert list(schema['paths']) == ['/retrieve']
        assert schema['paths']['/retrieve']['post']['operationId'] == 'retrieveDocuments'
        assert '项目名称' in schema['components']['schemas']['RetrievalInput']['properties']['param']['description']
        success = schema['paths']['/retrieve']['post']['responses']['200']['content']
        assert list(success) == ['text/plain']
        assert success['text/plain']['schema']['type'] == 'string'
        print(json.dumps({'http_project_requests': len(projects), 'verified_text_contents': verified_contents,
                          'archive_docx_count': expected_count,
                          'live_updates': ['add', 'replace', 'duplicate_and_recover', 'rename', 'corrupt_and_recover', 'delete'],
                          'examples': examples}, ensure_ascii=False, indent=2))
    finally:
        process.terminate()
        try:
            output = process.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            output = process.communicate()[0]
        if output:
            print(output.decode('utf-8', errors='replace'))
