UPDATE creature_template SET StaticFlags = 524296
WHERE entry = 35412 AND StaticFlags = 524288;
SELECT ROW_COUNT() AS templates_restored;
