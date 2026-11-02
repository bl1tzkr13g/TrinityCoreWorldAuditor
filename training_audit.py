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
