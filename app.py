import os
from pathlib import Path
from fastapi import Request
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import pymysql

app = FastAPI(title="TrinityCore World Auditor", version="0.02.006")

def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "10.0.0.115"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.getenv("DB_NAME", "world"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
        read_timeout=40, autocommit=True)

BASE_RULES = [
    ("quest", "Missing prerequisite quest", """SELECT a.ID AS entity_id, CONCAT('PrevQuestID ',a.PrevQuestID,' does not exist') AS detail FROM quest_template_addon a LEFT JOIN quest_template q ON q.ID=ABS(a.PrevQuestID) WHERE a.PrevQuestID<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Missing next quest", """SELECT a.ID AS entity_id, CONCAT('NextQuestID ',a.NextQuestID,' does not exist') AS detail FROM quest_template_addon a LEFT JOIN quest_template q ON q.ID=a.NextQuestID WHERE a.NextQuestID<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Missing reward-next quest", """SELECT a.ID AS entity_id, CONCAT('RewardNextQuest ',a.RewardNextQuest,' does not exist') AS detail FROM quest_template a LEFT JOIN quest_template q ON q.ID=a.RewardNextQuest WHERE a.RewardNextQuest<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Quest starter references missing quest", """SELECT a.quest AS entity_id, CONCAT('Creature ',a.id,' references absent quest') AS detail FROM creature_queststarter a LEFT JOIN quest_template q ON q.ID=a.quest WHERE q.ID IS NULL LIMIT 300"""),
    ("quest", "Quest ender references missing quest", """SELECT a.quest AS entity_id, CONCAT('Creature ',a.id,' references absent quest') AS detail FROM creature_questender a LEFT JOIN quest_template q ON q.ID=a.quest WHERE q.ID IS NULL LIMIT 300"""),
    ("creature", "Quest starter missing creature template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent creature') AS detail FROM creature_queststarter a LEFT JOIN creature_template c ON c.entry=a.id WHERE c.entry IS NULL LIMIT 300"""),
    ("creature", "Quest ender missing creature template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent creature') AS detail FROM creature_questender a LEFT JOIN creature_template c ON c.entry=a.id WHERE c.entry IS NULL LIMIT 300"""),
    ("creature", "Invalid creature level range", """SELECT entry AS entity_id, CONCAT('minlevel=',minlevel,', maxlevel=',maxlevel) AS detail FROM creature_template WHERE minlevel>maxlevel LIMIT 300"""),
    ("creature", "Nonpositive creature health modifier", """SELECT entry AS entity_id, name, CONCAT('HealthModifier=',HealthModifier, '; unit_flags=',unit_flags, '; flags_extra=',flags_extra, '; minlevel=',minlevel, '; maxlevel=',maxlevel, '; ScriptName=',COALESCE(NULLIF(ScriptName,''),'(none)'), '; assessment=needs review (attackability cannot be established from template flags alone)') AS detail, CASE WHEN (unit_flags & 2) <> 0 OR (unit_flags & 33554432) <> 0 THEN 'special-purpose candidate' ELSE 'needs review' END AS assessment FROM creature_template WHERE HealthModifier<=0 LIMIT 300"""),
    ("gameobject", "Quest starter missing gameobject template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent gameobject') AS detail FROM gameobject_queststarter a LEFT JOIN gameobject_template g ON g.entry=a.id WHERE g.entry IS NULL LIMIT 300"""),
    ("gameobject", "Quest ender missing gameobject template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent gameobject') AS detail FROM gameobject_questender a LEFT JOIN gameobject_template g ON g.entry=a.id WHERE g.entry IS NULL LIMIT 300"""),
]

 
# Additional rules use established TrinityCore Cataclysm world tables.
# A rule failing due to schema differences is reported explicitly, never as zero findings.
EXTRA_RULES = [
    ("quest", "Quest prerequisite references itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has PrevQuestID=',PrevQuestID,' (self-reference)') AS detail FROM quest_template_addon WHERE PrevQuestID<>0 AND ABS(PrevQuestID)=ID LIMIT 300"""),
    ("quest", "Quest next reference points to itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has NextQuestID=',NextQuestID,' (self-reference)') AS detail FROM quest_template_addon WHERE NextQuestID=ID LIMIT 300"""),
    ("quest", "Quest reward-next references itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has RewardNextQuest=',RewardNextQuest,' (self-reference)') AS detail FROM quest_template WHERE RewardNextQuest=ID LIMIT 300"""),
    ("creature", "Creature spawns missing template", """SELECT c.id AS entity_id, CONCAT('Spawn GUID ',c.guid,' has no creature_template') AS detail FROM creature c LEFT JOIN creature_template t ON t.entry=c.id WHERE t.entry IS NULL LIMIT 300"""),
    ("gameobject", "Gameobject spawns missing template", """SELECT g.id AS entity_id, CONCAT('Spawn GUID ',g.guid,' has no gameobject_template') AS detail FROM gameobject g LEFT JOIN gameobject_template t ON t.entry=g.id WHERE t.entry IS NULL LIMIT 300"""),
    ("creature", "Creature templates with negative scale", """SELECT entry AS entity_id, CONCAT('scale=',scale) AS detail FROM creature_template WHERE scale<0 LIMIT 300"""),
    ("quest", "Quest starter references missing creature", """SELECT s.id AS entity_id, CONCAT('Quest ',s.quest,' starter has no creature_template') AS detail FROM creature_queststarter s LEFT JOIN creature_template t ON t.entry=s.id WHERE t.entry IS NULL LIMIT 300"""),
    ("quest", "Quest ender references missing creature", """SELECT s.id AS entity_id, CONCAT('Quest ',s.quest,' ender has no creature_template') AS detail FROM creature_questender s LEFT JOIN creature_template t ON t.entry=s.id WHERE t.entry IS NULL LIMIT 300"""),
    ("loot", "Creature loot references missing creature", """SELECT t.entry AS entity_id, CONCAT('Creature ',t.entry,' loot ID ',t.lootid,' has no creature_loot_template') AS detail FROM creature_template t LEFT JOIN creature_loot_template l ON l.Entry=t.lootid WHERE t.lootid>0 AND l.Entry IS NULL LIMIT 300"""),
    ("loot", "Gameobject loot references missing gameobject", """SELECT t.entry AS entity_id, CONCAT('Gameobject loot ID ',t.Data1,' has no gameobject_loot_template') AS detail FROM gameobject_template t LEFT JOIN gameobject_loot_template l ON l.Entry=t.Data1 WHERE t.type=3 AND t.Data1>0 AND l.Entry IS NULL LIMIT 300"""),
    ("vendor", "Vendor references missing creature", """SELECT v.entry AS entity_id, CONCAT('npc_vendor references missing creature_template ',v.entry) AS detail FROM npc_vendor v LEFT JOIN creature_template t ON t.entry=v.entry WHERE t.entry IS NULL GROUP BY v.entry LIMIT 300"""),
    ("gossip", "Gossip menu references missing text", """SELECT g.MenuID AS entity_id, CONCAT('gossip_menu references missing npc_text ',g.TextID) AS detail FROM gossip_menu g LEFT JOIN npc_text n ON n.ID=g.TextID WHERE g.TextID<>0 AND n.ID IS NULL LIMIT 300"""),
    ("movement", "Spawn with negative wander distance", """SELECT guid AS entity_id, CONCAT('creature ',id,' wander_distance=',wander_distance) AS detail FROM creature WHERE wander_distance<0 LIMIT 300"""),
    ("scripts", "SmartAI source references missing creature template", """SELECT s.entryorguid AS entity_id, CONCAT('smart_scripts source_type=0 references missing creature_template') AS detail FROM smart_scripts s LEFT JOIN creature_template t ON t.entry=s.entryorguid WHERE s.source_type=0 AND s.entryorguid>0 AND t.entry IS NULL GROUP BY s.entryorguid LIMIT 300"""),
]
RULES = BASE_RULES + EXTRA_RULES

SEVERITY = {
    "Nonpositive creature health modifier": "review",
    "Creature templates with negative scale": "warning",
    "Invalid creature level range": "warning",
    "Spawn with negative wander distance": "warning",
}

@app.get("/api/health")
def health():
    try:
        with connect() as db:
            with db.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM quest_template")
                return {"connected": True, "quest_count": cur.fetchone()["count"]}
    except Exception:
        raise HTTPException(503, "Database unavailable")

@app.get("/api/scan")
def scan():
    results = []
    try:
        with connect() as db:
            with db.cursor() as cur:
                for module, name, sql in RULES:
                    try:
                        cur.execute(sql)
                        rows = cur.fetchall()
                        results.append({"module": module, "rule": name, "severity": SEVERITY.get(name, "warning"), "findings": rows, "count": len(rows), "capped": len(rows) == 300})
                    except Exception as exc:
                        results.append({"module": module, "rule": name, "severity": "error", "error": str(exc), "findings": [], "count": 0})
    except Exception:
        raise HTTPException(503, "Database unavailable")
    # Resolve display names by entity type, without changing the original numeric IDs.
    # Schema discovery avoids assuming a particular TrinityCore quest title column.
    try:
        with connect() as db:
            with db.cursor() as cur:
                def lookup(table, id_column, name_columns, ids):
                    if not ids:
                        return {}
                    cur.execute("SHOW COLUMNS FROM `" + table + "`")
                    columns = {row["Field"] for row in cur.fetchall()}
                    title = next((col for col in name_columns if col in columns), None)
                    if not title or id_column not in columns:
                        return {}
                    result = {}
                    values = sorted(ids)
                    for offset in range(0, len(values), 300):
                        batch = values[offset:offset + 300]
                        sql = ("SELECT `" + id_column + "` AS entity_id, `" + title
                               + "` AS entity_name FROM `" + table + "` WHERE `"
                               + id_column + "` IN (" + ",".join(["%s"] * len(batch)) + ")")
                        cur.execute(sql, batch)
                        result.update({str(row["entity_id"]): row["entity_name"] for row in cur.fetchall()})
                    return result

                def kind(rule):
                    if rule["module"] == "quest":
                        return "quest" if not ("missing creature" in rule["rule"].lower()) else "creature"
                    if rule["module"] == "movement":
                        return "spawn"
                    if rule["module"] == "loot":
                        return "gameobject" if rule["rule"].startswith("Gameobject") else "creature"
                    if rule["module"] == "gameobject":
                        return "gameobject"
                    if rule["module"] in ("creature", "vendor", "scripts"):
                        return "creature"
                    return None

                grouped = {"quest": set(), "creature": set(), "gameobject": set(), "item": set(), "spawn": set()}
                for rule in results:
                    entity_kind = kind(rule)
                    if not entity_kind:
                        continue
                    for finding in rule["findings"]:
                        try:
                            entity_id = int(finding["entity_id"])
                            if entity_id > 0:
                                grouped[entity_kind].add(entity_id)
                        except (ValueError, TypeError, KeyError):
                            continue
                # Movement findings use spawn GUIDs, not creature template entries.
                spawn_to_creature = {}
                if grouped["spawn"]:
                    for offset in range(0, len(grouped["spawn"]), 300):
                        batch = sorted(grouped["spawn"])[offset:offset + 300]
                        cur.execute("SELECT guid,id FROM creature WHERE guid IN (" + ",".join(["%s"] * len(batch)) + ")", batch)
                        spawn_to_creature.update({str(row["guid"]): str(row["id"]) for row in cur.fetchall()})
                    grouped["creature"].update(int(v) for v in spawn_to_creature.values())
                names = {
                    "quest": lookup("quest_template", "ID", ("LogTitle", "Title"), grouped["quest"]),
                    "creature": lookup("creature_template", "entry", ("name", "Name"), grouped["creature"]),
                    "gameobject": lookup("gameobject_template", "entry", ("name", "Name"), grouped["gameobject"]),
                    "item": lookup("item_template", "entry", ("name", "Name"), grouped["item"]),
                }
                for rule in results:
                    entity_kind = kind(rule)
                    for finding in rule["findings"]:
                        key = str(finding.get("entity_id", ""))
                        if entity_kind == "spawn":
                            creature_id = spawn_to_creature.get(key)
                            if creature_id:
                                finding["name"] = names["creature"].get(creature_id, "") or ""
                                finding["creature_entry"] = creature_id
                        elif entity_kind in names:
                            finding["name"] = finding.get("name") or names[entity_kind].get(key, "") or ""
    except Exception:
        # Name resolution must not cause a successful integrity scan to fail.
        pass
    return {"scanned_at": datetime.now(timezone.utc).isoformat(), "rules": results, "total_findings": sum(r["count"] for r in results), "error_rules": sum(bool(r.get("error")) for r in results), "capped_rules": sum(bool(r.get("capped")) for r in results), "read_only": True}

@app.post("/api/request-update")
async def request_update(request: Request):
    # The updater runs on the host, not inside the database-scanning container.
    # Only accept same-origin requests to the LAN-bound service.
    host = request.headers.get("host", "").split(":")[0]
    origin = request.headers.get("origin", "")
    if host != "10.0.0.118" or origin != "http://10.0.0.118:3050":
        raise HTTPException(403, "Update requests are restricted to the LAN dashboard")
    marker = Path("/update-requests/request")
    if not marker.parent.is_dir():
        raise HTTPException(503, "Host update request directory is unavailable")
    marker.write_text("requested\\n")
    return {"queued": True, "message": "Update queued; host updater checks every minute. Reload after deployment."}

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>TrinityCore World Auditor</title><style>
body{font:15px system-ui;background:#101725;color:#e9edf5;max-width:1050px;margin:40px auto;padding:0 20px}h1{font-size:29px}button{background:#5087ee;color:white;border:0;border-radius:8px;padding:12px 18px;cursor:pointer;font-weight:700}button:disabled{opacity:.5}select{background:#1b2638;color:#e9edf5;border:1px solid #334359;border-radius:8px;padding:11px;margin-left:8px}.panel{background:#1b2638;border:1px solid #334359;border-radius:12px;padding:18px;margin:16px 0}small{color:#a8b6c9}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #334359}code{color:#a6d9ff}summary{cursor:pointer;font-weight:650}#status{margin-left:12px}
</style></head><body><h1>TrinityCore World Auditor</h1><p>Live MariaDB integrity scanner · Read-only · v0.02.006</p><button onclick="run()" id="run">Run world scan</button> <button onclick="updateApp()" id="update">Update auditor</button> <button onclick="downloadReport()" id="download" disabled>Download JSON report</button><select id="filter" onchange="renderReport()"><option value="all">All findings</option><option value="nonzero">Findings only</option><option value="error">SQL errors</option><option value="review">Needs review</option></select><span id="status"></span><div id="results"></div><script>
async function updateApp(){if(!confirm('Update auditor now?'))return;let b=document.getElementById('update');b.disabled=true;try{let r=await fetch('/api/request-update',{method:'POST'});let d=await r.json();if(!r.ok)throw Error(d.detail||'Update request failed');alert(d.message)}catch(e){alert(e.message)}finally{b.disabled=false}}
let lastReport=null;const el=document.getElementById('results');
function downloadReport(){if(!lastReport)return;const blob=new Blob([JSON.stringify(lastReport,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='world-audit-'+lastReport.scanned_at.replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function renderReport(){if(!lastReport)return;const filter=document.getElementById('filter').value;const rules=lastReport.rules.filter(r=>filter==='all'||(filter==='nonzero'&&r.count>0)||(filter==='error'&&r.error)||(filter==='review'&&r.severity==='review'));el.innerHTML=rules.map(r=>'<div class="panel"><details><summary>'+safe(r.module)+' · '+safe(r.rule)+' ['+safe(r.severity)+'] ('+r.count+')</summary>'+(r.error?'<p>Error: '+safe(r.error)+'</p>':'')+(r.capped?'<p>Showing first 300 findings; additional records may exist.</p>':'')+'<table><tr><th>ID</th><th>Name</th><th>Finding</th></tr>'+r.findings.map(f=>'<tr><td><code>'+safe(f.entity_id)+'</code></td><td>'+safe(f.name||'—')+'</td><td>'+safe(f.detail)+'</td></tr>').join('')+'</table></details></div>').join('')||'<p>No rules match this filter.</p>'}
function safe(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function run(){document.getElementById('run').disabled=true;document.getElementById('status').textContent='Scanning…';try{let response=await fetch('/api/scan');let data=await response.json();if(!response.ok)throw Error(data.detail||'Scan failed');lastReport=data;document.getElementById('download').disabled=false;document.getElementById('status').textContent=data.total_findings+' findings · '+data.rules.length+' rules · '+data.error_rules+' SQL errors'+(data.capped_rules?' · '+data.capped_rules+' capped':'');renderReport()}catch(e){document.getElementById('status').textContent='Error: '+e.message}finally{document.getElementById('run').disabled=false}}
fetch('/api/health').then(r=>r.json()).then(d=>document.getElementById('status').textContent=d.connected?'Connected · '+d.quest_count+' quests':'Disconnected').catch(()=>document.getElementById('status').textContent='Database unavailable');</script></body></html>"""
