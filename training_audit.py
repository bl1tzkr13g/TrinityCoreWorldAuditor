"""Read-only incident contracts for spell-practice objectives.

A missing ordinary combat AI is not a bug. Only a known interaction/credit
contract justifies this rule; other training spells require separate evidence.
"""
from fastapi import APIRouter
router = APIRouter(prefix='/api/training', tags=['training-quests'])
CONNECT = None


def configure(connect):
    global CONNECT
    CONNECT = connect


def assess(quest, npc, rows, credit_exists, overrides=False):
    base={'quest':24766,'npc':47057,'spell':774,'credit':44175,'read_only':True,
          'automatic_apply':False,'repair_file':'repairs/24766-rejuvenation.sql'}
    if not quest or not npc or not credit_exists:
        return dict(base,status='incomplete',detail='Quest, watcher or credit template missing; no repair inferred')
    if any(int(quest.get(k) or 0)!=v for k,v in {'RequiredNpcOrGo1':44175,'RequiredNpcOrGoCount1':1,'RequiredSpell':774}.items()):
        return dict(base,status='review',detail='Quest differs from the verified spell-practice contract')
    if npc.get('ScriptName') or overrides:
        return dict(base,status='review',detail='C++/spawn-specific behavior exists; inspect before changing AI')
    correct=any(all(int(r.get(k) or 0)==v for k,v in {
        'source_type':0,'entryorguid':47057,'event_type':8,'event_param1':774,
        'event_param2':0,'event_phase_mask':0,'event_chance':100,'event_flags':0,
        'action_type':33,'action_param1':44175,'target_type':7}.items()) for r in rows)
    if npc.get('AIName')=='SmartAI' and correct:
        return dict(base,status='handler_present',detail='Expected spell-hit credit handler is configured; test in game and inspect conditions')
    if not npc.get('AIName') and not rows:
        return dict(base,status='missing_handler',detail='No configured watcher AI connects Rejuvenation 774 to practice credit 44175',repair='guarded_sql_available')
    return dict(base,status='review',detail='Existing AI/actions differ from the verified handler; no overwrite proposed')


def inspect(connect=None):
    connection=connect or CONNECT
    try:
        with connection() as db:
            with db.cursor() as cur:
                cur.execute('SELECT ID,RequiredNpcOrGo1,RequiredNpcOrGoCount1,RequiredSpell FROM quest_template WHERE ID=24766')
                quest=cur.fetchone()
                cur.execute('SELECT entry,AIName,ScriptName FROM creature_template WHERE entry=47057')
                npc=cur.fetchone()
                cur.execute('SELECT entry FROM creature_template WHERE entry=44175')
                credit=cur.fetchone() is not None
                cur.execute('SELECT * FROM smart_scripts WHERE entryorguid=47057 AND source_type=0 LIMIT 101')
                rows=cur.fetchall()
                cur.execute("SELECT guid FROM creature WHERE id=47057 AND COALESCE(ScriptName,'')<>'' LIMIT 1")
                overrides=bool(cur.fetchone())
                cur.execute('SELECT s.entryorguid FROM smart_scripts s JOIN creature c ON s.entryorguid=-c.guid WHERE s.source_type=0 AND c.id=47057 LIMIT 1')
                overrides=overrides or bool(cur.fetchone())
                if len(rows)>100:
                    return {'status':'incomplete','quest':24766,'error':'Watcher SmartAI results capped','read_only':True}
                result=assess(quest,npc,rows,credit,overrides)
                result['evidence']={'quest':quest,'npc':npc,'smart_scripts':rows,'credit_template_exists':credit,'spawn_overrides':overrides}
                return result
    except Exception as exc:
        return {'status':'incomplete','quest':24766,'error':str(exc),'read_only':True}


@router.get('/24766')
def endpoint():
    return inspect()


def discover(connect=None):
    """Find practice objectives and trace named targets to direct SmartAI credit."""
    connection = connect or CONNECT
    try:
        with connection() as db, db.cursor() as cur:
            cur.execute("""
                SELECT ID, LogTitle, LogDescription, RequiredSpell,
                       RequiredNpcOrGo1, RequiredNpcOrGo2, RequiredNpcOrGo3, RequiredNpcOrGo4,
                       RequiredNpcOrGoCount1, RequiredNpcOrGoCount2, RequiredNpcOrGoCount3, RequiredNpcOrGoCount4,
                       ObjectiveText1, ObjectiveText2, ObjectiveText3, ObjectiveText4
                FROM quest_template
                WHERE LOWER(ObjectiveText1) LIKE 'practice %'
                   OR LOWER(ObjectiveText2) LIKE 'practice %'
                   OR LOWER(ObjectiveText3) LIKE 'practice %'
                   OR LOWER(ObjectiveText4) LIKE 'practice %'
                ORDER BY ID
            """)
            quests = cur.fetchall()
            results = []
            for quest in quests:
                if quest['ID'] == 24766:
                    continue  # Independently verified contract is reported by inspect().
                cur.execute("""
                    SELECT entry, name, AIName, ScriptName FROM creature_template
                    WHERE CHAR_LENGTH(name) >= 5 AND LOCATE(name, %s) > 0
                      AND (LOCATE(CONCAT('on a ', name), %s) > 0
                        OR LOCATE(CONCAT('on an ', name), %s) > 0
                        OR LOCATE(CONCAT('on the ', name), %s) > 0
                        OR LOCATE(CONCAT('on ', name), %s) > 0)
                    ORDER BY CHAR_LENGTH(name) DESC, entry LIMIT 21
                """, (quest['LogDescription'] or '',) * 5)
                targets = cur.fetchall()
                for slot in range(1, 5):
                    label = quest[f'ObjectiveText{slot}'] or ''
                    if not label.lower().startswith('practice '):
                        continue
                    result = {'quest': quest['ID'], 'name': quest['LogTitle'],
                              'objective_slot': slot, 'objective': label,
                              'learned_spell': quest['RequiredSpell'],
                              'credit': quest[f'RequiredNpcOrGo{slot}'],
                              'count': quest[f'RequiredNpcOrGoCount{slot}'],
                              'automatic_apply': False, 'read_only': True}
                    if len(targets) != 1 or result['credit'] <= 0:
                        results.append(dict(result, status='review',
                                            detail='Practice objective found. Verify its target and credit type.',
                                            candidate_targets=targets[:20]))
                        continue
                    target = targets[0]
                    cur.execute("""
                        SELECT entryorguid, event_type, event_param1, action_type,
                               action_param1, target_type, event_chance, event_phase_mask
                        FROM smart_scripts
                        WHERE source_type=0 AND (
                            entryorguid=%s OR entryorguid IN
                            (SELECT -CAST(guid AS SIGNED) FROM creature WHERE id=%s)
                        )
                        ORDER BY entryorguid, id LIMIT 101
                    """, (target['entry'], target['entry']))
                    rows = cur.fetchall()
                    direct = [row for row in rows if row['event_type'] == 8
                              and row['action_type'] == 33
                              and row['action_param1'] == result['credit']
                              and row['target_type'] == 7]
                    status = 'review'
                    detail = 'No direct spell-hit credit action recognized. Check C++, spell effects, and linked actions.'
                    if len(rows) > 100:
                        status, detail = 'incomplete', 'SmartAI rows exceeded the inspection limit.'
                    elif direct and target['AIName'] == 'SmartAI':
                        status = 'candidate_handler'
                        detail = 'Direct SmartAI credit action found. Verify the practice spell, conditions and gameplay.'
                    results.append(dict(result, status=status, detail=detail, target=target,
                                        smart_actions=rows[:100], direct_credit_actions=direct[:100]))
            return results
    except Exception as exc:
        return [{'status': 'incomplete', 'error': str(exc), 'read_only': True}]


def scan_checks(connect=None):
    return [inspect(connect), *discover(connect)]


@router.get('')
def all_checks():
    return {'checks': scan_checks(), 'read_only': True}
