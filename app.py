import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import pymysql

app = FastAPI(title="TrinityCore World Auditor", version="0.1.0")

def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "10.0.0.115"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.getenv("DB_NAME", "world"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
        read_timeout=40, autocommit=True)

RULES = [
    ("quest", "Missing prerequisite quest", """SELECT a.ID AS entity_id, CONCAT('PrevQuestID ',a.PrevQuestID,' does not exist') AS detail FROM quest_template_addon a LEFT JOIN quest_template q ON q.ID=ABS(a.PrevQuestID) WHERE a.PrevQuestID<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Missing next quest", """SELECT a.ID AS entity_id, CONCAT('NextQuestID ',a.NextQuestID,' does not exist') AS detail FROM quest_template_addon a LEFT JOIN quest_template q ON q.ID=a.NextQuestID WHERE a.NextQuestID<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Missing reward-next quest", """SELECT a.ID AS entity_id, CONCAT('RewardNextQuest ',a.RewardNextQuest,' does not exist') AS detail FROM quest_template a LEFT JOIN quest_template q ON q.ID=a.RewardNextQuest WHERE a.RewardNextQuest<>0 AND q.ID IS NULL LIMIT 300"""),
    ("quest", "Quest starter references missing quest", """SELECT a.quest AS entity_id, CONCAT('Creature ',a.id,' references absent quest') AS detail FROM creature_queststarter a LEFT JOIN quest_template q ON q.ID=a.quest WHERE q.ID IS NULL LIMIT 300"""),
    ("quest", "Quest ender references missing quest", """SELECT a.quest AS entity_id, CONCAT('Creature ',a.id,' references absent quest') AS detail FROM creature_questender a LEFT JOIN quest_template q ON q.ID=a.quest WHERE q.ID IS NULL LIMIT 300"""),
    ("creature", "Quest starter missing creature template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent creature') AS detail FROM creature_queststarter a LEFT JOIN creature_template c ON c.entry=a.id WHERE c.entry IS NULL LIMIT 300"""),
    ("creature", "Quest ender missing creature template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent creature') AS detail FROM creature_questender a LEFT JOIN creature_template c ON c.entry=a.id WHERE c.entry IS NULL LIMIT 300"""),
    ("creature", "Invalid creature level range", """SELECT entry AS entity_id, CONCAT('minlevel=',minlevel,', maxlevel=',maxlevel) AS detail FROM creature_template WHERE minlevel>maxlevel LIMIT 300"""),
    ("creature", "Nonpositive creature health modifier", """SELECT entry AS entity_id, CONCAT('HealthModifier=',HealthModifier) AS detail FROM creature_template WHERE HealthModifier<=0 LIMIT 300"""),
    ("gameobject", "Quest starter missing gameobject template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent gameobject') AS detail FROM gameobject_queststarter a LEFT JOIN gameobject_template g ON g.entry=a.id WHERE g.entry IS NULL LIMIT 300"""),
    ("gameobject", "Quest ender missing gameobject template", """SELECT a.id AS entity_id, CONCAT('Quest ',a.quest,' references absent gameobject') AS detail FROM gameobject_questender a LEFT JOIN gameobject_template g ON g.entry=a.id WHERE g.entry IS NULL LIMIT 300"""),
]

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
                        results.append({"module": module, "rule": name, "findings": rows, "count": len(rows), "capped": len(rows) == 300})
                    except Exception as exc:
                        results.append({"module": module, "rule": name, "error": str(exc), "findings": [], "count": 0})
    except Exception:
        raise HTTPException(503, "Database unavailable")
    return {"scanned_at": datetime.now(timezone.utc).isoformat(), "rules": results, "total_findings": sum(r["count"] for r in results), "read_only": True}

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>TrinityCore World Auditor</title><style>
body{font:15px system-ui;background:#101725;color:#e9edf5;max-width:1050px;margin:40px auto;padding:0 20px}h1{font-size:29px}button{background:#5087ee;color:white;border:0;border-radius:8px;padding:12px 18px;cursor:pointer;font-weight:700}button:disabled{opacity:.5}.panel{background:#1b2638;border:1px solid #334359;border-radius:12px;padding:18px;margin:16px 0}small{color:#a8b6c9}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #334359}code{color:#a6d9ff}summary{cursor:pointer;font-weight:650}#status{margin-left:12px}
</style></head><body><h1>TrinityCore World Auditor</h1><p>Live MariaDB integrity scanner · Read-only · v0.1</p><button onclick="run()" id="run">Run world scan</button><span id="status"></span><div id="results"></div><script>
const el=document.getElementById('results');function safe(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function run(){document.getElementById('run').disabled=true;document.getElementById('status').textContent='Scanning…';try{let response=await fetch('/api/scan');let data=await response.json();if(!response.ok)throw Error(data.detail||'Scan failed');document.getElementById('status').textContent=data.total_findings+' findings · '+data.rules.length+' rules';el.innerHTML=data.rules.map(r=>'<div class="panel"><details><summary>'+safe(r.module)+' · '+safe(r.rule)+' ('+r.count+')</summary>'+(r.error?'<p>Error: '+safe(r.error)+'</p>':'')+(r.capped?'<p>Showing first 300 findings.</p>':'')+'<table><tr><th>ID</th><th>Finding</th></tr>'+r.findings.map(f=>'<tr><td><code>'+safe(f.entity_id)+'</code></td><td>'+safe(f.detail)+'</td></tr>').join('')+'</table></details></div>').join('')}catch(e){document.getElementById('status').textContent='Error: '+e.message}finally{document.getElementById('run').disabled=false}}
fetch('/api/health').then(r=>r.json()).then(d=>document.getElementById('status').textContent=d.connected?'Connected · '+d.quest_count+' quests':'Disconnected').catch(()=>document.getElementById('status').textContent='Database unavailable');</script></body></html>"""
