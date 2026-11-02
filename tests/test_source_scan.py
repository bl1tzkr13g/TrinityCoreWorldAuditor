import tempfile
import unittest
from pathlib import Path
import source_scan as scan

FIXTURE = '''
// new fake(); CreatureScript("commented")
class jailor : public CreatureScript {
public: jailor() : CreatureScript("npc_darkspear_jailor") {}
struct AI { bool GossipSelect(Player* player) {
if (HasProvingPitQuest(player)) { scout->AI()->DoAction(ACTION_BEGIN_PROVING_PIT);
scout->AI()->AttackStart(player); player->KilledMonsterCredit(39062); } return true; } };
};
class scout : public CreatureScript { public:
scout() : CreatureScript("npc_captive_spitescale_scout") {}
struct AI { void Reset(){me->SetImmuneToPC(true);} void DoAction(int a){me->SetImmuneToPC(false);} };
};
void AddSC_durotar() { new jailor(); new scout(); }
void AddKalimdorScripts() { AddSC_durotar(); }
'''

class SourceTests(unittest.TestCase):
    def index(self, text):
        clean, regs, loaders = scan.parse_source(text, 'zone.cpp')
        scripts = {}
        for r in regs:
            scripts.setdefault(r['name'], []).append(r)
        return dict(scripts=scripts, texts={'zone.cpp':clean}, complete=True,
                    loaders={k:dict(body=v,file='zone.cpp') for k,v in loaders.items()})

    def test_actual_confirmed_fix(self):
        text=Path('tests/proving_pit.cpp.fixture').read_text()
        findings=scan.analyze(self.index(text), [])
        self.assertFalse(any('Proving Pit' in f['rule'] for f in findings), findings)

    def test_working_proving_pit(self):
        rows=[dict(_table='creature_template',entity_id=k,ScriptName=v,AIName='') for k,v in scan.PROVING.items()]
        self.assertEqual([],scan.analyze(self.index(FIXTURE),rows))

    def test_missing_release_detected(self):
        findings=scan.analyze(self.index(FIXTURE.replace('me->SetImmuneToPC(false);','')),[])
        self.assertTrue(any('release immunity' in f['evidence'].get('missing',[]) for f in findings))

    def test_commented_registration_not_counted(self):
        index=self.index('/* '+FIXTURE+' */')
        self.assertEqual({},index['scripts'])

    def test_binding_missing_not_auto_fixed(self):
        findings=scan.analyze(self.index(FIXTURE),[dict(_table='gameobject_template', entity_id=1, ScriptName='unknown')])
        self.assertEqual(1,len(findings))
        self.assertFalse(findings[0]['automatic_repair'])

    def test_declaration_is_not_loader_call(self):
        index=self.index(FIXTURE.replace('void AddKalimdorScripts() { AddSC_durotar(); }','void AddSC_durotar();'))
        findings=scan.analyze(index,[])
        self.assertEqual(1,len(findings))
        self.assertIn('no recognized call',findings[0]['rule'])

    def test_macros_and_string_braces(self):
        i=self.index('struct npc_x { const char* x="}"; }; void AddSC_x(){RegisterCreatureAI(npc_x);}')
        self.assertIn('npc_x',i['scripts'])
        self.assertIn('const char*',i['scripts']['npc_x'][0]['body'])

    def test_damage_clamp_is_review_only(self):
        i=self.index(FIXTURE+' struct boss { void DamageTaken(Unit*, uint32& damage) override {damage = me->GetHealth() - 1;} };')
        f=scan.analyze(i,[])
        self.assertEqual('review',f[0]['severity'])
        self.assertFalse(f[0]['automatic_repair'])

    def test_unmounted_source_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):scan.inventory(Path(tmp))

    def test_inventory_excludes_symlinks_and_reports_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'CMakeLists.txt').write_text('project(test)')
            d=root/'src/server/scripts';d.mkdir(parents=True)
            (d/'zone.cpp').write_text(FIXTURE)
            (d/'linked.cpp').symlink_to(d/'zone.cpp')
            index=scan.inventory(root)
            self.assertEqual(1,index['files']); self.assertTrue(index['complete'])
            old=scan.MAX_FILES
            try:
                scan.MAX_FILES=0
                self.assertFalse(scan.inventory(root)['complete'])
            finally:scan.MAX_FILES=old

if __name__=='__main__': unittest.main()

class DatabaseTests(unittest.TestCase):
    def test_schema_error_marks_incomplete_and_only_reads(self):
        class DB:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def cursor(self): return self
            def execute(self,sql):
                if not sql.startswith(('SELECT ', 'SHOW ')):
                    raise AssertionError('Unexpected write')
                raise ValueError('test schema mismatch')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'CMakeLists.txt').write_text('project(test)')
            d=root/'src/server/scripts';d.mkdir(parents=True)
            (d/'zone.cpp').write_text(FIXTURE)
            report=scan.scan(root,DB)
            self.assertFalse(report['complete'])
            self.assertEqual(5,len(report['errors']))
            self.assertEqual(0,report['bindings_checked'])


class DungeonRegistrationTests(unittest.TestCase):
    def test_real_report_false_positives(self):
        cases = [('boss_baron_silverlaine', 'ShadowfangKeep'), ('boss_houndmaster_loksey', 'ScarletMonastery')]
        for script, dungeon in cases:
            text = (Path(__file__).parent / (script+'.cpp.fixture')).read_text()
            definitions = '#define Register'+dungeon+'CreatureAI(ai_name) RegisterCreatureAIWithFactory(ai_name, Get'+dungeon+'AI)'
            macros = scan.wrapper_macros([definitions])
            _, regs, _ = scan.parse_source(text, script+'.cpp', macros)
            self.assertIn(script, {r['name'] for r in regs})

    def test_unknown_macro_not_guessed(self):
        _, regs, _ = scan.parse_source('void AddSC_test(){RegisterUnknownCreatureAI(npc_x);}', 'test.cpp')
        self.assertFalse(regs)

    def test_wrapper_forwarding_and_explicit_spell_alias(self):
        defs = '#define RegisterDungeonAI(x) RegisterCreatureAIWithFactory(x, Factory)\n#define RegisterNested(x) RegisterDungeonAI(x)'
        macros = scan.wrapper_macros([defs])
        _, regs, _ = scan.parse_source('void AddSC_x(){RegisterNested(npc_test);RegisterSpellScriptWithArgs(spell_test, "spell_alias", 123);}', 'test.cpp',macros)
        self.assertEqual({'npc_test','spell_alias'}, {r['name'] for r in regs})
