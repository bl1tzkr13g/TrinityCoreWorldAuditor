import os
import re
from pathlib import Path
from fastapi import Request
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import pymysql
from inspectors import router as explorer_router, configure as configure_explorer
from nli import router as nli_router
from quest_chains import router as chain_router, configure as configure_chains, build_graph, path as chain_path

from training_audit import router as training_router, configure as configure_training
from source_repairs import router as source_repairs_router
from report_publication import queue_report, router as reports_router
from source_scan import router as source_router, configure as configure_source

app = FastAPI(title="TrinityCore World Auditor", version="0.02.035")

def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "10.0.0.115"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.getenv("DB_NAME", "world"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
        read_timeout=40, autocommit=True)

configure_training(connect)
app.include_router(training_router)
configure_source(connect)
app.include_router(source_router)
app.include_router(reports_router)
app.include_router(source_repairs_router)

configure_explorer(connect)
app.include_router(explorer_router)
app.include_router(nli_router)
configure_chains(connect)
app.include_router(chain_router)

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
    ("quest", "Duplicate NPC quest starter links", """SELECT s.quest AS entity_id, CONCAT('NPC ',s.id,' has ',COUNT(*),' identical quest starter links for quest ',s.quest) AS detail FROM creature_queststarter s GROUP BY s.id,s.quest HAVING COUNT(*)>1 LIMIT 300"""),
    ("quest", "Duplicate NPC quest ender links", """SELECT s.quest AS entity_id, CONCAT('NPC ',s.id,' has ',COUNT(*),' identical quest ender links for quest ',s.quest) AS detail FROM creature_questender s GROUP BY s.id,s.quest HAVING COUNT(*)>1 LIMIT 300"""),
    ("quest", "Duplicate gameobject quest starter links", """SELECT s.quest AS entity_id, CONCAT('Gameobject ',s.id,' has ',COUNT(*),' identical quest starter links for quest ',s.quest) AS detail FROM gameobject_queststarter s GROUP BY s.id,s.quest HAVING COUNT(*)>1 LIMIT 300"""),
    ("quest", "Duplicate gameobject quest ender links", """SELECT s.quest AS entity_id, CONCAT('Gameobject ',s.id,' has ',COUNT(*),' identical quest ender links for quest ',s.quest) AS detail FROM gameobject_questender s GROUP BY s.id,s.quest HAVING COUNT(*)>1 LIMIT 300"""),
    ("quest", "NPC offers different quests with same title", """SELECT q1.ID AS entity_id, CONCAT('NPC ',s1.id,' offers potentially overlapping quests ',q1.ID,' and ',q2.ID,' (',q1.LogTitle,'); classes=',COALESCE(a1.AllowableClasses,0),'/',COALESCE(a2.AllowableClasses,0),'; races=',COALESCE(a1.AllowableRaces,0),'/',COALESCE(a2.AllowableRaces,0),'; prerequisites=',COALESCE(a1.PrevQuestID,0),'/',COALESCE(a2.PrevQuestID,0),'; exclusive groups=',COALESCE(a1.ExclusiveGroup,0),'/',COALESCE(a2.ExclusiveGroup,0)) AS detail FROM creature_queststarter s1 JOIN creature_queststarter s2 ON s1.id=s2.id AND s1.quest<s2.quest JOIN quest_template q1 ON q1.ID=s1.quest JOIN quest_template q2 ON q2.ID=s2.quest AND q1.LogTitle=q2.LogTitle LEFT JOIN quest_template_addon a1 ON a1.ID=q1.ID LEFT JOIN quest_template_addon a2 ON a2.ID=q2.ID WHERE TRIM(COALESCE(q1.LogTitle,''))<>'' AND q1.LogTitle NOT LIKE '<NYI>%' AND (COALESCE(a1.AllowableClasses,0)=0 OR COALESCE(a2.AllowableClasses,0)=0 OR (a1.AllowableClasses & a2.AllowableClasses)<>0) AND (COALESCE(a1.AllowableRaces,0)=0 OR COALESCE(a2.AllowableRaces,0)=0 OR (a1.AllowableRaces & a2.AllowableRaces)<>0) AND NOT (COALESCE(a1.PrevQuestID,0)=q2.ID OR COALESCE(a2.PrevQuestID,0)=q1.ID OR COALESCE(a1.NextQuestID,0)=q2.ID OR COALESCE(a2.NextQuestID,0)=q1.ID) LIMIT 300"""),
    ("quest", "Gameobject offers different quests with same title", """SELECT q1.ID AS entity_id, CONCAT('Gameobject ',s1.id,' offers potentially overlapping quests ',q1.ID,' and ',q2.ID,' (',q1.LogTitle,'); classes=',COALESCE(a1.AllowableClasses,0),'/',COALESCE(a2.AllowableClasses,0),'; races=',COALESCE(a1.AllowableRaces,0),'/',COALESCE(a2.AllowableRaces,0),'; prerequisites=',COALESCE(a1.PrevQuestID,0),'/',COALESCE(a2.PrevQuestID,0),'; exclusive groups=',COALESCE(a1.ExclusiveGroup,0),'/',COALESCE(a2.ExclusiveGroup,0)) AS detail FROM gameobject_queststarter s1 JOIN gameobject_queststarter s2 ON s1.id=s2.id AND s1.quest<s2.quest JOIN quest_template q1 ON q1.ID=s1.quest JOIN quest_template q2 ON q2.ID=s2.quest AND q1.LogTitle=q2.LogTitle LEFT JOIN quest_template_addon a1 ON a1.ID=q1.ID LEFT JOIN quest_template_addon a2 ON a2.ID=q2.ID WHERE TRIM(COALESCE(q1.LogTitle,''))<>'' AND q1.LogTitle NOT LIKE '<NYI>%' AND (COALESCE(a1.AllowableClasses,0)=0 OR COALESCE(a2.AllowableClasses,0)=0 OR (a1.AllowableClasses & a2.AllowableClasses)<>0) AND (COALESCE(a1.AllowableRaces,0)=0 OR COALESCE(a2.AllowableRaces,0)=0 OR (a1.AllowableRaces & a2.AllowableRaces)<>0) AND NOT (COALESCE(a1.PrevQuestID,0)=q2.ID OR COALESCE(a2.PrevQuestID,0)=q1.ID OR COALESCE(a1.NextQuestID,0)=q2.ID OR COALESCE(a2.NextQuestID,0)=q1.ID) LIMIT 300"""),
    ("gossip", "Repeated gossip menu text entries", """SELECT MenuID AS entity_id, CONCAT('Menu ',MenuID,' repeats TextID ',TextID,' ',COUNT(*),' times') AS detail FROM gossip_menu GROUP BY MenuID,TextID HAVING COUNT(*)>1 LIMIT 300"""),
    ("gossip", "Repeated gossip menu option actions", """SELECT a.MenuID AS entity_id, CONCAT('Menu ',a.MenuID,' repeats option ',a.OptionText,' with same destination/action ',a.OptionType,'/',a.OptionNpcFlag,' and ',COALESCE(a.ActionMenuID,0),'/',COALESCE(a.ActionPoiID,0),'; option IDs: ',GROUP_CONCAT(a.OptionID ORDER BY a.OptionID)) AS detail FROM gossip_menu_option a WHERE TRIM(COALESCE(a.OptionText,''))<>'' GROUP BY a.MenuID,a.OptionText,a.OptionType,a.OptionNpcFlag,a.ActionMenuID,a.ActionPoiID HAVING COUNT(*)>1 LIMIT 300"""),
    ("quest", "Quest prerequisite references itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has PrevQuestID=',PrevQuestID,' (self-reference)') AS detail FROM quest_template_addon WHERE PrevQuestID<>0 AND ABS(PrevQuestID)=ID LIMIT 300"""),
    ("quest", "Quest next reference points to itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has NextQuestID=',NextQuestID,' (self-reference)') AS detail FROM quest_template_addon WHERE NextQuestID=ID LIMIT 300"""),
    ("quest", "Quest reward-next references itself", """SELECT ID AS entity_id, CONCAT('Quest ',ID,' has RewardNextQuest=',RewardNextQuest,' (self-reference)') AS detail FROM quest_template WHERE RewardNextQuest=ID LIMIT 300"""),
    ("creature", "Quest giver spawn overrides disable interaction", """SELECT c.id AS entity_id, t.name AS name, CONCAT('Spawn GUID ',c.guid,' on map ',c.map,' at (',ROUND(c.position_x,2),', ',ROUND(c.position_y,2),', ',ROUND(c.position_z,2),') has npcflag override=',c.npcflag,' while template npcflag=',t.npcflag,'; questgiver bit (2) cleared; linked starter quests=',(SELECT COUNT(*) FROM creature_queststarter qs WHERE qs.id=c.id),', ender quests=',(SELECT COUNT(*) FROM creature_questender qe WHERE qe.id=c.id),'; recommended review: use NULL to inherit template flags if override is unintended') AS detail FROM creature c JOIN creature_template t ON t.entry=c.id WHERE c.npcflag IS NOT NULL AND (t.npcflag & 2)<>0 AND (c.npcflag & 2)=0 AND (EXISTS (SELECT 1 FROM creature_queststarter qs WHERE qs.id=c.id) OR EXISTS (SELECT 1 FROM creature_questender qe WHERE qe.id=c.id)) LIMIT 300"""),
    ("creature", "Quest giver without permanent spawn", """SELECT t.entry AS entity_id, t.name AS name, CONCAT('Quest-related NPC ',t.entry,' has no records in creature; template npcflag=',t.npcflag,'; starter quests=',(SELECT COUNT(*) FROM creature_queststarter qs WHERE qs.id=t.entry),'; ender quests=',(SELECT COUNT(*) FROM creature_questender qe WHERE qe.id=t.entry),'; possible scripted/summoned/phased spawn: verify intended location and availability before adding a spawn') AS detail FROM creature_template t WHERE (EXISTS (SELECT 1 FROM creature_queststarter qs WHERE qs.id=t.entry) OR EXISTS (SELECT 1 FROM creature_questender qe WHERE qe.id=t.entry)) AND NOT EXISTS (SELECT 1 FROM creature c WHERE c.id=t.entry) LIMIT 300"""),
    ("creature", "Quest-linked NPC template missing questgiver flag", """SELECT t.entry AS entity_id, t.name AS name, CONCAT('NPC has quest starter/ender links but template npcflag=',t.npcflag,' (questgiver bit 2 absent); starter links=',(SELECT COUNT(*) FROM creature_queststarter qs WHERE qs.id=t.entry),'; ender links=',(SELECT COUNT(*) FROM creature_questender qe WHERE qe.id=t.entry),'; scripted quest interactions may be intentional') AS detail FROM creature_template t WHERE (t.npcflag & 2)=0 AND (EXISTS (SELECT 1 FROM creature_queststarter qs WHERE qs.id=t.entry) OR EXISTS (SELECT 1 FROM creature_questender qe WHERE qe.id=t.entry)) LIMIT 300"""),
    ("creature", "Quest giver spawn has interaction-restricting unit flags", """SELECT c.id AS entity_id, t.name AS name, CONCAT('Spawn GUID ',c.guid,' map ',c.map,' has unit_flags=',c.unit_flags,' (non-attackable/selectability-related bits); template unit_flags=',t.unit_flags,'; verify in-game selection and quest interaction; some restrictions are intentional') AS detail FROM creature c JOIN creature_template t ON t.entry=c.id WHERE (c.unit_flags & 33554432)<>0 AND (EXISTS (SELECT 1 FROM creature_queststarter qs WHERE qs.id=c.id) OR EXISTS (SELECT 1 FROM creature_questender qe WHERE qe.id=c.id)) LIMIT 300"""),
    ("creature", "Quest giver spawn uses explicit phase", """SELECT c.id AS entity_id, t.name AS name, CONCAT('Spawn GUID ',c.guid,' map ',c.map,' PhaseId=',c.PhaseId,', phaseMask=',c.phaseMask,', phaseUseFlags=',c.phaseUseFlags,'; verify questgiver visibility in the intended phase (not necessarily an error)') AS detail FROM creature c JOIN creature_template t ON t.entry=c.id WHERE c.PhaseId<>0 AND (EXISTS (SELECT 1 FROM creature_queststarter qs WHERE qs.id=c.id) OR EXISTS (SELECT 1 FROM creature_questender qe WHERE qe.id=c.id)) LIMIT 300"""),
    ("creature", "Creature spawns missing template", """SELECT c.id AS entity_id, CONCAT('Spawn GUID ',c.guid,' has no creature_template') AS detail FROM creature c LEFT JOIN creature_template t ON t.entry=c.id WHERE t.entry IS NULL LIMIT 300"""),
    ("gameobject", "Gameobject spawns missing template", """SELECT g.id AS entity_id, CONCAT('Spawn GUID ',g.guid,' has no gameobject_template') AS detail FROM gameobject g LEFT JOIN gameobject_template t ON t.entry=g.id WHERE t.entry IS NULL LIMIT 300"""),
    ("creature", "Creature templates with negative scale", """SELECT entry AS entity_id, CONCAT('scale=',scale) AS detail FROM creature_template WHERE scale<0 LIMIT 300"""),
    ("quest", "Quest starter references missing creature", """SELECT s.id AS entity_id, CONCAT('Quest ',s.quest,' starter has no creature_template') AS detail FROM creature_queststarter s LEFT JOIN creature_template t ON t.entry=s.id WHERE t.entry IS NULL LIMIT 300"""),
    ("quest", "Quest ender references missing creature", """SELECT s.id AS entity_id, CONCAT('Quest ',s.quest,' ender has no creature_template') AS detail FROM creature_questender s LEFT JOIN creature_template t ON t.entry=s.id WHERE t.entry IS NULL LIMIT 300"""),
    ("creature", "Quest objective target template sets unkillable", """
        WITH candidates AS (
            SELECT t.entry, t.name, q.ID AS quest_id, t.StaticFlags,
                   (COALESCE(t.ScriptName,'') <> '' OR COALESCE(t.AIName,'') <> ''
                    OR EXISTS (SELECT 1 FROM creature c WHERE c.id=t.entry
                               AND COALESCE(c.ScriptName,'') <> '')
                    OR EXISTS (SELECT 1 FROM smart_scripts ss WHERE ss.source_type=0
                               AND ss.entryorguid=t.entry)
                    OR EXISTS (SELECT 1 FROM creature c JOIN smart_scripts ss
                               ON ss.entryorguid=-CAST(c.guid AS SIGNED) AND ss.source_type=0
                               WHERE c.id=t.entry)) AS has_ai
            FROM creature_template t JOIN quest_template q ON
                (t.entry=q.RequiredNpcOrGo1 AND q.RequiredNpcOrGoCount1>0)
                OR (t.entry=q.RequiredNpcOrGo2 AND q.RequiredNpcOrGoCount2>0)
                OR (t.entry=q.RequiredNpcOrGo3 AND q.RequiredNpcOrGoCount3>0)
                OR (t.entry=q.RequiredNpcOrGo4 AND q.RequiredNpcOrGoCount4>0)
            WHERE (t.StaticFlags & 8) <> 0
              AND EXISTS (SELECT 1 FROM creature c WHERE c.id=t.entry)
        )
        SELECT entry AS entity_id, name, quest_id, StaticFlags AS static_flags, has_ai,
               CONCAT('Quest ', quest_id, ' references this creature. ',
                      CASE WHEN has_ai THEN 'AI configured; trace surrender or alternate credit. '
                           ELSE 'No AI binding or SmartAI rows found; prioritize gameplay verification. ' END,
                      'Verify death is required and inspect effective spawn flags.') AS detail
        FROM candidates ORDER BY has_ai, entry, quest_id LIMIT 300
    """),
    ("creature", "Blood Theory lizard inherits unkillable flag", """
        SELECT t.entry AS entity_id, t.name, c.guid AS spawn_guid, t.StaticFlags AS static_flags,
               'Blood Theory requires the lizard to die after Bottle of Leeches. Verified repair: repairs/35412-unkillable.sql' AS detail
        FROM creature_template t JOIN creature c ON c.id=t.entry
        WHERE t.entry=35412 AND c.guid=366585 AND (t.StaticFlags & 8) <> 0
          AND NOT EXISTS (SELECT 1 FROM creature_static_flags_override WHERE SpawnId=c.guid)
    """),
    ("creature", "Creature template has unkillable flag", """
        SELECT t.entry AS entity_id, t.name, t.StaticFlags AS static_flags,
               EXISTS (SELECT 1 FROM creature c WHERE c.id=t.entry) AS has_spawn,
               COUNT(*) OVER () AS total_matches,
               'Template sets UNKILLABLE (0x8). Check spawn overrides and intended encounter behavior.' AS detail
        FROM creature_template t WHERE (t.StaticFlags & 8) <> 0
        ORDER BY has_spawn DESC, t.entry LIMIT 300
    """),
    ("creature", "Spawn override has unkillable flag", """
        SELECT c.id AS entity_id, c.guid AS spawn_guid, o.DifficultyId AS difficulty_id,
               o.StaticFlags1 AS static_flags, COUNT(*) OVER () AS total_matches,
               'Spawn difficulty override sets UNKILLABLE (0x8). Verify its intended behavior.' AS detail
        FROM creature_static_flags_override o JOIN creature c ON c.guid=o.SpawnId
        WHERE (o.StaticFlags1 & 8) <> 0 ORDER BY c.guid, o.DifficultyId LIMIT 300
    """),
    ("creature", "Creature SmartAI configures invincibility", """
        SELECT COALESCE(c.id,t.entry) AS entity_id, c.guid AS spawn_guid,
               s.entryorguid, s.id AS action_id, s.event_type, s.action_param1 AS health_floor,
               s.target_type, s.comment, COUNT(*) OVER () AS total_matches,
               'SmartAI action 42 sets an invincibility health floor. Verify its target and activation/reset path.' AS detail
        FROM smart_scripts s
        LEFT JOIN creature c ON s.entryorguid<0 AND c.guid=-s.entryorguid
        LEFT JOIN creature_template t ON s.entryorguid>0 AND t.entry=s.entryorguid
        WHERE s.source_type=0 AND s.action_type=42 AND s.action_param1<>0
        ORDER BY s.entryorguid,s.id LIMIT 300
    """),
    ("loot", "Advertised quest item missing from creature loot paths", """
        WITH RECURSIVE advertised AS (
            SELECT DISTINCT c.entry, c.name, c.lootid, qi.ItemId
            FROM creature_questitem qi JOIN creature_template c ON c.entry=qi.CreatureEntry
        ), reachable (loot_id, item_id, ref_id) AS (
            SELECT DISTINCT a.lootid, a.ItemId, l.Reference
            FROM advertised a JOIN creature_loot_template l ON l.Entry=a.lootid
            WHERE l.Reference>0
            UNION DISTINCT
            SELECT r.loot_id, r.item_id, l.Reference
            FROM reachable r JOIN reference_loot_template l ON l.Entry=r.ref_id
            WHERE l.Reference>0
        )
        SELECT a.entry AS entity_id, a.name, a.lootid AS loot_id, a.ItemId AS item_id,
               COUNT(*) OVER () AS total_matches,
               CONCAT('Quest marker lists item ', a.ItemId, ' but base loot ', a.lootid,
                      ' has no matching direct or referenced row. Check scripted rewards and difficulty variants.') AS detail
        FROM advertised a
        WHERE NOT EXISTS (
            SELECT 1 FROM creature_loot_template l WHERE l.Entry=a.lootid
              AND l.Item=a.ItemId AND l.Reference=0 AND l.IsCurrency=0
        )
        AND NOT EXISTS (
            SELECT 1 FROM reachable r JOIN reference_loot_template l ON l.Entry=r.ref_id
            WHERE r.loot_id=a.lootid AND r.item_id=a.ItemId
              AND l.Item=a.ItemId AND l.Reference=0 AND l.IsCurrency=0
        )
        ORDER BY a.entry, a.ItemId LIMIT 300
    """),
    ("loot", "Quest objective item in ungated direct creature loot", """
        WITH objectives AS (
            SELECT ID AS quest_id, RequiredItemId1 AS item FROM quest_template WHERE RequiredItemCount1 > 0
            UNION SELECT ID, RequiredItemId2 FROM quest_template WHERE RequiredItemCount2 > 0
            UNION SELECT ID, RequiredItemId3 FROM quest_template WHERE RequiredItemCount3 > 0
            UNION SELECT ID, RequiredItemId4 FROM quest_template WHERE RequiredItemCount4 > 0
            UNION SELECT ID, RequiredItemId5 FROM quest_template WHERE RequiredItemCount5 > 0
            UNION SELECT ID, RequiredItemId6 FROM quest_template WHERE RequiredItemCount6 > 0
        ), quest_items AS (
            SELECT item, GROUP_CONCAT(quest_id ORDER BY quest_id) AS quests
            FROM objectives WHERE item > 0 GROUP BY item
        )
        SELECT c.entry AS entity_id, c.name, l.Entry AS loot_id, l.Item AS item_id,
               q.quests AS quest_ids, l.Chance AS chance, l.LootMode AS loot_mode,
               CONCAT('Item ', l.Item, ' in loot ', l.Entry, ' is required by quests ',
                      q.quests, ' and has QuestRequired=0. Verify item restrictions and intended pre-collection.') AS detail
        FROM quest_items q
        JOIN creature_loot_template l ON l.Item=q.item
        JOIN creature_template c ON c.lootid=l.Entry
        WHERE l.Reference=0 AND l.IsCurrency=0 AND l.QuestRequired=0
          AND l.LootMode<>0 AND (l.Chance>0 OR l.GroupId>0)
          AND NOT EXISTS (
              SELECT 1 FROM conditions co
              WHERE co.SourceTypeOrReferenceId=1 AND co.SourceGroup=l.Entry AND co.SourceEntry=l.Item
          )
        ORDER BY c.entry, l.Item
        LIMIT 300
    """),
    ("loot", "Creature loot references missing creature", """SELECT t.entry AS entity_id, CONCAT('Creature ',t.entry,' loot ID ',t.lootid,' has no creature_loot_template') AS detail FROM creature_template t LEFT JOIN creature_loot_template l ON l.Entry=t.lootid WHERE t.lootid>0 AND l.Entry IS NULL LIMIT 300"""),
    ("loot", "Gameobject loot references missing gameobject", """SELECT t.entry AS entity_id, CONCAT('Gameobject loot ID ',t.Data1,' has no gameobject_loot_template') AS detail FROM gameobject_template t LEFT JOIN gameobject_loot_template l ON l.Entry=t.Data1 WHERE t.type=3 AND t.Data1>0 AND l.Entry IS NULL LIMIT 300"""),
    ("vendor", "Vendor references missing creature", """SELECT v.entry AS entity_id, CONCAT('npc_vendor references missing creature_template ',v.entry) AS detail FROM npc_vendor v LEFT JOIN creature_template t ON t.entry=v.entry WHERE t.entry IS NULL GROUP BY v.entry LIMIT 300"""),
    ("gossip", "Gossip menu references missing text", """SELECT g.MenuID AS entity_id, CONCAT('gossip_menu references missing npc_text ',g.TextID) AS detail FROM gossip_menu g LEFT JOIN npc_text n ON n.ID=g.TextID WHERE g.TextID<>0 AND n.ID IS NULL LIMIT 300"""),
    ("movement", "Spawn with negative wander distance", """SELECT guid AS entity_id, CONCAT('creature ',id,' wander_distance=',wander_distance) AS detail FROM creature WHERE wander_distance<0 LIMIT 300"""),
    ("scripts", "SmartAI source references missing creature template", """SELECT s.entryorguid AS entity_id, CONCAT('smart_scripts source_type=0 references missing creature_template') AS detail FROM smart_scripts s LEFT JOIN creature_template t ON t.entry=s.entryorguid WHERE s.source_type=0 AND s.entryorguid>0 AND t.entry IS NULL GROUP BY s.entryorguid LIMIT 300"""),
]
RULES = BASE_RULES + EXTRA_RULES

SEVERITY = {
    "NPC offers different quests with same title": "review",
    "Gameobject offers different quests with same title": "review",
    "Repeated gossip menu text entries": "review",
    "Repeated gossip menu option actions": "review",
    "Nonpositive creature health modifier": "review",
    "Quest reward-next references itself": "review",
    "Creature templates with negative scale": "warning",
    "Quest giver spawn overrides disable interaction": "warning",
    "Quest giver without permanent spawn": "review",
    "Quest-linked NPC template missing questgiver flag": "review",
    "Quest giver spawn has interaction-restricting unit flags": "review",
    "Quest giver spawn uses explicit phase": "review",
    "Quest objective item in ungated direct creature loot": "review",
    "Quest objective target template sets unkillable": "review",
    "Creature template has unkillable flag": "review",
    "Spawn override has unkillable flag": "review",
    "Creature SmartAI configures invincibility": "review",
    "Advertised quest item missing from creature loot paths": "review",
    "Invalid creature level range": "warning",
    "Spawn with negative wander distance": "warning",
}

@app.get("/api/creature-survival/{spawn_guid}")
def creature_survival(spawn_guid: int):
    try:
        with connect() as db, db.cursor() as cur:
            cur.execute("""
                SELECT c.guid, c.id AS entry, c.map, c.curhealth, c.ScriptName AS spawn_script,
                       t.name, t.AIName, t.ScriptName AS template_script, t.StaticFlags,
                       (t.StaticFlags & 8) <> 0 AS template_unkillable, sp.HealthLimitPct
                FROM creature c JOIN creature_template t ON t.entry=c.id
                LEFT JOIN creature_sparring_template sp ON sp.CreatureID=t.entry
                WHERE c.guid=%s
            """, (spawn_guid,))
            spawn = cur.fetchone()
            if not spawn:
                raise HTTPException(404, "Spawn not found")
            cur.execute("""
                SELECT DifficultyId, StaticFlags1, (StaticFlags1 & 8) <> 0 AS unkillable
                FROM creature_static_flags_override WHERE SpawnId=%s ORDER BY DifficultyId
            """, (spawn_guid,))
            overrides = cur.fetchall()
            effective_flags = [{
                "difficulty_id": row["DifficultyId"],
                "static_flags": row["StaticFlags1"] if row["StaticFlags1"] is not None else spawn["StaticFlags"],
                "source": "override" if row["StaticFlags1"] is not None else "template",
            } for row in overrides]
            for row in effective_flags:
                row["unkillable"] = bool(row["static_flags"] & 8)
            cur.execute("""
                SELECT entryorguid, id, link, event_type, action_param1 AS health_floor, target_type, comment
                FROM smart_scripts WHERE source_type=0 AND entryorguid IN (%s,%s) AND action_type=42
                ORDER BY entryorguid,id
            """, (spawn["entry"], -spawn_guid))
            return {"spawn": spawn, "static_overrides": overrides,
                    "effective_flags_for_overrides": effective_flags,
                    "smartai_health_floors": cur.fetchall(), "read_only": True,
                    "note": "Sparring only limits damage from NPCs without player ownership. Runtime scripts and auras can also prevent death."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Creature survival inspection failed: {exc}") from exc


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
                        total_matches = rows[0].get("total_matches") if rows else None
                        result = {"module": module, "rule": name, "severity": SEVERITY.get(name, "warning"), "findings": rows, "count": len(rows), "capped": len(rows) == 300}
                        if total_matches is not None:
                            result.update(total_matches=total_matches, capped=total_matches > len(rows))
                        results.append(result)
                    except Exception as exc:
                        results.append({"module": module, "rule": name, "severity": "error", "error": str(exc), "findings": [], "count": 0})
    except Exception:
        raise HTTPException(503, "Database unavailable")
    # Suppress same-title findings only when a real multi-step quest chain connects both IDs.
    # Do not infer links from numeric proximity, titles or NPC association.
    try:
        with connect() as db:
            with db.cursor() as cur:
                graph = build_graph(cur)
        for rule in results:
            if rule["rule"] not in ("NPC offers different quests with same title",
                                    "Gameobject offers different quests with same title") or rule.get("error"):
                continue
            retained = []
            for finding in rule["findings"]:
                match = re.search(r"potentially overlapping quests (\d+) and (\d+)", str(finding.get("detail", "")))
                if match and chain_path(graph, int(match.group(1)), int(match.group(2))) is not None:
                    continue
                retained.append(finding)
            rule["findings"] = retained
            rule["count"] = len(retained)
    except Exception as exc:
        # Fail visibly rather than presenting a silently incomplete chain-filtered scan.
        results.append({"module":"quest","rule":"Quest chain relationship filtering",
                        "severity":"error","error":str(exc),"findings":[],"count":0})
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
    # Exclude NYI quests from reported findings, while retaining database records.
    # Resolve title from the source table even if optional name enrichment failed.
    try:
        with connect() as db:
            with db.cursor() as cur:
                cur.execute("SHOW COLUMNS FROM quest_template")
                fields = {r["Field"] for r in cur.fetchall()}
                title_col = "LogTitle" if "LogTitle" in fields else ("Title" if "Title" in fields else None)
                if title_col:
                    cur.execute("SELECT ID FROM quest_template WHERE `" + title_col + "` LIKE %s", ("<NYI>%",))
                    excluded_ids = {str(r["ID"]) for r in cur.fetchall()}
                    for rule in results:
                        if rule["module"] != "quest" or rule.get("error"):
                            continue
                        rule["findings"] = [finding for finding in rule["findings"]
                                            if str(finding.get("entity_id")) not in excluded_ids]
                        rule["count"] = len(rule["findings"])
    except Exception:
        # Never silently exclude records when the exclusion lookup fails.
        pass
    report = {"scanned_at": datetime.now(timezone.utc).isoformat(), "rules": results, "total_findings": sum(r["count"] for r in results), "error_rules": sum(bool(r.get("error")) for r in results), "capped_rules": sum(bool(r.get("capped")) for r in results), "read_only": True}
    from training_audit import scan_checks as inspect_training
    report["training_quest_checks"] = inspect_training()
    report["publication"] = queue_report("world", report)
    return report

@app.get("/api/quest/{quest_id}")
def inspect_quest(quest_id: int):
    if quest_id <= 0 or quest_id > 100000000:
        raise HTTPException(400, "Invalid quest ID")
    try:
        with connect() as db:
            with db.cursor() as cur:
                cur.execute("SELECT * FROM quest_template WHERE ID=%s", (quest_id,))
                quest = cur.fetchone()
                if not quest:
                    raise HTTPException(404, "Quest not found")
                def related(sql):
                    try:
                        cur.execute(sql, (quest_id,))
                        return cur.fetchall()
                    except pymysql.MySQLError as exc:
                        return {"error": str(exc)}
                addon = related("SELECT * FROM quest_template_addon WHERE ID=%s")
                starters = related("SELECT s.id AS npc_id, t.name AS npc_name FROM creature_queststarter s LEFT JOIN creature_template t ON t.entry=s.id WHERE s.quest=%s")
                enders = related("SELECT s.id AS npc_id, t.name AS npc_name FROM creature_questender s LEFT JOIN creature_template t ON t.entry=s.id WHERE s.quest=%s")
                object_starters = related("SELECT s.id AS object_id, t.name AS object_name FROM gameobject_queststarter s LEFT JOIN gameobject_template t ON t.entry=s.id WHERE s.quest=%s")
                object_enders = related("SELECT s.id AS object_id, t.name AS object_name FROM gameobject_questender s LEFT JOIN gameobject_template t ON t.entry=s.id WHERE s.quest=%s")
                return {"quest": quest, "addon": addon, "npc_starters": starters, "npc_enders": enders, "object_starters": object_starters, "object_enders": object_enders, "read_only": True}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Quest inspection unavailable")

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
</style></head><body><h1>TrinityCore World Auditor</h1><p>Live MariaDB integrity scanner · Read-only · v0.02.035</p><button onclick="run()" id="run">Run world scan</button> <button onclick="updateApp()" id="update">Update auditor</button> <button onclick="downloadReport()" id="download" disabled>Download JSON report</button><select id="filter" onchange="renderReport()"><option value="all">All findings</option><option value="nonzero">Findings only</option><option value="error">SQL errors</option><option value="review">Needs review</option></select><span id="status"></span><div class="panel"><strong>Linked database explorer</strong><p>Inspect quests, NPCs, gameobjects, items and their related loot records.</p><select id="entityKind"><option value="quest">Quest</option><option value="npc">NPC</option><option value="gameobject">Gameobject</option><option value="item">Item</option></select> <input id="entityId" type="number" min="1" placeholder="Entity ID" style="padding:10px;border-radius:6px"> <button onclick="inspectEntity()">Explore</button><div id="entityResult"></div></div><div class="panel"><strong>NLI quest review</strong><p>Observed pre-Cataclysm conflicts and unverified replacement candidates. Read-only; no SQL is executed.</p><button onclick="reviewNli()">Review NLI quests</button> <a href="/api/nli/preview.sql" download="nli-repair-preview.sql" style="color:#a6d9ff;margin-left:12px">Download SQL preview</a><div id="nliResult"></div></div><div class="panel"><strong>Quest inspector</strong><p><input id="questId" type="number" min="1" placeholder="Quest ID (e.g. 9679)" style="padding:10px;border-radius:6px"> <button onclick="inspectQuest()">Inspect quest</button></p><div id="questResult"></div></div><div class="panel"><strong>C++ source audit</strong><p>Compare mounted local source with database script bindings and known encounter fixes. Read-only; review findings before proposing repairs.</p><button id="sourceRun" onclick="scanSource()">Scan C++ source</button> <button id="sourceDownload" onclick="downloadSource()" disabled>Download source report</button><div id="sourceResult"></div></div><div class="panel"><strong>GitHub report publication</strong><p>Latest reports are automatically queued after scans. The host worker publishes them every minute.</p><button onclick="publicationStatus()">Check publication status</button><pre id="publicationStatus" style="white-space:pre-wrap"></pre></div><div class="panel"><strong>Verified source repair previews</strong><p>Checks the mounted files against known fixes. Downloads patches for review; source is never modified by the Auditor.</p><button onclick="loadRepairs()">Check verified repairs</button><div id="repairPlans"></div></div><div id="results"></div><script>
async function loadRepairs(){const t=document.getElementById('repairPlans');t.textContent='Checking…';try{const r=await fetch('/api/source/repairs');if(!r.ok)throw Error('Repair check failed');const d=await r.json();t.innerHTML=d.repairs.map(p=>'<p><strong>'+safe(p.title)+'</strong> · '+safe(p.status)+(p.status==='ready_for_review'?' · <a style="color:#a6d9ff" href="/api/source/repairs/'+encodeURIComponent(p.id)+'.patch">Download patch</a>':'')+(p.reason?' · '+safe(p.reason):'')+'</p>').join('')}catch(e){t.textContent=e.message}}

async function publicationStatus(){const t=document.getElementById("publicationStatus");try{const r=await fetch("/api/reports/status");if(!r.ok)throw Error("Status unavailable");t.textContent=JSON.stringify(await r.json(),null,2)}catch(e){t.textContent=e.message}}

let sourceReport=null;
function downloadSource(){if(!sourceReport)return;const u=URL.createObjectURL(new Blob([JSON.stringify(sourceReport,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='source-audit.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
async function scanSource(){const b=document.getElementById('sourceRun'),t=document.getElementById('sourceResult');b.disabled=true;t.textContent='Scanning local source and database bindings…';try{const r=await fetch('/api/source/scan');const d=await r.json();if(!r.ok)throw Error(d.detail||'Source scan failed');sourceReport=d;document.getElementById('sourceDownload').disabled=false;t.innerHTML='<p>'+safe(d.files_scanned)+' files · '+safe(d.bindings_checked)+' bindings · '+safe(d.total_findings)+' findings · '+safe(d.publication?.message||'')+' · '+(d.complete?'Scan completed':'INCOMPLETE — review errors')+'</p>'+(d.capped?'<p>Only first 300 findings displayed; total shown above.</p>':'')+'<pre style="white-space:pre-wrap">'+safe(d.errors.join('\\n'))+'</pre>'+d.findings.map(f=>'<details><summary>'+safe(f.severity)+' · '+safe(f.rule)+'</summary><p>'+safe(f.detail)+'</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+safe(JSON.stringify(f.evidence,null,2))+'</pre></details>').join('')+'<p>'+d.limitations.map(safe).join('<br>')+'</p>'}catch(e){t.textContent=e.message}finally{b.disabled=false}}

async function reviewNli(){const target=document.getElementById('nliResult');target.textContent='Loading…';try{const r=await fetch('/api/nli/review');const d=await r.json();if(!r.ok)throw Error(d.detail||'Review failed');target.textContent='Observed conflict pairs: '+d.verified_pairs.length+'; unverified candidates: '+d.unverified_candidates.length+'; details: '+JSON.stringify(d.verified_pairs,null,2)}catch(e){target.textContent=e.message}}
async function updateApp(){if(!confirm('Update auditor now?'))return;let b=document.getElementById('update');b.disabled=true;try{let r=await fetch('/api/request-update',{method:'POST'});let d=await r.json();if(!r.ok)throw Error(d.detail||'Update request failed');alert(d.message)}catch(e){alert(e.message)}finally{b.disabled=false}}
async function inspectEntity(kind,id){kind=kind||document.getElementById('entityKind').value;id=id||document.getElementById('entityId').value;const target=document.getElementById('entityResult');if(!/^[0-9]+$/.test(String(id))||Number(id)<1){target.textContent='Enter a positive ID';return}target.textContent='Loading…';try{const response=await fetch('/api/explore/'+encodeURIComponent(kind)+'/'+encodeURIComponent(id));const data=await response.json();if(!response.ok)throw Error(data.detail||'Inspection failed');const links=data.links.map(l=>'<button style="margin:3px" onclick="inspectEntity('+"&#39;"+safe(l.kind)+"&#39;"+','+Number(l.id)+')">'+safe(l.kind)+' #'+safe(l.id)+' '+safe(l.name||'')+'</button>').join('');const sections=Object.entries(data.sections).map(([title,part])=>'<details><summary>'+safe(title)+' ('+safe(part.rows?.length||0)+')</summary>'+(part.error?'<p>'+safe(part.error)+'</p>':'')+(part.capped?'<p>First 100 rows only</p>':'')+'<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+safe(JSON.stringify(part.rows,null,2))+'</pre></details>').join('');target.innerHTML='<h3>'+safe(data.name||kind)+' (#'+safe(id)+')</h3><details><summary>Primary record</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+safe(JSON.stringify(data.record,null,2))+'</pre></details><h4>Related records</h4>'+links+sections}catch(e){target.textContent=e.message}}
async function inspectQuest(){const id=document.getElementById('questId').value;const target=document.getElementById('questResult');if(!/^\\d+$/.test(id)||Number(id)<1){target.textContent='Enter a positive quest ID';return}target.textContent='Loading quest…';try{const r=await fetch('/api/quest/'+encodeURIComponent(id));const data=await r.json();if(!r.ok)throw Error(data.detail||'Inspection failed');target.innerHTML='<p><strong>'+safe(data.quest.LogTitle||data.quest.Title||'Quest')+' (#'+safe(id)+')</strong></p>'+Object.entries(data).filter(([k])=>k!=='read_only').map(([k,v])=>'<details><summary>'+safe(k)+'</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+safe(JSON.stringify(v,null,2))+'</pre></details>').join('')}catch(e){target.textContent=e.message}}
let lastReport=null;const el=document.getElementById('results');
function downloadReport(){if(!lastReport)return;const blob=new Blob([JSON.stringify(lastReport,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='world-audit-'+lastReport.scanned_at.replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function renderReport(){if(!lastReport)return;const filter=document.getElementById('filter').value;const rules=lastReport.rules.filter(r=>filter==='all'||(filter==='nonzero'&&r.count>0)||(filter==='error'&&r.error)||(filter==='review'&&r.severity==='review'));el.innerHTML=rules.map(r=>'<div class="panel"><details><summary>'+safe(r.module)+' · '+safe(r.rule)+' ['+safe(r.severity)+'] ('+r.count+')</summary>'+(r.error?'<p>Error: '+safe(r.error)+'</p>':'')+(r.capped?'<p>Showing first 300 findings; additional records may exist.</p>':'')+'<table><tr><th>ID</th><th>Name</th><th>Finding</th></tr>'+r.findings.map(f=>'<tr><td><code>'+safe(f.entity_id)+'</code></td><td>'+safe(f.name||'—')+'</td><td>'+safe(f.detail)+'</td></tr>').join('')+'</table></details></div>').join('')||'<p>No rules match this filter.</p>'}
function safe(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function run(){document.getElementById('run').disabled=true;document.getElementById('status').textContent='Scanning…';try{let response=await fetch('/api/scan');let data=await response.json();if(!response.ok)throw Error(data.detail||'Scan failed');lastReport=data;document.getElementById('download').disabled=false;document.getElementById('status').textContent=(data.publication?.message||'')+' · '+data.total_findings+' findings · '+data.rules.length+' rules · '+data.error_rules+' SQL errors'+(data.capped_rules?' · '+data.capped_rules+' capped':'');renderReport()}catch(e){document.getElementById('status').textContent='Error: '+e.message}finally{document.getElementById('run').disabled=false}}
fetch('/api/health').then(r=>r.json()).then(d=>document.getElementById('status').textContent=d.connected?'Connected · '+d.quest_count+' quests':'Disconnected').catch(()=>document.getElementById('status').textContent='Database unavailable');</script></body></html>"""



