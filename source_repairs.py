"""Verified source repair recipes. Preview only: mounted files remain read-only."""
import difflib
import hashlib
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix='/api/source/repairs', tags=['source-repairs'])
RECIPES = {
    'camel-spellclick-field': {
        'title': 'Halls of Origination camel spellclick field',
        'path': 'src/server/scripts/Kalimdor/HallsOfOrigination/instance_halls_of_origination.cpp',
        'scope': None,
        'replacements': [
            ('camel->RemoveFlag(UNIT_FIELD_FLAGS, UNIT_NPC_FLAG_SPELLCLICK)', 'camel->RemoveFlag(UNIT_NPC_FLAGS, UNIT_NPC_FLAG_SPELLCLICK)'),
            ('camel->SetFlag(UNIT_FIELD_FLAGS, UNIT_NPC_FLAG_SPELLCLICK)', 'camel->SetFlag(UNIT_NPC_FLAGS, UNIT_NPC_FLAG_SPELLCLICK)'),
        ],
        'reference_commit': '82ce6ed9c2849897df475db0ab77682f42462a7f',
    },
    'feign-death-96733-field': {
        'title': 'Spell 96733 feign-death secondary flag field',
        'path': 'src/server/scripts/Spells/spell_generic.cpp',
        'scope': 'spell_gen_feign_death_all_flags_no_uninteractible',
        'replacements': [
            ('target->SetFlag(UNIT_FIELD_FLAGS, UNIT_FLAG2_FEIGN_DEATH)', 'target->SetFlag(UNIT_FIELD_FLAGS_2, UNIT_FLAG2_FEIGN_DEATH)'),
            ('target->RemoveFlag(UNIT_FIELD_FLAGS, UNIT_FLAG2_FEIGN_DEATH)', 'target->RemoveFlag(UNIT_FIELD_FLAGS_2, UNIT_FLAG2_FEIGN_DEATH)'),
        ],
    },
}


def plan(recipe_id, root=None):
    from source_scan import body, uncomment
    import re
    if recipe_id not in RECIPES:
        raise HTTPException(404, 'Unknown verified repair recipe')
    recipe = RECIPES[recipe_id]
    root = Path(root or os.getenv('TRINITY_SOURCE_ROOT', '/trinitycore-source')).resolve()
    path = root / recipe['path']
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise HTTPException(409, 'Repair target must be a regular file within source root')
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise HTTPException(409, 'Repair target exceeds size limit')
        original = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return {'id': recipe_id, 'title': recipe['title'], 'status': 'unavailable', 'path': recipe['path'], 'patch': ''}
    start, end = 0, len(original)
    if recipe['scope']:
        clean = uncomment(original)
        matches = list(re.finditer(r'\bclass\s+' + re.escape(recipe['scope']) + r'[^;{}]*\{', clean))
        if len(matches) != 1:
            return {'id': recipe_id, 'title': recipe['title'], 'status': 'conflict', 'reason': 'Expected class is missing or ambiguous', 'patch': ''}
        # uncomment() preserves offsets, including newlines.
        start = matches[0].end()-1
        end = start + len(body(clean, start))
    block = original[start:end]
    counts = [(block.count(old), block.count(new)) for old, new in recipe['replacements']]
    result = {'id': recipe_id, 'title': recipe['title'], 'path': recipe['path'],
              'source_sha256': hashlib.sha256(original.encode()).hexdigest(),
              'automatic_apply': False, 'requires_rebuild': True,
              'validation': 'Run git apply --check before applying. Compile and test in-game after applying.',
              'patch': ''}
    if all(old == 0 and new == 1 for old, new in counts):
        result['status'] = 'already_fixed'
        return result
    if not all((old, new) in ((1,0),(0,1)) for old,new in counts):
        result.update(status='conflict',reason='Expected operations differ from the verified recipe; local edits preserved')
        return result
    updated = block
    for old, new in recipe['replacements']:
        updated = updated.replace(old, new)
    changed = original[:start] + updated + original[end:]
    result['status'] = 'ready_for_review'
    result['patch'] = ''.join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                                 fromfile='a/'+recipe['path'], tofile='b/'+recipe['path']))
    return result


def summaries(root=None):
    return [{k:v for k,v in plan(name,root).items() if k!='patch'} for name in RECIPES]


@router.get('')
def list_plans():
    return {'read_only': True, 'repairs': summaries()}


@router.get('/{recipe_id}.patch', response_class=PlainTextResponse)
def patch(recipe_id: str):
    result = plan(recipe_id)
    if result['status'] != 'ready_for_review':
        raise HTTPException(409, result['status'])
    return PlainTextResponse(result['patch'], headers={'Content-Disposition': 'attachment; filename="'+recipe_id+'.patch"'})
