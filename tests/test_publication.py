import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import report_publication as queue

spec=importlib.util.spec_from_file_location('publisher',Path(__file__).resolve().parents[1]/'publish-reports.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)

class PublicationTests(unittest.TestCase):
    def test_queue_replaces_latest_and_redacts(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,REPORT_QUEUE_DIR=tmp):
            data={'scanned_at':'now','error_rules':1,'rules':[{'error':'password=private 10.0.0.115','findings':[]}],
                  'DB_PASSWORD':'private','source_root':'/home/user/source'}
            self.assertTrue(queue.queue_report('world',data)['queued'])
            f=Path(tmp)/queue.NAMES['world'];saved=json.loads(f.read_text())
            self.assertNotIn('private',f.read_text());self.assertNotIn('10.0.0.115',f.read_text())
            self.assertFalse(saved['complete']);self.assertEqual(0o644,f.stat().st_mode & 0o777)
            self.assertEqual('private',data['DB_PASSWORD'])
            queue.queue_report('world',dict(scanned_at='later',error_rules=0,capped_rules=0))
            self.assertEqual('later',json.loads(f.read_text())['scanned_at'])
            self.assertEqual(1,len(list(Path(tmp).glob('*latest.json'))))

    def test_queue_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,REPORT_QUEUE_DIR=tmp+'/not-dir'):
            Path(tmp+'/not-dir').write_text('file')
            self.assertFalse(queue.queue_report('source',{'scanned_at':'now'})['queued'])

    def test_isolated_publish_only_reports_and_no_duplicate_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);remote=tmp/'remote.git';seed=tmp/'seed';pending=tmp/'pending'
            def git(*args,cwd=None):
                return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL,text=True).strip()
            git('init','--bare',str(remote));git('init','-b','main',str(seed))
            (seed/'app.py').write_text('original')
            git('add','app.py',cwd=seed)
            git('-c','user.name=Test','-c','user.email=test@example.com','commit','-m','seed',cwd=seed)
            git('remote','add','origin',str(remote),cwd=seed);git('push','origin','main',cwd=seed)
            # Uncommitted deployment change must never be included in publication.
            (seed/'app.py').write_text('local uncommitted')
            pending.mkdir()
            with patch.dict(os.environ,REPORT_QUEUE_DIR=str(pending)):
                queue.queue_report('world',{'scanned_at':'now','error_rules':0})
                queue.queue_report('source',{'scanned_at':'now','complete':False})
            with patch.object(publisher,'URL',str(remote)):
                a=publisher.publish(seed,pending);b=publisher.publish(seed,pending)
            self.assertEqual('published',a['state']);self.assertEqual(a['commit'],b['commit'])
            self.assertEqual('local uncommitted',(seed/'app.py').read_text())
            self.assertEqual('original',git('--git-dir='+str(remote),'show','main:app.py'))
            saved=json.loads(git('--git-dir='+str(remote),'show','main:reports/source-audit-latest.json'))
            self.assertFalse(saved['complete'])
            self.assertTrue((pending/queue.NAMES['source']).exists())

    def test_network_failure_retains_queue(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,REPORT_QUEUE_DIR=tmp):
            queue.queue_report('world',{'scanned_at':'now'})
            with patch.object(publisher,'git',side_effect=RuntimeError('no network')):
                with self.assertRaises(RuntimeError):publisher.publish(Path(tmp),Path(tmp))
            self.assertTrue((Path(tmp)/queue.NAMES['world']).exists())
