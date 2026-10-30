"""Explicit HTTP acceptance client. Never reads model credentials or changes expected data."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter
from task_files import MAX_PDF_BYTES, TaskSubmissionError, validate_pdf_url
from task_models import CreateTaskRequest, CreateResponse, TaskIdentity, TaskQueryResponse
from material_tree import iter_materials


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ('base-url', 'business-data', 'expected', 'report'):
        result.add_argument('--' + name, required=True)
    result.add_argument('--pdf', help='可选：用于人工验收核对的本地 PDF 原件，不上传')
    result.add_argument('--wait-seconds', type=float, default=600,
                        help='Client wait only; does not cancel server task or define an SLA')
    result.add_argument('--poll-seconds', type=float, default=1)
    return result


def _read(path):
    def reject(value):
        raise ValueError('JSON 不允许非有限数值')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject)


def _expected(value):
    if not isinstance(value, dict) or set(value) != {'products', 'items'}:
        raise ValueError('期望文件必须包含 products 和 items')
    names, items = value['products'], value['items']
    if (not isinstance(names, list) or not names or
            any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names)):
        raise ValueError('期望产品必须是非空且无重复的名称数组')
    if not isinstance(items, list) or not items:
        raise ValueError('期望材料必须是非空数组，每个对象实例独占一条')
    key = 'sourceRuleId' if isinstance(items[0], dict) and 'sourceRuleId' in items[0] else 'materialName'
    for item in items:
        if (not isinstance(item, dict) or not {'productName', key} <= set(item)
                or not set(item) <= {'productName', 'sourceRuleId', 'materialName', 'requirement'}
                or any(not isinstance(v, str) for v in item.values())
                or item['productName'] not in names or not item[key].strip()):
            raise ValueError('期望条目须统一提供 productName/materialName；旧结果可按 sourceRuleId 比较')
    return value


def _compare(expected, actual):
    key = 'sourceRuleId' if 'sourceRuleId' in expected['items'][0] else 'materialName'
    def counts(items):
        return Counter((i['productName'], i[key]) for i in items)
    def rows(counter):
        return [dict(productName=p, **{key: r}, count=n) for (p, r), n in sorted(counter.items())]
    wanted, got = counts(expected['items']), counts(iter_materials(actual['items']))
    missing, extra = rows(wanted - got), rows(got - wanted)
    product_names = [p['productName'] for p in actual['products']]
    missing_products = sorted(set(expected['products']) - set(product_names))
    extra_products = sorted(set(product_names) - set(expected['products']))
    coverage = [dict(productName=p, **{key: r}, expectedCount=wanted[p, r], actualCount=got[p, r])
                for p, r in sorted(wanted.keys() | got.keys())]
    matched = not (missing or extra or missing_products or extra_products) and len(product_names) == len(set(product_names))
    return dict(status='COUNTS_MATCH' if matched else 'COUNTS_DIFFER',
                missingItems=missing, extraItems=extra, missingProducts=missing_products,
                extraProducts=extra_products, coverage=coverage,
                expectedTotal=sum(wanted.values()), actualTotal=sum(got.values()))


def run(args, *, client=None):
    """Return 0 only for automatic counts match; semantic sign-off always stays pending."""
    report_path = Path(args.report).resolve()
    inputs = [Path(p).resolve() for p in (args.pdf, args.business_data, args.expected) if p]
    if report_path in inputs:
        raise ValueError('报告路径不能覆盖输入文件')
    report = dict(status='ERROR', checkedAt=datetime.now(timezone.utc).isoformat(),
                  manualConclusion='待人工核对文字、OCR及适用性', reviewer='', reviewDate='',
                  comparison='产品集合及每个(productName, sourceRuleId)的实例数量；不比较文字')
    code = 1
    try:
        url = urlsplit(args.base_url)
        url.port  # Reject invalid port syntax before any network access.
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError('base-url 必须是无凭证的 HTTP 服务地址')
        for value in (args.wait_seconds, args.poll_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('等待和轮询秒数必须为有限正数')
        business = CreateTaskRequest.model_validate(_read(args.business_data)).model_dump(mode='json', exclude_none=True)
        validate_pdf_url(business['replyImageUrl'])
        expected = _expected(_read(args.expected))
        if 'sourceRuleId' not in expected['items'][0]:
            report['comparison'] = '产品集合及每个(productName, materialName)的实例数量；不比较具体要求文字'
        report.update(expected=expected)
        if args.pdf:
            pdf = Path(args.pdf)
            if not 0 < pdf.stat().st_size <= MAX_PDF_BYTES:
                raise ValueError('PDF 必须非空且不超过 10 MiB')
            raw = pdf.read_bytes()
            if not re.match(rb'%PDF-(?:1\.[0-9]|2\.0)[\r\n\t ]', raw[:9]):
                raise ValueError('输入不是 PDF')
            report['sourceDocumentHash'] = hashlib.sha256(raw).hexdigest()
        if client is None:
            with httpx.Client(trust_env=False, timeout=75, follow_redirects=False) as owned:
                _execute(args, owned, business, report)
        else:
            _execute(args, client, business, report)
        report.update(_compare(expected, report['actual']['data']))
        code = 0 if report['status'] == 'COUNTS_MATCH' else 1
    except httpx.HTTPError:
        report['error'] = '服务不可达、HTTP 请求失败或网络超时；检查服务及任务状态'
    except TaskSubmissionError as error:
        report['error'] = str(error)
    except FileNotFoundError:
        report['error'] = '缺少 PDF、业务 JSON 或期望清单文件；未执行完整验收'
    except (OSError, ValueError, TypeError, KeyError):
        # Do not reflect arbitrary file/provider contents or credentials into errors.
        report['error'] = '输入文件、期望格式、参数或服务响应无效；检查路径、契约和配置'
    except RuntimeError as error:
        report['error'] = str(error)  # Only locally authored messages below.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


def _execute(args, client, business, report):
    base = args.base_url.rstrip('/')
    response = client.get(base + '/health')
    response.raise_for_status()
    health = response.json()
    if not isinstance(health, dict) or health.get('status') != 'healthy':
        raise RuntimeError('服务健康检查未通过；未创建任务')
    identity = TaskIdentity.model_validate({k: business[k] for k in ('bsnEvtId', 'userNo', 'taskId')}).model_dump()
    response = client.post(base + '/api/v1/tasks', json=business)
    response.raise_for_status()
    created = CreateResponse.model_validate(response.json())
    if any(getattr(created, k) != v for k, v in identity.items()):
        raise ValueError('创建标识不匹配')
    report['identity'] = identity
    deadline = time.monotonic() + args.wait_seconds
    while True:
        response = client.post(base + '/api/v1/tasks/query', json=identity)
        response.raise_for_status()
        result = TypeAdapter(TaskQueryResponse).validate_python(response.json()).model_dump(mode='json')
        if any(result.get(k) != v for k, v in identity.items()):
            raise RuntimeError('查询任务不存在或任务标识已被其他请求覆盖')
        status = result['status']
        report['taskStatus'] = status
        if status == 'DONE':
            if ('sourceDocumentHash' in result['data'] and 'sourceDocumentHash' in report
                    and result['data']['sourceDocumentHash'] != report['sourceDocumentHash']):
                raise RuntimeError('查询结果 PDF 哈希不匹配；任务可能被覆盖')
            report['actual'] = result
            return
        if status in ('FAILED', 'TERMINATED'):
            report['taskFailure'] = result
            raise RuntimeError('任务处理失败或已被替换，未完成验收')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('客户端等待超时；服务任务仍可能运行，可继续查询')
        time.sleep(min(args.poll_seconds, remaining))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError):
        print('无法安全写入报告；报告路径不能覆盖输入，请检查目录权限', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
