#!/usr/bin/env python3
"""Host-only publisher. Git auth is supplied by docker_user's existing setup."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone

NAMES = ('world-audit-latest.json', 'source-audit-latest.json')
URL = 'https://github.com/bl1tzkr13g/TrinityCoreWorldAuditor.git'


def git(*args, cwd=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    r = subprocess.run(['git', *args], cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=120)
    if r.returncode:
        # Do not expose credential helpers, remote URLs or Git's raw diagnostics.
        raise RuntimeError('Git ' + args[0] + ' failed; verify docker_user GitHub write authentication and branch permissions.')
    return r.stdout.strip()


def publish(repo, queue):
    reports = {}
    hashes = {}
    for name in NAMES:
        p = queue / name
        if not p.exists():
            continue
        if p.is_symlink() or p.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Queued report exceeds size limit or is a symlink')
        data = p.read_bytes()
        obj = json.loads(data)
        kind = 'world' if name.startswith('world') else 'source'
        if not isinstance(obj, dict) or obj.get('report_kind') != kind or not obj.get('scanned_at'):
            raise ValueError('Queued report metadata invalid')
        reports[name] = data
        hashes[name] = hashlib.sha256(data).hexdigest()
    if not reports:
        return {'state': 'idle', 'message': 'No reports queued'}
    # Separate checkout: never stash/reset/stage user changes in the deployment.
    # Clone from canonical remote; existing user credential helper/SSH config is
    # outside the container. HTTPS write credentials are required for this URL.
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix='auditor-publish-') as tmp:
            work = Path(tmp) / 'repo'
            git('clone', '--quiet', '--depth=1', '--branch=main', URL, str(work))
            (work / 'reports').mkdir(exist_ok=True)
            for name, data in reports.items():
                dest = work / 'reports' / name
                if dest.is_symlink():
                    raise ValueError('Remote report target is a symlink')
                dest.write_bytes(data)
            git('add', '--', *('reports/' + n for n in reports), cwd=work)
            if not git('diff', '--cached', '--name-only', cwd=work):
                return {'state': 'published', 'hashes': hashes, 'commit': git('rev-parse', 'HEAD', cwd=work)}
            git('-c', 'user.name=World Auditor', '-c', 'user.email=world-auditor@users.noreply.github.com',
                '-c', 'commit.gpgsign=false', 'commit', '-m', 'Update latest world audit reports', cwd=work)
            try:
                git('push', 'origin', 'HEAD:main', cwd=work)
            except RuntimeError:
                if attempt == 2:
                    raise
                continue
            return {'state': 'published', 'hashes': hashes, 'commit': git('rev-parse', 'HEAD', cwd=work)}
    raise RuntimeError('Publication retry limit reached')


def main():
    repo = Path(__file__).resolve().parent
    queue = repo / 'update-requests/reports'
    if not queue.is_dir():
        return
    with (queue / '.publish.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            state = publish(repo, queue)
        except Exception:
            state = {'state': 'failed', 'message': 'Publication failed. Check host GitHub HTTPS write credentials, network access and report validity. Reports retained for retry.'}
        state['checked_at'] = datetime.now(timezone.utc).isoformat()
        temp = queue / '.publication-status.tmp'
        temp.write_text(json.dumps(state, indent=2) + '\n')
        temp.replace(queue / 'publication-status.json')
        print(state['state'] + ': ' + state.get('message', state.get('commit', '')))


if __name__ == '__main__':
    main()
