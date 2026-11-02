"""Fail-closed comparison of an opt-in gateway witness with producer records.

The witness uses one monotonic clock even when service JVMs have different clocks.
This is a measurement of *gateway HTTP response completions*, not of provider
business effects, and includes gateway/proxy overhead by construction.
"""
import json
from pathlib import Path


def verify_witness(source, run_id, records, statuses):
    errors = []
    try:
        if isinstance(source, (str, Path)):
            report = json.loads(Path(source).read_text(encoding='utf-8'))
        else:
            report = source
        if not isinstance(report, dict):
            raise ValueError('Expected witness JSON object')
        if (type(report.get('schema_version')) is not int or report['schema_version'] != 1 or
                report.get('run_id') != run_id or
                report.get('scope') != 'single_loopback_gateway_monotonic_response_clock'):
            raise ValueError('Witness schema, scope or run identity mismatch')
        fields = ('started', 'finished', 'inflight', 'peak_inflight', 'first_forward_ns',
                  'last_reply_ns', 'transport_errors', 'local_rejections')
        if any(type(report.get(k)) is not int or report[k] < 0 for k in fields):
            raise ValueError('Witness contains invalid numeric counters')
        if (report['started'] != report['finished'] or report['inflight'] != 0 or
                report['finished'] != len(records)):
            raise ValueError('Witness counts disagree with complete producer evidence')
        if report['transport_errors'] or report['local_rejections']:
            raise ValueError('Witness recorded transport errors or locally rejected requests')
        if not (0 < report['first_forward_ns'] < report['last_reply_ns'] and
                1 <= report['peak_inflight'] <= report['started']):
            raise ValueError('Witness measured an invalid monotonic interval / concurrency')
        if not isinstance(report.get('statuses'), dict) or any(
                not isinstance(k, str) or not k.isdecimal() or type(v) is not int or v < 0
                for k, v in report['statuses'].items()):
            raise ValueError('Invalid witness HTTP status counters')
        expected = {str(k): int(v) for k, v in statuses.items() if int(v) > 0}
        if {k: v for k, v in report['statuses'].items() if v > 0} != expected:
            raise ValueError('Witness HTTP statuses disagree with producer evidence')
        per = report.get('backends')
        if not isinstance(per, list) or not 1 <= len(per) <= 16:
            raise ValueError('Missing witness backend counters')
        total = 0
        seen = set()
        sums = {}
        for index, item in enumerate(per):
            if not isinstance(item, dict) or item.get('index') != index or type(item.get('index')) is not int:
                raise ValueError('Malformed witness backend index')
            url = item.get('url')
            if not isinstance(url, str) or not url.startswith('http://') or url in seen:
                raise ValueError('Malformed or duplicate backend address')
            seen.add(url)
            n = item.get('finished')
            if type(n) is not int or n < 0 or not isinstance(item.get('statuses'), dict):
                raise ValueError('Malformed backend counts')
            if any(not isinstance(k, str) or not k.isdecimal() or type(v) is not int or v < 0
                   for k, v in item['statuses'].items()):
                raise ValueError('Malformed backend status counter')
            if sum(item['statuses'].values()) != n:
                raise ValueError('Backend total does not equal status counts')
            total += n
            for k, value in item['statuses'].items():
                sums[k] = sums.get(k, 0) + value
        if total != report['finished'] or {k:v for k,v in sums.items() if v > 0} != expected:
            raise ValueError('Witness backend totals disagree with overall statuses')
        duration = (report['last_reply_ns'] - report['first_forward_ns']) / 1e9
        if not 0 < duration < 86400 * 30:
            raise ValueError('Witness duration outside laboratory limits')
        return {'scope': 'one_gateway_clock_across_backends',
                'gateway_completed_post_qps': round(report['finished'] / duration, 3),
                'gateway_window_seconds': round(duration, 6),
                'gateway_peak_inflight': report['peak_inflight'],
                'total_http_responses': report['finished'],
                'backend_count': len(per),
                'backends': [{'index': x['index'], 'finished': x['finished']} for x in per],
                'measurement_note': 'All observed HTTP response completions measured by ONE gateway monotonic clock. Includes proxy overhead; not provider-effect throughput or service-only saturation. Witness is self-reported, unauthenticated laboratory evidence.'}, []
    except (OSError, TypeError, ValueError, KeyError, OverflowError, json.JSONDecodeError) as exc:
        errors.append(f'Untrusted witness evidence rejected: {exc}')
    return None, errors
