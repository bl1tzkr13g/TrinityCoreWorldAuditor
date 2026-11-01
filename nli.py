"""NLI legacy quest review and SQL preview. No database writes."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from inspectors import CONNECTION
router = APIRouter(prefix="/api/nli", tags=["legacy-quests"])
# Verified by in-game observation of duplicate offers; other proposed pairs require validation.
PAIRS = [
    {"legacy": 764, "replacement": 26179, "title": "The Venture Co.", "status": "observed_conflict"},
    {"legacy": 765, "replacement": 26180, "title": "Supervisor Fizsprocket", "status": "observed_conflict"},
]
CANDIDATES = [
    (3902,26365),(54,26372),(62,26388),(102,26214),(151,26233),
    (179,24479),(218,26207),(267,24490),(271,25925),(483,28734),
    (984,13516),(951,13524),(4402,26188),(761,26177),(844,26194),
    (840,26927),(793,26955),(1656,26166),(374,24959),(377,24970),
    (435,26964),(98,26723),(219,26639),(2821,25482),(1249,27129),
    (55,26645),
]
TABLES = ("creature_queststarter", "gameobject_queststarter")
def gather():
    try:
        with CONNECTION() as db:
            with db.cursor() as cur:
                result = []
                for pair in PAIRS:
                    ids = (pair["legacy"], pair["replacement"])
                    cur.execute("SELECT ID, LogTitle FROM quest_template WHERE ID IN (%s,%s)", ids)
                    quests = {r["ID"]: r["LogTitle"] for r in cur.fetchall()}
                    starters = {}
                    for table in TABLES:
                        cur.execute("SELECT id,quest FROM "+table+" WHERE quest IN (%s,%s) ORDER BY id,quest", ids)
                        starters[table] = cur.fetchall()
                    shared = {}
                    for table, rows in starters.items():
                        old = {r["id"] for r in rows if r["quest"] == ids[0]}
                        new = {r["id"] for r in rows if r["quest"] == ids[1]}
                        shared[table] = sorted(old & new)
                    result.append({**pair,"quests":quests,"starters":starters,"shared_starters":shared,
                        "review_required":True,"nli_suggested":bool(any(shared.values()))})
                return result
    except Exception:
        raise HTTPException(503, "Unable to inspect NLI quest associations")
@router.get("/review")
def review():
    return {"read_only":True,"write_enabled":False,"verified_pairs":gather(),
        "unverified_candidates":[{"legacy":a,"replacement":b,"status":"unverified"} for a,b in CANDIDATES],
        "notice":"NLI is an auditor classification only; quest titles and database records are unchanged."}
@router.get("/preview.sql",response_class=PlainTextResponse)
def preview():
    rows = gather()
    lines = ["-- REVIEW ONLY. Not executed by auditor.",
        "-- Snapshot-based preview; recheck associations and back up database before any manual execution.",
        "-- Only shared quest starters for observed duplicate pairs are included.",
        "-- No changes to quest_template, quest enders, or other records."]
    for row in rows:
        for table, ids in row["shared_starters"].items():
            for npc in ids:
                lines.append("-- "+row["title"]+" legacy "+str(row["legacy"])+" -> "+str(row["replacement"]))
                lines.append("DELETE FROM `"+table+"` WHERE id="+str(int(npc))+" AND quest="+str(row["legacy"])+";")
    if not any(row["shared_starters"][t] for row in rows for t in TABLES):
        lines.append("-- No overlapping legacy starter associations found.")
    return "\n".join(lines)+"\n"
