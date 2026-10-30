"""T25: build in an isolated project; never include runtime files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_archive_includes_modules_excludes_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'src').mkdir()
            (project / 'list').mkdir()
            shutil.copy2(ROOT / 'package.py', project)
            modules = list((ROOT / 'src').glob('*.py'))
            for source in modules + [ROOT / 'src/requirements.txt', ROOT / 'src/start.sh']:
                shutil.copy2(source, project / 'src')
            original = next(p for p in (ROOT / 'list').glob('*材料清单.docx')
                            if not p.name.startswith('~$'))
            shutil.copy2(original, project / 'list')
            phrase = ROOT / 'list/话术库对应材料.xlsx'
            shutil.copy2(phrase, project / 'list')
            (project / 'list/unrelated.xlsx').write_bytes(b'do-not-include')
            (project / 'model-api.env').write_text('EXTRACT_MODEL_API_KEY=fake-secret-excluded', encoding='utf-8')
            for name in ('.env', 'model-api.env', 'secret.key', 'tasks.sqlite3', 'upload.pdf',
                         'task_storage/source.pdf', 'data/old.docx', '__pycache__/main.pyc',
                         'log/2026-10-30.txt'):
                path = project / 'src' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fake-secret-excluded')
            env = {**os.environ, 'TASK_STORAGE_ROOT': str(project / 'storage')}
            result = subprocess.run([sys.executable, '-B', str(project / 'package.py')],
                                    env=env, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            with tarfile.open(project / 'src.tar') as archive:
                names = set(archive.getnames())
                expected = {p.name for p in modules} | {'requirements.txt', 'start.sh', 'list/' + original.name,
                                                       'list/' + phrase.name}
                self.assertEqual(names, expected)
                self.assertEqual(archive.extractfile('list/' + original.name).read(), original.read_bytes())
                self.assertEqual(archive.extractfile('list/' + phrase.name).read(), phrase.read_bytes())
                self.assertEqual(archive.extractfile('requirements.txt').read(), (ROOT / 'src/requirements.txt').read_bytes())
                unpacked = project / 'unpacked'
                archive.extractall(unpacked, filter='data')
            imports = '; '.join('import ' + p.stem for p in modules)
            result = subprocess.run([sys.executable, '-B', '-c', imports], cwd=unpacked,
                                    env=env, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertFalse((project / 'storage').exists(), 'packaging/import must not start worker')
            self.assertFalse((unpacked / 'log').exists(), 'packaging/import must not create runtime logs')
            schema = json.loads((project / 'openapi-hiagent.json').read_text(encoding='utf-8'))
            self.assertEqual(set(schema['paths']), {'/retrieve'})
            response = schema['paths']['/retrieve']['post']['responses']['200']['content']
            self.assertEqual(set(response), {'text/plain'})


if __name__ == '__main__':
    unittest.main()
