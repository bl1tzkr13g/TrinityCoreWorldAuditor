# Latest audit reports

Completed scans automatically queue these reports for the host worker to publish:

* `world-audit-latest.json`
* `source-audit-latest.json`

Only the latest file of each type is kept here. Previous versions remain in Git
history. Files do not exist until the first successful publication. Check
`scanned_at`, `auditor_version`, completeness/errors, caps and (source reports)
`source_digest` before interpreting findings. Reports may contain review candidates
and incomplete checks; publication does not mean the server is free of issues.
Connection diagnostics are removed from public copies. Reports are public.
