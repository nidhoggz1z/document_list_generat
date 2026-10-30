import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
settings = importlib.import_module('task_settings') if importlib.util.find_spec('task_settings') else None


def valid_env():
    result = {}
    for stage in ('EXTRACT', 'GENERATE'):
        result.update({f'{stage}_MODEL_API_URL': f'https://{stage.lower()}.example.test/custom/chat',
                       f'{stage}_MODEL_API_KEY': f'test-only-{stage.lower()}-credential',
                       f'{stage}_MODEL_NAME': f'{stage.lower()}-model'})
    return result


class TaskSettingsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(settings, 'T02 task_settings implementation is missing')

    def test_keys_redacted_and_two_endpoints(self):
        env = valid_env()
        config = settings.TaskSettings.from_env(env)
        extract, generate = config.require_models()
        for obj in (config, extract, generate):
            display = repr(obj) + str(obj) + json.dumps(obj.to_dict())
            for stage in ('EXTRACT', 'GENERATE'):
                self.assertNotIn(env[f'{stage}_MODEL_API_KEY'], display)
        self.assertNotIn('api_key', extract.to_dict())
        self.assertEqual(extract.api_key.get_secret_value(), env['EXTRACT_MODEL_API_KEY'])
        self.assertEqual(extract.api_url, env['EXTRACT_MODEL_API_URL'])
        self.assertEqual(generate.api_url, env['GENERATE_MODEL_API_URL'])
        env['GENERATE_MODEL_API_URL'] = env['EXTRACT_MODEL_API_URL']
        shared = settings.TaskSettings.from_env(env).require_models()
        self.assertEqual(shared[0].api_url, shared[1].api_url)

    def test_confirmed_defaults_no_token_budget_or_directory_creation(self):
        config = settings.TaskSettings.from_env({})
        self.assertEqual(config.storage_root, ROOT / 'src/task_storage')
        self.assertEqual(config.request_timeout_seconds, 180)
        self.assertEqual(config.max_retries, 2)
        self.assertFalse(any('token' in key for key in config.to_dict()))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'not-created'
            custom = settings.TaskSettings.from_env({
                'TASK_STORAGE_ROOT': str(target), 'MODEL_REQUEST_TIMEOUT_SECONDS': '12.5',
                'MODEL_MAX_RETRIES': '0'})
            self.assertEqual(custom.storage_root, target)
            self.assertEqual(custom.request_timeout_seconds, 12.5)
            self.assertEqual(custom.max_retries, 0)
            self.assertFalse(target.exists())

    def test_missing_model_config_defers_service_unavailable(self):
        env = valid_env()
        del env['GENERATE_MODEL_API_KEY']
        config = settings.TaskSettings.from_env(env)
        self.assertEqual(config.max_retries, 2)
        with self.assertRaises(settings.TaskConfigurationError) as caught:
            config.require_models()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('GENERATE_MODEL', str(caught.exception))
        self.assertNotIn(env['EXTRACT_MODEL_API_KEY'], str(caught.exception))

    def test_each_of_six_model_variables_is_required_only_for_generation(self):
        for name in valid_env():
            env = valid_env()
            del env[name]
            with self.subTest(name=name):
                config = settings.TaskSettings.from_env(env)
                self.assertIn('storage_root', config.to_dict())
                with self.assertRaises(settings.TaskConfigurationError):
                    config.require_models()

    def test_invalid_model_values_block_use_without_leaking_credentials(self):
        for stage in ('EXTRACT', 'GENERATE'):
            for suffix, value in (
                ('API_KEY', ''), ('API_KEY', 'replace-with-extract-api-key'),
                ('API_KEY', 'replace-with-generate-api-key'), ('API_KEY', 'YOUR_API_KEY'),
                ('API_KEY', ' key\nheader'), ('NAME', 'replace-with-extract-model-name'),
                ('NAME', ''), ('API_URL', 'not-a-url'),
                ('API_URL', 'https://model-api.example.com/v1/chat/completions'),
                ('API_URL', 'https://user:secret-in-url@example.test/chat'),
            ):
                env = valid_env()
                env[f'{stage}_MODEL_{suffix}'] = value
                with self.subTest(stage=stage, suffix=suffix, value=value):
                    config = settings.TaskSettings.from_env(env)
                    with self.assertRaises(settings.TaskConfigurationError) as caught:
                        config.require_models()
                    display = repr(config) + str(caught.exception) + ''.join(traceback.format_exception(caught.exception))
                    self.assertNotIn('secret-in-url', display)
                    for endpoint in ('EXTRACT', 'GENERATE'):
                        key = env[f'{endpoint}_MODEL_API_KEY']
                        if key:
                            self.assertNotIn(key, display)

    def test_invalid_retry_and_timeout_budgets_are_safe_errors(self):
        for name, values in (
            ('MODEL_MAX_RETRIES', ('-1', '1.5', 'inf', 'nan', '', 'untrusted-value')),
            ('MODEL_REQUEST_TIMEOUT_SECONDS', ('0', '-1', 'inf', 'nan', '', 'untrusted-value')),
            ('TASK_STORAGE_ROOT', ('', '\x00')),
        ):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(settings.TaskConfigurationError) as caught:
                    settings.TaskSettings.from_env({**valid_env(), name: value})
                self.assertEqual(caught.exception.status_code, 503)
                self.assertNotIn('untrusted-value', str(caught.exception))
        for retries in ('0', '2', '10'):
            self.assertEqual(settings.TaskSettings.from_env({'MODEL_MAX_RETRIES': retries}).max_retries, int(retries))

    def test_process_environment_and_import_do_not_require_keys(self):
        with patch.dict(os.environ, valid_env(), clear=True):
            config = settings.TaskSettings.from_env()
            self.assertEqual(config.require_models()[0].model_name, 'extract-model')
        with patch.dict(os.environ, {}, clear=True):
            importlib.reload(settings)
            self.assertIsNotNone(settings.TaskSettings.from_env({}))


class ModelConfigFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'model-api.env'
        self.addCleanup(patch.stopall)
        patch.object(settings, 'DEFAULT_CONFIG_FILE', self.path, create=True).start()
        patch.dict(os.environ, {}, clear=True).start()

    def write_config(self, **overrides):
        values = {**valid_env(), **overrides}
        self.path.write_text('# Model configuration\n' + ''.join(
            f'{name}={value}\n' for name, value in values.items()), encoding='utf-8')

    def test_loads_bom_comments_quotes_and_preserves_process_environment(self):
        self.write_config(MODEL_REQUEST_TIMEOUT_SECONDS='25', MODEL_MAX_RETRIES='0')
        content = self.path.read_text(encoding='utf-8').replace(
            'EXTRACT_MODEL_NAME=extract-model', '  EXTRACT_MODEL_NAME = "extract-model"  ').replace(
                'GENERATE_MODEL_NAME=generate-model', "GENERATE_MODEL_NAME='generate-model'")
        self.path.write_text(content + "\n# 中文说明\n\n", encoding='utf-8-sig')
        config = settings.TaskSettings.from_env()
        self.assertIsNotNone(config.extract, 'model-api.env must be loaded')
        self.assertEqual(config.require_models()[0].model_name, 'extract-model')
        self.assertEqual(config.generate.model_name, 'generate-model')
        self.assertEqual(config.generate.api_key.get_secret_value(), valid_env()['GENERATE_MODEL_API_KEY'])
        self.assertEqual(config.request_timeout_seconds, 25)
        self.assertEqual(config.max_retries, 0)
        self.assertEqual(dict(os.environ), {}, 'file loading must not modify process variables')
        self.assertNotIn(valid_env()['GENERATE_MODEL_API_KEY'], repr(config))

    def test_environment_wins_including_explicitly_empty_values(self):
        self.write_config()
        os.environ['GENERATE_MODEL_NAME'] = 'environment-model'
        config = settings.TaskSettings.from_env()
        self.assertIsNotNone(config.extract, 'file must supply variables missing from environment')
        self.assertEqual(config.require_models()[1].model_name, 'environment-model')
        os.environ['EXTRACT_MODEL_API_KEY'] = ''
        with self.assertRaises(settings.TaskConfigurationError):
            settings.TaskSettings.from_env().require_models()

    def test_each_load_rereads_file_without_changing_existing_settings(self):
        self.write_config()
        previous = settings.TaskSettings.from_env()
        self.write_config(GENERATE_MODEL_NAME='replacement-model')
        current = settings.TaskSettings.from_env()
        self.assertIsNotNone(previous.generate, 'model-api.env must be loaded')
        self.assertEqual(previous.require_models()[1].model_name, 'generate-model')
        self.assertEqual(current.require_models()[1].model_name, 'replacement-model')

    def test_missing_file_and_explicit_mapping_keep_existing_behavior(self):
        self.assertIsNone(settings.TaskSettings.from_env().extract)
        self.write_config()
        self.assertIsNone(settings.TaskSettings.from_env({}).extract)
        self.assertEqual(settings.TaskSettings.from_env(valid_env()).require_models()[0].model_name,
                         'extract-model')

    def test_bad_file_errors_are_safe_and_have_no_secret_in_traceback(self):
        secret = 'fixture-secret-must-not-leak'
        for content in (secret, 'UNKNOWN_SETTING=' + secret,
                        'EXTRACT_MODEL_API_KEY="' + secret,
                        'EXTRACT_MODEL_API_KEY=' + secret + '\nEXTRACT_MODEL_API_KEY=duplicate'):
            self.path.write_text(content, encoding='utf-8')
            with self.subTest(content=content):
                with self.assertRaises(settings.TaskConfigurationError) as caught:
                    settings.TaskSettings.from_env()
                self.assertNotIn(secret, ''.join(traceback.format_exception(caught.exception)))
                self.assertIn('model-api.env', str(caught.exception))
        self.path.write_bytes(b'\xff\xfeinvalid-utf8')
        with self.assertRaises(settings.TaskConfigurationError):
            settings.TaskSettings.from_env()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(settings.TaskConfigurationError):
            settings.TaskSettings.from_env()

    def test_development_and_unpacked_paths_ignore_working_directory(self):
        module = ROOT / 'src/task_settings.py'
        for development in (True, False):
            with self.subTest(development=development), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                # An unpacked deployment may itself be named src.
                module_dir = project / 'src'
                module_dir.mkdir()
                shutil.copy2(module, module_dir / module.name)
                config_dir = project if development else module_dir
                if development:
                    (project / 'package.py').write_text('# source project marker', encoding='utf-8')
                (config_dir / 'model-api.env').write_text(''.join(
                    f'{key}={value}\n' for key, value in valid_env().items()), encoding='utf-8')
                result = subprocess.run([sys.executable, '-B', '-c',
                    'import task_settings; '
                    'config = task_settings.TaskSettings.from_env(); '
                    'assert config.extract is not None, "config file not loaded"; '
                    'assert config.require_models()[0].model_name == "extract-model"'],
                    cwd=project.parent, env={'PYTHONPATH': str(module_dir)},
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
