import tempfile
from pathlib import Path
import unittest
from source_repairs import plan,RECIPES
from source_scan import analyze
import test_source_scan as base
FIXTURE = base.FIXTURE

class RepairTests(unittest.TestCase):
    def test_patch_preserves_unrelated_edits_and_detects_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=RECIPES['feign-death-96733-field'];p=root/r['path'];p.parent.mkdir(parents=True)
            old='// local change\nclass '+r['scope']+' { void apply(){'+r['replacements'][0][0]+';} void remove(){'+r['replacements'][1][0]+';} };\n'
            p.write_text(old)
            result=plan('feign-death-96733-field',root)
            self.assertEqual('ready_for_review',result['status'])
            self.assertIn('+class',result['patch']);self.assertEqual(old,p.read_text())
            for a,b in r['replacements']:old=old.replace(a,b)
            p.write_text(old)
            self.assertEqual('already_fixed',plan('feign-death-96733-field',root)['status'])

    def test_ambiguous_source_refuses_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=RECIPES['camel-spellclick-field'];p=root/r['path'];p.parent.mkdir(parents=True)
            p.write_text(r['replacements'][0][0]*2)
            self.assertEqual('conflict',plan('camel-spellclick-field',root)['status'])

    def test_nested_ai_not_duplicated_and_if_handled(self):
        s=FIXTURE+'''class Outer { struct AI {void start(){events.ScheduleEvent(EVENT_X,1s);}
        void update(){while(uint32 eventId=events.ExecuteEvent()){if(eventId == EVENT_X) work();}} }; };'''
        findings=analyze(base.SourceTests().index(s),[])
        self.assertFalse(any('Scheduled event' in f['rule'] for f in findings))
        s=s.replace('if(eventId == EVENT_X) work();','work();')
        findings=analyze(base.SourceTests().index(s),[])
        events=[f for f in findings if 'Scheduled event' in f['rule']]
        self.assertEqual(1,len(events));self.assertEqual('AI',events[0]['evidence']['class_name'])
