# Homepage download statistics

`Update DuneCity Download Counts` runs hourly at minute 17 UTC and is also
manually dispatchable. `scripts/update-download-stats.py` reads every GitHub
release page and SourceForge's interval totals. No private analytics credentials
are needed; Actions uses GITHUB_TOKEN for GitHub rate limits. The public snapshot
is `website/data/download-stats.json`; `analytics/download-state.json` preserves
per-asset maxima and hourly (two days) / daily (400 days) baselines in Git.
The older downloads.json/history files are historical evidence, no longer read.

The homepage shows total, calendar year to date, 30 UTC calendar days including
today, and a roughly 24-hour snapshot difference. It also lists five calendar
years. Missing GitHub history is marked incomplete; partial counts say “At least”.
24-hour numbers begin only when a baseline exists between 24 and 22.5 hours ago.
SourceForge has reporting latency, so these are reported download increments,
not exact download-event timestamps. Counts include repeat/automated downloads
and ancillary files; they are not unique players or installs. Keep metadata files
in the definition to match SourceForge's all-files scope. Deleted GitHub assets
are retained once observed, but earlier deleted assets cannot be recovered.

SourceForge coarsens long timeline queries, so query each month/year range's
`total` directly; never slice the all-time timeline to estimate recent downloads.
GitHub old-asset lifetime counts must never be assigned wholesale to a recent
period. Release pagination and cumulative resets/deleted files are tested.

Failed upstream calls fail the run before replacing the last good publication.
The UI shows update delay after three hours and refetches every 15 minutes.
GitHub Actions provides workflow failure reporting. Its schedule can be delayed;
check the workflow if freshness is stale. After writing and pushing the snapshot,
the workflow explicitly dispatches Deploy to Droplet: GITHUB_TOKEN commits do
not automatically trigger another workflow. Deployment is serialized with other
website releases by the existing production concurrency group.

Recovery: inspect failed Actions steps, then dispatch update-download-counts.yml.
Do not overwrite missing source data with zero or clear the retained asset state.
No game release/version bump is needed for statistics-only changes.
