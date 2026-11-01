"""Read-only quest-chain graph for duplicate-title false-positive filtering."""
from collections import deque
from fastapi import APIRouter, HTTPException
router = APIRouter(prefix="/api/quest-chain", tags=["quest-chain"])
CONNECT = None
def configure(connect):
    global CONNECT
    CONNECT = connect

def build_graph(cur):
    graph = {}
    def edge(a,b):
        a,b=int(a),int(b)
        if a > 0 and b > 0 and a != b:
            graph.setdefault(a,set()).add(b)
            graph.setdefault(b,set()).add(a)
    cur.execute("SELECT ID, PrevQuestID, NextQuestID FROM quest_template_addon")
    for r in cur.fetchall():
        q=int(r["ID"])
        for field in ("PrevQuestID","NextQuestID"):
            n=abs(int(r.get(field) or 0))
            if n: edge(q,n)
    cur.execute("SELECT ID, RewardNextQuest FROM quest_template WHERE RewardNextQuest <> 0")
    for r in cur.fetchall():
        edge(r["ID"],abs(int(r["RewardNextQuest"])))
    return graph

def path(graph,start,end,max_depth=12):
    start,end=int(start),int(end)
    if start == end: return [start]
    queue=deque([(start,[start])])
    seen={start}
    while queue:
        current,route=queue.popleft()
        if len(route)>max_depth: continue
        for nxt in graph.get(current,()):
            if nxt==end: return route+[end]
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt,route+[nxt]))
    return None

@router.get("/{first}/{second}")
def trace(first:int,second:int):
    if min(first,second)<=0 or max(first,second)>10000000:
        raise HTTPException(400,"Invalid quest ID")
    try:
        with CONNECT() as db:
            with db.cursor() as cur:
                graph=build_graph(cur)
                route=path(graph,first,second)
                return {"first":first,"second":second,"linked":route is not None,
                        "path":route,"max_depth":12,"read_only":True,
                        "note":"Edges from PrevQuestID, NextQuestID and RewardNextQuest. A missing path does not prove quests are unrelated."}
    except Exception:
        raise HTTPException(503,"Quest chain inspection unavailable")
