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


## Local C++ source audit (v0.02.021)

Mount the actual Cataclysm source at `/trinitycore-source` read-only. Compose now
binds `/mnt/trinitycore-source` on the Docker host to this path. In this deployment
that host path is the NFS mount from the Cataclysm VM. Verify NFS is mounted and the
source file is readable **before** recreating the container. The mount must be
available after host reboot. Missing source returns HTTP 503, not a clean report.
`TRINITY_SOURCE_ROOT` can override the in-container path.

Use **Scan C++ source** in the dashboard or `GET /api/source/scan`. Download the
separate JSON report for evidence, source digest, coverage and errors. This scanner
is separate from **Run world scan**. It performs only SELECT/SHOW database queries
and reads source files; it never executes source, compiles, writes files, repairs
records, or restarts worldserver.

Checks implemented:

* Compare creature, gameobject, spell, area trigger and instance script bindings
  against recognized legacy constructor registrations and common Register* macros.
* Identify registered scripts whose loader has no recognized direct call. Loader
  reachability, generated loaders and CMake build inclusion are not proven.
* Proving Pit regression checks based on working commit
  `24cf65fb40c9985d44e1a93de8b362056db11313`: jailor/scout binding expectations,
  quest eligibility, gossip handler, activation, combat, credit, and immunity
  initialization/release operations in the recognized classes.
* Identify DamageTaken handlers cancelling damage or clamping health to one for
  encounter review. The Helix/Lumbering Oaf incident demonstrates that these can
  be intentional phase mechanics. This rule NEVER recommends force-killing mobs.

All findings are review candidates, not confirmed gameplay bugs or automatic
repair instructions. Alternative implementations, helper functions, conditional
compilation and custom macros can change results. Registration does not prove the
script is in the running binary. The source digest identifies scanned contents,
not a transactional snapshot or installed build. Missing tables/columns and file
limits explicitly mark the report incomplete. At most 300 findings are returned;
total findings are computed before truncation. Scan limits: 12,000 files, 100 MiB
aggregate, 2 MiB per file, 20,000 bindings per table. Symlinks are skipped.

Earlier database-only fixes (Noggenfogger vendor gossip and Saltspray Gull
movement) remain evidence for future database/source correlation rules; they are
not used to infer missing C++ from ordinary vendor or movement configuration.
No broad immunity removal, guessed script assignment, or automatic C++ repair is
included. Proposed repairs require independently verified intended behavior.

Validation: `python -m unittest discover -s tests -v` from the repository root.
Tests include the actual Proving Pit source at the working commit, missing immunity
release, commented code, loader declarations, script macros, intentional health
clamps, unavailable mounts, skipped symlinks and incomplete inventory reporting.
Live database, NFS and gameplay validation must be performed on the deployment.

## Automatic GitHub reports (v0.02.022)

Each completed world/source scan atomically queues its latest sanitized report in
`/update-requests/reports`. The existing once-per-minute host cron worker publishes
`reports/world-audit-latest.json` and `reports/source-audit-latest.json` to `main`.
A failed scan request does not replace the previous report; completed scans with
rule errors are published with their incompleteness indicators. File contents are
replaced, while Git retains history. Reports include scan time and app version.
Source reports retain the source digest. Public copies omit connection settings,
raw diagnostics and common credential patterns; local downloads retain diagnostics.
This is targeted redaction, not a guarantee against arbitrary sensitive free text.

The dashboard distinguishes **queued** from **published**. Use **Check publication
status** (or `GET /api/reports/status`) for the last host check, commit and hashes.
A newer scan may be waiting behind that status. Pending files stay on disk for
retry; repeated failures do not silently discard reports. Per-type latest reports
replace older pending reports rather than accumulating an unbounded queue.

Deployment requirements:

* Existing host cron: `* * * * * root /bin/bash /home/docker_user/TrinityCoreWorldAuditor/host-update.sh`.
* Host Python 3, Git and flock; container retains the existing update-requests bind.
* `docker_user` needs noninteractive HTTPS **write** authentication to this repo.
  The worker inherits that user's Git credential helper. Read-only clone access is
  insufficient. Never put GitHub tokens in Compose, the dashboard or committed files.
* Check `update-requests/last-publication.log` and
  `update-requests/reports/publication-status.json` on failure.

The worker uses a separate temporary checkout and commits only the two allowed
report paths, with non-force pushes and retries for concurrent branch changes.
It never stages, resets or stashes deployment changes. Git authentication and
network failures preserve queued reports for the next cron invocation. The updater
now stops deployment after a failed pull and runs publication even without an app
update request. Worker invocations are serialized with flock.


## Investigation improvements (v0.02.023)

The first live report incorrectly classified dungeon wrapper registrations as
missing. Confirmed examples: `boss_baron_silverlaine` uses
`RegisterShadowfangKeepCreatureAI`, and `boss_houndmaster_loksey` uses
`RegisterScarletMonasteryCreatureAI`. Their headers forward the AI name to
`RegisterCreatureAIWithFactory`; the core macro stringifies that name. The scanner
now resolves simple forwarding macro definitions across scanned headers before
examining registration calls. Unknown wrappers remain review findings; their
names are not guessed. Explicit spell aliases and paired registrations are also
recognized. Actual source fixtures cover both confirmed false positives.

Source reports now retain **all** computed findings plus per-rule counts instead
of cutting off after 300. Existing input limits still apply; the publisher's
20 MiB safety limit may reject an unusually large report (queue retained). The
world scanner's separate SQL limits have not changed. `report_complete` describes
source input/database coverage; it is not a gameplay correctness guarantee.

A read-only Vargoth case packet (`incident_evidence`) captures template 19481,
spawn 98174, starter/ender links and template/spawn SmartAI. Each section reports
its own errors and caps; check these separately from source coverage. Source
inspection confirms `ObjectMgr::ChooseCreatureFlags` uses an explicit spawn
`npcflag` instead of the template value; NULL inherits the template. Thus override
1 excludes questgiver bit 2 even with template 3. This establishes flag behavior,
not whether that particular Dalaran spawn should offer quests. No server SQL or
C++ gameplay changes are applied by this release.
