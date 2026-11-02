"""Read-only lexical source/database checks. Never executes mounted source."""
import hashlib
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix='/api/source', tags=['source-audit'])
CONNECT = None
LOCK = threading.Lock()
MAX_FILES = 12000
MAX_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
CAP = 300


def configure(connect):
    global CONNECT
    CONNECT = connect


def uncomment(text):
    # Keep literals and line breaks; commented registrations must not count.
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/')
    return token.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith(('//', '/*')) else m[0], text)


def body(text, start):
    """Balanced braces, ignoring braces inside string/character literals."""
    depth = 0
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', text[start:])
    for m in tokens:
        if m[0] == '{':
            depth += 1
        elif m[0] == '}':
            depth -= 1
            if depth == 0:
                return text[start:start + m.end()]
    return ''


def parse_source(text, filename):
    clean = uncomment(text)
    registrations = []
    classes = {}
    constructors = {}
    loaders = {}
    for m in re.finditer(r'\b(?:class|struct)\s+(\w+)[^;{}]*\{', clean):
        classes[m[1]] = body(clean, m.end() - 1)
    for m in re.finditer(r'\b(?:CreatureScript|GameObjectScript|SpellScriptLoader|AreaTriggerScript|InstanceMapScript)\s*\(\s*"([^"]+)"', clean):
        owner = next((name for name, block in classes.items() if m[0] in block), None)
        if owner:
            constructors[owner] = m[1]
    for m in re.finditer(r'\bvoid\s+(Add\w+)\s*\([^)]*\)\s*\{', clean):
        loaders[m[1]] = body(clean, m.end() - 1)
    for loader, block in loaders.items():
        for m in re.finditer(r'\bnew\s+(\w+)\s*\(|\bRegister(?:CreatureAI|GameObjectAI|SpellScript|AuraScript)\s*\(\s*(\w+)\s*\)', block):
            cls = m[1] or m[2]
            name = constructors.get(cls, cls if m[2] else None)
            if name:
                registrations.append({'name': name, 'class': cls, 'loader': loader,
                                      'file': filename, 'body': classes.get(cls, '')})
    return clean, registrations, loaders


def inventory(root):
    base = root / 'src/server/scripts'
    if not (root / 'CMakeLists.txt').is_file() or not base.is_dir():
        raise ValueError('TrinityCore source unavailable: check the read-only /trinitycore-source mount.')
    scripts = {}
    loaders = {}
    texts = {}
    errors = []
    digest = hashlib.sha256()
    size = 0
    count = 0
    # Walk only script source, never build/, .git/, configs or arbitrary symlink targets.
    for directory, dirs, names in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            p = Path(directory) / name
            if p.suffix not in ('.cpp', '.h') or p.is_symlink():
                continue
            rel = p.relative_to(root).as_posix()
            try:
                if count >= MAX_FILES or p.stat().st_size > MAX_FILE_BYTES:
                    errors.append(rel + ': file limit exceeded')
                    continue
                raw = p.read_bytes()
                if len(raw) > MAX_FILE_BYTES or size + len(raw) > MAX_BYTES:
                    errors.append(rel + ': byte limit exceeded')
                    continue
                text = raw.decode('utf-8')
                size += len(raw)
                count += 1
                digest.update(rel.encode() + b'\0' + raw)
                clean, regs, defs = parse_source(text, rel)
                texts[rel] = clean
                loaders.update({key: {'file': rel, 'body': val} for key, val in defs.items()})
                for reg in regs:
                    scripts.setdefault(reg['name'], []).append(reg)
            except (OSError, UnicodeError) as exc:
                errors.append(rel + ': ' + type(exc).__name__)
    return {'scripts': scripts, 'loaders': loaders, 'texts': texts, 'files': count,
            'bytes': size, 'digest': digest.hexdigest(), 'errors': errors,
            'complete': not errors and count > 0}


# Incident contracts are deliberately narrow. Do not generalize immunity or 1 HP
# handling to all NPCs: encounter phases, vehicles and summons legitimately use it.
PROVING = {
    39062: 'npc_darkspear_jailor',
    38142: 'npc_captive_spitescale_scout',
}


def analyze(index, bindings):
    findings = []
    def add(rule, severity, detail, **evidence):
        findings.append({'rule': rule, 'severity': severity, 'detail': detail,
                         'evidence': evidence, 'repair': 'manual_review',
                         'automatic_repair': False})
    scripts = index['scripts']
    for row in bindings:
        name = row.get('ScriptName') or ''
        if name and name not in scripts:
            add('Database script not found in recognized registrations', 'review',
                'No recognized registration for this binding. Custom macros, conditional compilation and external modules require review; do not clear ScriptName automatically.',
                table=row['_table'], id=row['entity_id'], script=name,
                source_complete=index['complete'])
        if row['_table'] != 'creature_template':
            continue
        entry = int(row['entity_id'])
        if entry in PROVING:
            expected = PROVING[entry]
            if name != expected:
                add('Proving Pit script binding differs from working fix', 'review',
                    'Compare with the confirmed Proving Pit implementation before changing this NPC; another implementation may be intentional.',
                    id=entry, actual=name, expected=expected, ai=row.get('AIName'))
            if row.get('AIName'):
                add('Proving Pit AI selection needs review', 'review',
                    'A nonempty AIName may select a different AI. Verify against the local core AI selection logic.',
                    id=entry, ai=row['AIName'], script=name)
    requirements = {
        'npc_darkspear_jailor': [
            ('quest eligibility', r'HasProvingPitQuest\s*\(\s*player\s*\)'),
            ('gossip handler', r'GossipSelect\s*\('),
            ('activate scout', r'DoAction\s*\(\s*ACTION_BEGIN_PROVING_PIT\s*\)'),
            ('begin combat', r'AttackStart\s*\(\s*player\s*\)'),
            ('interaction credit', r'KilledMonsterCredit\s*\(\s*(?:NPC_DARKSPEAR_JAILOR|39062)\s*\)'),
        ],
        'npc_captive_spitescale_scout': [
            ('initial/reset immunity', r'SetImmuneToPC\s*\(\s*true\s*\)'),
            ('release immunity', r'SetImmuneToPC\s*\(\s*false\s*\)'),
            ('activation handler', r'DoAction\s*\('),
        ],
    }
    for name, checks in requirements.items():
        regs = scripts.get(name, [])
        if not regs:
            add('Proving Pit handler not recognized', 'review',
                'The known working encounter handler was not recognized. Investigate source or an alternative SmartAI implementation; requires C++ review if absent.',
                script=name, reference_commit='24cf65fb40c9985d44e1a93de8b362056db11313')
        else:
            for reg in regs:
                missing = [label for label, pattern in checks if not re.search(pattern, reg['body'])]
                if missing:
                    add('Proving Pit behavior differs from regression contract', 'review',
                        'Expected operations not recognized in this class. Refactoring into helpers may explain this; lexical checks do not prove gameplay failure.',
                        script=name, file=reg['file'], missing=missing)
    # Registration inside an AddSC function is evidence, not proof of loader reachability.
    all_text = '\n'.join(item['body'] for item in index['loaders'].values())
    loader_calls = set(re.findall(r'\b(AddSC_\w+)\s*\(\s*\)\s*;', all_text))
    checked_loaders = set()
    for name, regs in scripts.items():
        for reg in regs:
            loader = reg['loader']
            if loader in checked_loaders:
                continue
            checked_loaders.add(loader)
            if loader not in loader_calls:
                add('Script loader has no recognized call', 'review',
                    'Loader function exists but no direct call was found. Generated loaders and CMake module settings must be checked.',
                    script=name, file=reg['file'], loader=loader)
    for filename, text in index['texts'].items():
        for match in re.finditer(r'\bDamageTaken\s*\([^;{}]*\)\s*(?:override\s*)?\{', text):
            handler = body(text, match.end() - 1)
            if re.search(r'\bdamage\s*=\s*(?:0\s*;|[^;]*GetHealth\s*\(\s*\)\s*-\s*1\b)', handler):
                add('Damage interception requires encounter review', 'review',
                    'Damage is cancelled or clamped in DamageTaken. This may intentionally protect an encounter phase. Helix/Lumbering Oaf showed why force-killing at 1 HP is unsafe; inspect phase completion, linked creatures and reset logic.',
                    file=filename, line=text.count('\n', 0, match.start()) + 1,
                    excerpt=handler[:1600], action='C++ and gameplay review; no automatic kill or flag removal')
    return findings


def scan(root=None, connect=None):
    root = Path(root or os.getenv('TRINITY_SOURCE_ROOT', '/trinitycore-source'))
    index = inventory(root)
    bindings = []
    db_errors = []
    connection = connect or CONNECT
    if connection is None:
        db_errors.append('Database connection not configured')
    else:
        try:
            with connection() as db:
                with db.cursor() as cur:
                    for table, key in [('creature_template', 'entry'), ('gameobject_template', 'entry'),
                                       ('spell_script_names', 'spell_id'), ('areatrigger_scripts', 'entry'),
                                       ('instance_template', 'map')]:
                        try:
                            cur.execute('SHOW COLUMNS FROM `' + table + '`')
                            cols = {r['Field'] for r in cur.fetchall()}
                            field = 'ScriptName' if 'ScriptName' in cols else 'script' if 'script' in cols else None
                            if not field or key not in cols:
                                raise ValueError('Required binding columns unavailable')
                            extra = ', AIName' if table == 'creature_template' and 'AIName' in cols else ''
                            where = "`" + field + "` <> ''"
                            if table == 'creature_template':
                                where += ' OR entry IN (39062,38142)'
                            cur.execute('SELECT `' + key + '` AS entity_id, `' + field + '` AS ScriptName' + extra +
                                        ' FROM `' + table + '` WHERE ' + where + ' LIMIT 20001')
                            rows = cur.fetchall()
                            if len(rows) > 20000:
                                db_errors.append(table + ': capped at 20000 bindings')
                            bindings.extend(dict(r, _table=table) for r in rows[:20000])
                        except Exception as exc:
                            db_errors.append(table + ': ' + str(exc))
        except Exception as exc:
            db_errors.append('Database inspection failed: ' + str(exc))
    findings = analyze(index, bindings)
    return {'scanned_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
            'source_root': str(root), 'source_digest': index['digest'],
            'files_scanned': index['files'], 'bytes_scanned': index['bytes'],
            'registered_script_names': len(index['scripts']), 'bindings_checked': len(bindings),
            'complete': index['complete'] and not db_errors,
            'errors': index['errors'] + db_errors, 'total_findings': len(findings),
            'capped': len(findings) > CAP, 'findings': findings[:CAP],
            'limitations': ['Lexical checks only: no compilation, control-flow proof or gameplay validation.',
                           'Source files may differ from the running binary or change during a scan.',
                           'Custom registration macros, generated loaders and build exclusions require manual verification.',
                           'No source or database changes are performed.']}


@router.get('/scan')
def endpoint():
    if not LOCK.acquire(blocking=False):
        raise HTTPException(409, 'A source scan is already running')
    try:
        from report_publication import queue_report
        report = scan()
        report["publication"] = queue_report("source", report)
        return report
    except (ValueError, OSError) as exc:
        raise HTTPException(503, str(exc))
    finally:
        LOCK.release()
