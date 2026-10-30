"""Run on Linux/WSL: python3 -B -m unittest discover -s tests -p test_docker_deployment.py -v."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Requires Linux Bash')
class DockerDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='offline deploy ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('huanban_tar.sh', 'release.sh', 'start.sh', 'stop.sh',
                     'show.sh', 'restore.sh', 'rollback.sh', 'DEPLOYMENT.md'):
            self.assertTrue((source / name).is_file(), f'Missing deployment file: {name}')
            shutil.copyfile(source / name, self.root / name)
        (self.root / 'list').mkdir()
        (self.root / 'list' / '测试材料清单.docx').write_bytes(b'checklist')
        (self.root / 'list' / '话术库对应材料.xlsx').write_bytes(b'phrases')
        (self.root / 'docs' / 'sdd').mkdir(parents=True)
        (self.root / 'docs' / 'sdd' / 'model-api.env.example').write_text('TEMPLATE_ONLY=1\n')
        (self.root / 'model-api.env').write_text('SECRET_MUST_NOT_SHIP=private\n')
        (self.root / 'file-retrieval-2.2.tar.gz').write_bytes(b'fake image')
        binary = self.root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$DOCKER_TEST_LOG"
if [ "$1" = save ]; then
    printf 'fake image contents'
    [ "${FAIL_SAVE:-0}" != 1 ] || exit 7
fi
''')
        docker.chmod(0o755)
        self.log = self.root / 'docker-calls.txt'
        self.deploy = self.root / 'target'
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                        DOCKER_TEST_LOG=str(self.log), DEPLOY_DIR=str(self.deploy),
                        APP_NAME='file-retrieval', VERSION='2.2', API_PORT='18008')

    def run_script(self, name):
        return subprocess.run(['bash', str(self.root / name)], cwd='/', env=self.env,
                              text=True, capture_output=True)

    def test_start_missing_config_keeps_existing_container(self):
        (self.deploy / 'list').mkdir(parents=True)
        result = self.run_script('start.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('rm -f', self.log.read_text() if self.log.exists() else '')

    def test_release_preserves_config_checklists_and_runtime_data(self):
        (self.deploy / 'list').mkdir(parents=True)
        (self.deploy / 'list' / '现场清单.docx').write_bytes(b'onsite')
        (self.deploy / 'model-api.env').write_text('KEEP_EXISTING_CONFIG=1\n')
        for directory in ('task_storage', 'log'):
            (self.deploy / directory).mkdir()
            (self.deploy / directory / 'keep').write_bytes(b'persist')
        result = self.run_script('release.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.deploy / 'model-api.env').read_text(), 'KEEP_EXISTING_CONFIG=1\n')
        self.assertFalse((self.deploy / 'list' / '测试材料清单.docx').exists())
        calls = self.log.read_text()
        self.assertIn('-p 18008:8000', calls)
        for target in ('/home/app/list', '/home/app/model-api.env',
                       '/home/app/task_storage', '/home/app/log'):
            self.assertIn('target=' + target, calls)
        for directory in ('task_storage', 'log'):
            self.assertEqual((self.deploy / directory / 'keep').read_bytes(), b'persist')
        result = self.run_script('rollback.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.log.read_text().rstrip().endswith('rm -f file-retrieval'))
        self.assertTrue((self.deploy / 'task_storage' / 'keep').exists())

    def test_package_reassembles_and_contains_no_real_config(self):
        result = self.run_script('huanban_tar.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = self.root / 'dist' / 'file-retrieval-2.2'
        combined = b''.join(p.read_bytes() for p in sorted(output.glob('huanban.tar_*')))
        self.assertEqual(combined, (output / 'huanban.tar').read_bytes())
        with tarfile.open(output / 'huanban.tar') as archive:
            self.assertNotIn('model-api.env', archive.getnames())
            self.assertEqual(archive.extractfile('model-api.env.example').read(), b'TEMPLATE_ONLY=1\n')
            self.assertIn('rollback.sh', archive.getnames())
            self.assertIn('list/话术库对应材料.xlsx', archive.getnames())
        result = subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=output,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_save_failure_does_not_publish_package(self):
        self.env['FAIL_SAVE'] = '1'
        result = self.run_script('huanban_tar.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'dist' / 'file-retrieval-2.2' / 'huanban.tar').exists())


if __name__ == '__main__':
    unittest.main()
