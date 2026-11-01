"""Read-only, schema-aware record explorer for TrinityCore world databases."""
from fastapi import APIRouter, HTTPException
from pymysql import MySQLError

router = APIRouter(prefix="/api/explore", tags=["explorer"])
CONNECTION = None

def configure(connect):
    global CONNECTION
    CONNECTION = connect

TABLES = {
    "quest": ("quest_template", "ID", ("LogTitle", "Title")),
    "npc": ("creature_template", "entry", ("name", "Name")),
    "gameobject": ("gameobject_template", "entry", ("name", "Name")),
    "item": ("item_template", "entry", ("name", "Name")),
}

def columns(cur, table):
    cur.execute("SHOW COLUMNS FROM `" + table + "`")
    return {r["Field"] for r in cur.fetchall()}

def fetch(cur, table, col, value, limit=100, select="*"):
    cur.execute("SELECT " + select + " FROM `" + table + "` WHERE `" + col + "`=%s LIMIT %s", (value, limit))
    return cur.fetchall()

def section(cur, table, col, value, limit=100, select="*", extra=None):
    try:
        available = columns(cur, table)
        if col not in available:
            return {"rows": [], "error": "Column " + col + " missing in " + table}
        rows = fetch(cur, table, col, value, limit, select)
        return {"rows": rows, "capped": len(rows) == limit}
    except MySQLError as exc:
        return {"rows": [], "error": str(exc)}

def link_rows(cur, table, id_col, ids, kind):
    if not ids:
        return []
    try:
        avail = columns(cur, table)
        title = next((c for c in TABLES[kind][2] if c in avail), None)
        if not title:
            return [{"kind": kind, "id": n} for n in sorted(ids)]
        vals = sorted(ids)[:150]
        cur.execute("SELECT `" + id_col + "` AS id, `" + title + "` AS name FROM `" + table + "` WHERE `" + id_col + "` IN (" + ",".join(["%s"] * len(vals)) + ")", vals)
        names = {r["id"]: r["name"] for r in cur.fetchall()}
        return [{"kind": kind, "id": n, "name": names.get(n, "")} for n in vals]
    except MySQLError:
        return [{"kind": kind, "id": n} for n in sorted(ids)[:150]]

def ids(section_data, key):
    out = set()
    for r in section_data.get("rows", []):
        try:
            n = int(r.get(key) or 0)
            if n > 0:
                out.add(n)
        except (TypeError, ValueError):
            pass
    return out

def inspect(kind, record_id):
    if kind not in TABLES:
        raise HTTPException(404, "Unknown entity type")
    if record_id <= 0 or record_id > 100000000:
        raise HTTPException(400, "Invalid ID")
    table, primary, name_cols = TABLES[kind]
    try:
        with CONNECTION() as db:
            with db.cursor() as cur:
                record_section = section(cur, table, primary, record_id, 1)
                if record_section.get("error"):
                    raise HTTPException(503, record_section["error"])
                if not record_section["rows"]:
                    raise HTTPException(404, kind + " not found")
                record = record_section["rows"][0]
                name = next((record.get(c) for c in name_cols if record.get(c)), "")
                sections = {}
                links = []
                def add(label, table_name, field, value=record_id, limit=100):
                    sections[label] = section(cur, table_name, field, value, limit)
                    return sections[label]
                if kind == "quest":
                    addon = add("Quest settings", "quest_template_addon", "ID")
                    npc_start = add("NPC starters", "creature_queststarter", "quest")
                    npc_end = add("NPC enders", "creature_questender", "quest")
                    go_start = add("Gameobject starters", "gameobject_queststarter", "quest")
                    go_end = add("Gameobject enders", "gameobject_questender", "quest")
                    links += link_rows(cur, "creature_template", "entry", ids(npc_start, "id") | ids(npc_end, "id"), "npc")
                    links += link_rows(cur, "gameobject_template", "entry", ids(go_start, "id") | ids(go_end, "id"), "gameobject")
                    related = set()
                    for a in addon.get("rows", []):
                        for field in ("PrevQuestID", "NextQuestID"):
                            try:
                                v = abs(int(a.get(field) or 0))
                                if v and v != record_id:
                                    related.add(v)
                            except (ValueError, TypeError):
                                pass
                    try:
                        v = int(record.get("RewardNextQuest") or 0)
                        if v and v != record_id:
                            related.add(v)
                    except (ValueError, TypeError):
                        pass
                    links += link_rows(cur, "quest_template", "ID", related, "quest")
                    # Item objectives/rewards are often held in the quest template itself.
                    item_ids = set()
                    for col, val in record.items():
                        if col.startswith(("RequiredItemId", "RewardItemId", "RewardChoiceItemId", "RewardItemID", "RewardChoiceItemID")):
                            try:
                                n = int(val or 0)
                                if n > 0:
                                    item_ids.add(n)
                            except (ValueError, TypeError):
                                pass
                    links += link_rows(cur, "item_template", "entry", item_ids, "item")
                elif kind == "npc":
                    add("Spawns", "creature", "id")
                    start = add("Quests started", "creature_queststarter", "id")
                    end = add("Quests ended", "creature_questender", "id")
                    vendor = add("Vendor inventory", "npc_vendor", "entry")
                    add("SmartAI", "smart_scripts", "entryorguid")
                    add("Template movement", "creature_template_movement", "CreatureId")
                    links += link_rows(cur, "quest_template", "ID", ids(start, "quest") | ids(end, "quest"), "quest")
                    links += link_rows(cur, "item_template", "entry", ids(vendor, "item"), "item")
                    loot_id = record.get("lootid") or 0
                    if loot_id:
                        loot = add("Creature loot", "creature_loot_template", "Entry", loot_id)
                        links += link_rows(cur, "item_template", "entry", ids(loot, "Item"), "item")
                elif kind == "gameobject":
                    add("Spawns", "gameobject", "id")
                    start = add("Quests started", "gameobject_queststarter", "id")
                    end = add("Quests ended", "gameobject_questender", "id")
                    links += link_rows(cur, "quest_template", "ID", ids(start, "quest") | ids(end, "quest"), "quest")
                    if record.get("type") == 3 and record.get("Data1"):
                        loot = add("Gameobject loot", "gameobject_loot_template", "Entry", record["Data1"])
                        links += link_rows(cur, "item_template", "entry", ids(loot, "Item"), "item")
                elif kind == "item":
                    add("Vendor listings", "npc_vendor", "item")
                    add("Creature loot sources", "creature_loot_template", "Item")
                    add("Gameobject loot sources", "gameobject_loot_template", "Item")
                    vendors = sections["Vendor listings"]
                    links += link_rows(cur, "creature_template", "entry", ids(vendors, "entry"), "npc")
                    creature_loot = sections["Creature loot sources"]
                    loot_ids = ids(creature_loot, "Entry")
                    if loot_ids:
                        try:
                            avail = columns(cur, "creature_template")
                            if "lootid" in avail:
                                vals = sorted(loot_ids)[:100]
                                cur.execute("SELECT entry FROM creature_template WHERE lootid IN (" + ",".join(["%s"]*len(vals)) + ") LIMIT 150", vals)
                                links += link_rows(cur, "creature_template", "entry", {r["entry"] for r in cur.fetchall()}, "npc")
                        except MySQLError:
                            pass
                dedup = {}
                for link in links:
                    dedup[(link["kind"], link["id"])] = link
                return {"kind": kind, "id": record_id, "name": name, "record": record,
                        "sections": sections, "links": list(dedup.values()), "read_only": True,
                        "notes": "Links are evidence of database relationships, not proof of gameplay correctness. Sections are capped at 100 rows."}
    except HTTPException:
        raise
    except MySQLError:
        raise HTTPException(503, "Database inspection failed")

@router.get("/{kind}/{record_id}")
def explore(kind: str, record_id: int):
    return inspect(kind, record_id)
