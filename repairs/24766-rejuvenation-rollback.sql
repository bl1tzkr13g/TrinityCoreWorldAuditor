-- Undo only the exact tagged action. Back up first and stop worldserver.
START TRANSACTION;
DELETE s FROM smart_scripts s JOIN creature_template c ON c.entry=s.entryorguid
WHERE s.entryorguid=47057 AND s.source_type=0 AND s.id=0 AND s.link=0
  AND s.event_type=8 AND s.event_param1=774 AND s.event_param2=0
  AND s.event_phase_mask=0 AND s.event_chance=100 AND s.event_flags=0
  AND s.action_type=33 AND s.action_param1=44175 AND s.target_type=7
  AND s.comment='Auditor 24766 v1: Rejuvenation hit - grant practice credit to caster'
  AND c.AIName='SmartAI' AND COALESCE(c.ScriptName,'')='';
SET @auditor_removed_24766 = ROW_COUNT();
UPDATE creature_template SET AIName=''
WHERE entry=47057 AND AIName='SmartAI' AND COALESCE(ScriptName,'')=''
  AND @auditor_removed_24766=1
  AND NOT EXISTS (SELECT 1 FROM smart_scripts WHERE entryorguid=47057 AND source_type=0);
COMMIT;
SELECT @auditor_removed_24766 AS removed_tagged_action;
