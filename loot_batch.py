"""Review exact creature/item evidence and generate a read-only repair preview."""
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/loot")
connect = None


class Evidence(BaseModel):
    creature: int = Field(gt=0, le=4294967295)
    item: int = Field(gt=0, le=4294967295)
    source: str = Field(min_length=1, max_length=500)
    kind: Literal["corpse_drop", "alternate_path", "conflicting"]


def reachable(rows, references, item):
    pending = list(rows)
    visited = set()
    while pending:
        row = pending.pop()
        reference = row["Reference"]
        if not reference and row["Item"] == item and not row["IsCurrency"]:
            return True
        if reference and reference not in visited:
            visited.add(reference)
            pending.extend(references[reference])
    return False


@router.post("/batch-preview")
def preview(evidence: list[Evidence]):
    """Treat submitted evidence as operator-reviewed assertions."""
    with connect() as db, db.cursor() as cursor:
        def read(sql):
            cursor.execute(sql)
            return cursor.fetchall()

        creatures = {r["entry"]: r for r in read(
            "SELECT entry, lootid, difficulty_entry_1, difficulty_entry_2, "
            "difficulty_entry_3 FROM creature_template"
        )}
        users = defaultdict(set)
        for creature, row in creatures.items():
            users[row["lootid"]].add(creature)
        columns = ("Entry, Item, Reference, IsCurrency, Chance, QuestRequired, "
                   "LootMode, GroupId, MinCount, MaxCount")
        direct = defaultdict(list)
        refs = defaultdict(list)
        for table, index in (("creature_loot_template", direct),
                             ("reference_loot_template", refs)):
            for row in read(f"SELECT {columns} FROM {table}"):
                index[row["Entry"]].append(row)
        objectives = defaultdict(set)
        for row in read("SELECT ID, " + ", ".join(
            f"RequiredItemId{i}" for i in range(1, 7)
        ) + " FROM quest_template"):
            for key, item in row.items():
                if key != "ID" and item:
                    objectives[item].add(row["ID"])

    accepted = defaultdict(set)
    blocked = set()
    for entry in evidence:
        pair = (entry.creature, entry.item)
        if entry.kind == "corpse_drop" and entry.source.strip():
            accepted[entry.item].add(entry.creature)
        else:
            blocked.add(pair)

    review = []
    changes = []
    forward = []
    reverse = []
    seen = set()
    table = "creature_loot_template"
    for entry in evidence:
        creature, item = entry.creature, entry.item
        row = creatures.get(creature)
        loot = row["lootid"] if row else 0
        pair = (loot, item)
        affected = sorted(users[loot]) if loot else [creature]
        reason = None
        if (creature, item) in blocked:
            reason = "Alternate or conflicting evidence"
        elif not loot or not objectives[item]:
            reason = "Missing loot ID or quest objective"
        elif any(c not in accepted[item] or (c, item) in blocked for c in affected):
            reason = "Shared loot has unverified users"
        elif any(creatures[c][f"difficulty_entry_{i}"]
                 for c in affected for i in range(1, 4)):
            reason = "Difficulty variants need review"
        if reason:
            review.append({**entry.model_dump(), "reason": reason,
                           "affected_creatures": affected})
            continue
        if pair in seen:
            continue
        seen.add(pair)
        existing = [r for r in direct[loot] if r["Item"] == item]
        referenced = reachable(
            [r for r in direct[loot] if r["Reference"]], refs, item
        )
        if referenced or (existing and (
            len(existing) != 1 or existing[0]["Reference"]
            or existing[0]["IsCurrency"] or existing[0]["GroupId"]
            or existing[0]["LootMode"] != 1
        )):
            review.append({**entry.model_dump(), "reason": "Complex loot path",
                           "affected_creatures": affected})
            continue
        key = f"Entry={loot} AND Item={item}"
        guard = (
            f"(SELECT COUNT(*) FROM creature_template WHERE lootid={loot})"
            f"={len(affected)} AND NOT EXISTS (SELECT 1 FROM creature_template "
            f"WHERE lootid={loot} AND entry NOT IN ({','.join(map(str, affected))}))"
        )
        if existing:
            old = existing[0]
            if old["Chance"] == 100 and old["QuestRequired"] == 1:
                continue
            fields = ["Reference", "IsCurrency", "LootMode", "GroupId",
                      "MinCount", "MaxCount"]
            key += "".join(f" AND {field}={int(old[field])}" for field in fields)
            before = f"Chance={float(old['Chance'])} AND QuestRequired={int(old['QuestRequired'])}"
            forward.append(f"UPDATE {table} SET Chance=100, QuestRequired=1 "
                           f"WHERE {key} AND {before} AND {guard};")
            reverse.append(f"UPDATE {table} SET {before.replace(' AND ', ', ')} "
                           f"WHERE {key} AND Chance=100 AND QuestRequired=1;")
        else:
            forward.append(
                f"INSERT INTO {table} (Entry, Item, Reference, IsCurrency, Chance, "
                "QuestRequired, LootMode, GroupId, MinCount, MaxCount) "
                f"SELECT {loot}, {item}, 0, 0, 100, 1, 1, 0, 1, 1 "
                f"WHERE {guard} AND NOT EXISTS (SELECT 1 FROM {table} WHERE {key});"
            )
            reverse.append(f"DELETE FROM {table} WHERE {key} AND Reference=0 "
                           "AND IsCurrency=0 AND Chance=100 AND QuestRequired=1 "
                           "AND LootMode=1 AND GroupId=0 AND MinCount=1 AND MaxCount=1;")
        changes.append({"loot": loot, "item": item, "affected_creatures": affected,
                        "quest_ids": sorted(objectives[item]),
                        "evidence": [e.model_dump() for e in evidence
                                     if e.item == item and e.creature in users[loot]],
                        "action": "normalize" if existing else "insert"})

    def transaction(statements):
        return "\n".join(["START TRANSACTION;", *statements, "COMMIT;", ""])

    return {"changes": changes, "review": review, "apply_sql": transaction(forward),
            "rollback_sql": transaction(reversed(reverse))}
