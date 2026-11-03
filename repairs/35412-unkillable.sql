-- Rejuvenated Thunder Lizard must die for Blood Theory.
-- Clear only bit 0x8 on the verified template value.
UPDATE creature_template
SET StaticFlags = StaticFlags & ~8
WHERE entry = 35412 AND StaticFlags = 524296
  AND EXISTS (SELECT 1 FROM creature WHERE guid = 366585 AND id = 35412)
  AND NOT EXISTS (SELECT 1 FROM creature_static_flags_override WHERE SpawnId = 366585);
SELECT ROW_COUNT() AS templates_changed;
SELECT entry, name, StaticFlags FROM creature_template WHERE entry = 35412;
