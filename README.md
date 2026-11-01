# TrinityCore World Auditor

Read-only Docker web dashboard for TrinityCore Cataclysm 4.3.4 MariaDB integrity checks. This is the initial scanner, not an automatic repair tool. Planned modules include quests, NPCs, combat, creatures stuck at 1 HP, spawns, spells, loot, movement, scripts and instances.

## Docker VM setup

```bash
git clone https://github.com/bl1tzkr13g/TrinityCoreWorldAuditor.git
cd TrinityCoreWorldAuditor
cp .env.example .env
nano .env
chmod 600 .env
sudo docker compose up -d --build
```

Set `DB_PASSWORD` to the existing `world_auditor` MariaDB user's password. Open `http://10.0.0.118:3050` on your trusted LAN. Never commit `.env`. The application has no authentication, so do not expose port 3050 to the internet. The MariaDB account should have SELECT/SHOW VIEW permissions only. Results are capped at 300 rows per rule; a capped rule does not indicate an exact total. Scans are not yet validated against the live server.

Update: `git pull && sudo docker compose up -d --build`.

## Version numbering

Use the release format `v0.02.001`, `v0.02.002`, `v0.02.003`, etc. Increment the final three-digit segment for each subsequent application change, and update both the FastAPI application version and the version shown on the dashboard in the same change. The first version under this convention is `v0.02.001`.

## Linked record explorer (v0.02.008)

Use the dashboard's **Linked database explorer**, or the read-only endpoints:

- `GET /api/explore/quest/9679`
- `GET /api/explore/npc/21587`
- `GET /api/explore/gameobject/12345`
- `GET /api/explore/item/12345`

The explorer displays a primary record, related table rows, and navigable links to known NPCs, quests, gameobjects, and items. It discovers some schema differences at runtime and reports missing tables/columns as section errors instead of silently treating them as clean results. Sections are capped at 100 rows; linked entities are bounded. Loot table Entry IDs are not assumed to equal creature/gameobject entry IDs. Item objectives are extracted from available quest template columns. Data relationships are not proof of in-game behavior. Inspectors are read-only; do not grant the database user write permissions.

### Scope and current limitations

The explorer is a first integrated foundation, not a complete gameplay validator. It does not yet resolve every spell, condition, scripted event, reference loot group, quest objective schema variation, or detect live stuck-at-1-HP combat behavior. The new explorer has not yet been tested against the deployed Cataclysm database. Review section errors and truncated result warnings before treating missing relationships as meaningful.
