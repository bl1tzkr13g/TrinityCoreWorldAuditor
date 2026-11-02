"""Atomic report handoff; no Git credentials or network access in the app."""
import json
import os
import re
import tempfile
from pathlib import Path
from fastapi import APIRouter

VERSION = '0.02.022'
router = APIRouter(prefix='/api/reports', tags=['report-publication'])
NAMES = {'world': 'world-audit-latest.json', 'source': 'source-audit-latest.json'}


def directory():
    return Path(os.getenv('REPORT_QUEUE_DIR', '/update-requests/reports'))


def public_report(value):
    """Remove connection diagnostics/settings; preserve gameplay evidence."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if re.search(r'password|passwd|token|secret|credential|connection|source_root|db_host|db_user', key, re.I):
                continue
            if key in ('error', 'errors'):
                out[key] = ('Diagnostic details omitted from public report' if item else item)
            else:
                out[key] = public_report(item)
        return out
    if isinstance(value, list):
        return [public_report(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?i)(password|passwd|token|secret)\s*[=:]\s*[^\s;,]+', r'\1=[REDACTED]', value)
        value = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP omitted]', value)
        value = re.sub(r'/home/[^\s\'";,]+', '[local path omitted]', value)
    return value


def queue_report(kind, report):
    """Publication failures must not turn a successful scan into HTTP 500."""
    path = None
    try:
        target = directory()
        target.mkdir(parents=True, exist_ok=True)
        result = public_report(report)
        result['auditor_version'] = VERSION
        result['report_kind'] = kind
        result['publication_notice'] = 'Public GitHub report; connection diagnostics omitted.'
        if kind == 'world':
            result['complete'] = not bool(report.get('error_rules') or report.get('capped_rules'))
        data = json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n'
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target, prefix='.pending-', delete=False) as handle:
            path = handle.name
            handle.write(data)
        os.chmod(path, 0o644)  # Host publisher runs as docker_user, container may be root.
        os.replace(path, target / NAMES[kind])
        return {'queued': True, 'path': 'reports/' + NAMES[kind], 'message': 'Queued for host publication; not yet confirmed on GitHub.'}
    except (OSError, ValueError, KeyError):
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
        return {'queued': False, 'message': 'Unable to queue report; check the update-requests mount and permissions.'}


@router.get('/status')
def status():
    try:
        return json.loads((directory() / 'publication-status.json').read_text())
    except (OSError, ValueError):
        return {'state': 'not_confirmed', 'message': 'No host publication status yet. Check the host cron job.'}
