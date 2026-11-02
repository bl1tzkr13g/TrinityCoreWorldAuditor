-- Manual repair for The Arts of a Druid. Back up creature_template and
-- smart_scripts first. Use the MariaDB CLI with world selected; do not use --force.
-- Run while worldserver is stopped. No quest objectives or existing scripts deleted.
DELIMITER //
CREATE PROCEDURE auditor_fix_24766_rejuvenation()
BEGIN
    DECLARE current_ai VARCHAR(64);
    DECLARE current_script VARCHAR(128);
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    START TRANSACTION;
    IF (SELECT COUNT(*) FROM creature_template WHERE entry=47057) <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Expected watcher template 47057 missing';
    END IF;
    SELECT AIName, ScriptName INTO current_ai, current_script
      FROM creature_template WHERE entry=47057 FOR UPDATE;
    IF COALESCE(current_ai,'') <> '' OR COALESCE(current_script,'') <> '' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Watcher already has an AI or script; review before repair';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM quest_template WHERE ID=24766
        AND RequiredNpcOrGo1=44175 AND RequiredNpcOrGoCount1=1 AND RequiredSpell=774) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Quest 24766 differs from verified objective contract';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM creature_template WHERE entry=44175) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Credit template 44175 missing; investigate first';
    END IF;
    IF EXISTS (SELECT 1 FROM smart_scripts WHERE source_type=0 AND entryorguid=47057)
      OR EXISTS (SELECT 1 FROM smart_scripts s JOIN creature c ON s.entryorguid=-c.guid
                 WHERE s.source_type=0 AND c.id=47057)
      OR EXISTS (SELECT 1 FROM creature WHERE id=47057 AND COALESCE(ScriptName,'') <> '') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Existing watcher behavior or spawn override detected; review first';
    END IF;
    UPDATE creature_template SET AIName='SmartAI' WHERE entry=47057;
    INSERT INTO smart_scripts
      (entryorguid,source_type,id,link,event_type,event_phase_mask,event_chance,event_flags,
       event_param1,event_param2,event_param3,event_param4,
       action_type,action_param1,action_param2,action_param3,action_param4,action_param5,action_param6,
       target_type,target_param1,target_param2,target_param3,target_x,target_y,target_z,target_o,comment)
    VALUES
      (47057,0,0,0,8,0,100,0,774,0,0,0,33,44175,0,0,0,0,0,7,0,0,0,0,0,0,0,
       'Auditor 24766 v1: Rejuvenation hit - grant practice credit to caster');
    COMMIT;
    SELECT 'Applied: Rejuvenation on 47057 grants credit 44175 to caster' AS result;
END//
DELIMITER ;
CALL auditor_fix_24766_rejuvenation();
DROP PROCEDURE auditor_fix_24766_rejuvenation;
