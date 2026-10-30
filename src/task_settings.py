"""Startup configuration; no file creation, model calls or import-time key loading.

Call TaskSettings.from_env(), then require_models() before starting generation.
Missing credentials do not prevent retrieval or reading persisted task results.
"""
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parent / 'task_storage'
_MODULE_DIR = Path(__file__).resolve().parent
_CONFIG_DIR = (_MODULE_DIR.parent if _MODULE_DIR.name == 'src'
               and (_MODULE_DIR.parent / 'package.py').is_file() else _MODULE_DIR)
DEFAULT_CONFIG_FILE = _CONFIG_DIR / 'model-api.env'
_MODEL_ENV_NAMES = {f'{stage}_MODEL_{suffix}' for stage in ('EXTRACT', 'GENERATE')
                    for suffix in ('API_URL', 'API_KEY', 'NAME')} | {
                        'MODEL_REQUEST_TIMEOUT_SECONDS', 'MODEL_MAX_RETRIES'}


class TaskConfigurationError(Exception):
    status_code = 503


def _read_model_config(path):
    """Read literal KEY=value pairs; never execute code or expand variables."""
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise TaskConfigurationError('model-api.env 无法读取，请检查文件权限及 UTF-8 编码') from None
    values = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or name not in _MODEL_ENV_NAMES or name in values:
            raise TaskConfigurationError(f'model-api.env 第 {number} 行格式无效：配置项未知或重复')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise TaskConfigurationError(f'model-api.env 第 {number} 行引号未闭合')
            value = value[1:-1]
        values[name] = value
    return values


def _placeholder(value):
    lowered = value.lower()
    return (not value or lowered.startswith(('replace-with-', 'your_', 'your-', '<'))
            or lowered in ('changeme', 'change-me'))


@dataclass(frozen=True)
class ModelSettings:
    api_url: str
    model_name: str
    api_key: SecretStr = field(repr=False)

    def to_dict(self):
        """Safe export for diagnostics; credentials are never exported."""
        return {'api_url': self.api_url, 'model_name': self.model_name}


def _model_from_env(env, stage):
    prefix = stage + '_MODEL_'
    url = env.get(prefix + 'API_URL', '').strip()
    name = env.get(prefix + 'NAME', '').strip()
    key = env.get(prefix + 'API_KEY', '')
    # Never include environment values or underlying parser exceptions in errors.
    invalid = _placeholder(url) or _placeholder(name) or _placeholder(key)
    invalid = invalid or any(c.isspace() for c in key) or any(ord(c) < 32 or ord(c) == 127 for c in url + name + key)
    try:
        parsed = urlsplit(url)
        invalid = invalid or (parsed.scheme not in ('http', 'https') or not parsed.hostname
                              or parsed.username is not None or parsed.password is not None
                              or bool(parsed.fragment) or parsed.hostname == 'model-api.example.com')
        parsed.port  # Validate malformed/out-of-range ports without retaining parser text.
    except ValueError:
        invalid = True
    if invalid:
        return None
    return ModelSettings(api_url=url, model_name=name, api_key=SecretStr(key))


@dataclass(frozen=True)
class TaskSettings:
    storage_root: Path
    request_timeout_seconds: float
    max_retries: int
    extract: ModelSettings | None = None
    generate: ModelSettings | None = None

    @classmethod
    def from_env(cls, environ=None):
        """Read model-api.env at startup, overridden by process variables.

        A supplied mapping is used alone, without reading files or os.environ.
        Existing settings are immutable: file edits take effect after restart.

        180 seconds is a per-request network timeout, not a task SLA. max_retries
        counts additional attempts after the initial request. No token budget is set.
        """
        env = {**_read_model_config(DEFAULT_CONFIG_FILE), **os.environ} if environ is None else environ
        storage = env.get('TASK_STORAGE_ROOT', str(DEFAULT_STORAGE_ROOT))
        if not storage.strip() or '\x00' in storage:
            raise TaskConfigurationError('TASK_STORAGE_ROOT 配置无效')
        timeout_raw = env.get('MODEL_REQUEST_TIMEOUT_SECONDS', '180')
        try:
            timeout = float(timeout_raw)
        except (ValueError, TypeError):
            timeout = float('nan')
        if not math.isfinite(timeout) or timeout <= 0:
            raise TaskConfigurationError('MODEL_REQUEST_TIMEOUT_SECONDS 必须为有限正数')
        retry_raw = env.get('MODEL_MAX_RETRIES', '2')
        if not isinstance(retry_raw, str) or not re.fullmatch(r'[0-9]+', retry_raw):
            raise TaskConfigurationError('MODEL_MAX_RETRIES 必须为非负整数')
        try:
            retries = int(retry_raw)
        except ValueError:
            raise TaskConfigurationError('MODEL_MAX_RETRIES 必须为非负整数') from None
        return cls(storage_root=Path(storage).resolve(), request_timeout_seconds=timeout,
                   max_retries=retries, extract=_model_from_env(env, 'EXTRACT'),
                   generate=_model_from_env(env, 'GENERATE'))

    def require_models(self):
        """Return validated stages or raise a safe, explicit 503-path error."""
        missing = [stage for stage, config in (('EXTRACT_MODEL', self.extract),
                                              ('GENERATE_MODEL', self.generate)) if config is None]
        if missing:
            raise TaskConfigurationError('生成服务不可用：' + '、'.join(missing) + ' 配置缺失或无效')
        return self.extract, self.generate

    def to_dict(self):
        return {'storage_root': str(self.storage_root),
                'request_timeout_seconds': self.request_timeout_seconds,
                'max_retries': self.max_retries,
                'extract': self.extract.to_dict() if self.extract else None,
                'generate': self.generate.to_dict() if self.generate else None}
